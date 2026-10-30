#!/usr/bin/env python3
"""Decide, without a model, whether a bot PR needs an agent, and keep the books.

Every five minutes cron asks `gate`. It reads GitHub (gh only, no tokens spent)
and answers yes when one of the bot's open pull requests has

  * a review asking for changes that nobody has answered yet, or
  * a failing CI check on its current head (skipped when the profile sets
    `prFeedback.ci` to false, as brave-core's does),

and the cache in ~/.brave-dev-loop says the PR is not already being worked on,
not in a cool-down, and not stuck in a loop. Only then does scripts/pr-feedback.sh
create a worktree, start the agent, re-request review and remove the worktree.

Loop protection. Every reason to wake up is a *component*:

    cr:<reviewer>:<review id>        one reviewer's standing request for changes
    ci:<head sha>:<failing checks>   red CI on one particular head

A component is dropped from consideration once an attempt that included it
pushed a commit (it was answered; the next move is the reviewer's, and a
re-review is requested), or once it has had `maxAttemptsPerComponent` attempts
that pushed nothing. A new review or a new head makes a new component, so a
reviewer who asks again is answered, and a PR that goes red again on every fix is
still bounded by the per-day and per-week caps and by a cool-down that doubles
with every attempt that achieved nothing. `reset` clears a PR by hand.

State is one JSON file per PR under
~/.brave-dev-loop/pr-feedback/<owner>__<repo>/<number>.json
($BRAVE_DEV_LOOP_HOME overrides the directory).

Commands (all print JSON on stdout, diagnostics on stderr):
  gate [-v]                 exit 0 if some PR is eligible, 1 if none, 2 on error
  next [-v]                 the first eligible PR as JSON (exit 1 if none)
  begin                     reads next's JSON on stdin; records the attempt
  worktree --pr N           create or reuse the PR's worktree (exit 3: busy)
  prompt --pr N --worktree P  the agent prompt, reads next's JSON on stdin
  finish --pr N --at T --agent-rc RC | --outcome busy|setup-failed
                            record the outcome; re-request review if it pushed
  cleanup --path P          remove a worktree this job created
  status                    what the cache holds
  reset --pr N              forget a PR's history
"""

import argparse
import json
import os
import shutil
import subprocess  # nosemgrep
import sys
import time

_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_dir)
from lib import org_gate, pr_worktree  # noqa: E402
from lib.load_config import (  # noqa: E402
    bot_dir,
    get_config,
    load_config,
    load_profile,
)

DEFAULTS = {
    "ci": True,
    "maxAttemptsPerComponent": 2,
    "maxAttemptsPerDay": 4,
    "maxAttemptsPerWeek": 10,
    "cooldownMinutes": 30,
    "maxCooldownMinutes": 360,
    "timeoutSeconds": 3600,
    # Checks a bot cannot fix by editing code: workflow_dispatch/infra-flaky ones
    # that workflow-pushed.md says need a human re-run.
    "ignoreChecks": ["Assign labels", "security"],
    "limit": 100,
}

WEEK = 7 * 24 * 3600
DAY = 24 * 3600
FAIL_CONCLUSIONS = {"FAILURE", "TIMED_OUT", "STARTUP_FAILURE"}
FAIL_STATES = {"FAILURE", "ERROR"}
PENDING_STATES = {"PENDING", "EXPECTED"}
# Outcomes that count against a component's attempts and slow the next try.
UNPRODUCTIVE = {"nochange", "failed"}
EXIT_BUSY = 3

PR_FIELDS = (
    "number,title,headRefName,headRefOid,isDraft,updatedAt,"
    "reviews,statusCheckRollup,author"
)


def say(message):
    print(message, file=sys.stderr)


def settings_for(config, profile):
    merged = dict(DEFAULTS)
    merged.update(profile.get("prFeedback") or {})
    merged.update(config.get("prFeedback") or {})
    return merged


# --- cache ---------------------------------------------------------------


def home_dir():
    return os.environ.get("BRAVE_DEV_LOOP_HOME") or os.path.join(
        os.path.expanduser("~"), ".brave-dev-loop"
    )


def cache_dir(repo):
    return os.path.join(home_dir(), "pr-feedback", repo.replace("/", "__"))


def state_path(repo, number):
    return os.path.join(cache_dir(repo), f"{number}.json")


def load_state(repo, number):
    try:
        with open(state_path(repo, number)) as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        state = {}
    state.setdefault("attempts", [])
    state.setdefault("notBefore", 0)
    return state


def save_state(repo, number, state, now):
    state["attempts"] = [a for a in state["attempts"] if a["at"] > now - WEEK]
    os.makedirs(cache_dir(repo), exist_ok=True)
    path = state_path(repo, number)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def forget_closed(repo, open_numbers, now):
    """Drop cache files of PRs that are no longer open, once they are old."""
    try:
        names = os.listdir(cache_dir(repo))
    except OSError:
        return
    for name in names:
        stem, _, ext = name.rpartition(".")
        if ext != "json" or not stem.isdigit() or int(stem) in open_numbers:
            continue
        path = os.path.join(cache_dir(repo), name)
        try:
            if os.path.getmtime(path) < now - 14 * DAY:
                os.remove(path)
        except OSError:
            pass


# --- GitHub (read only) --------------------------------------------------


def gh(*args, check=True):
    result = subprocess.run(  # nosemgrep
        ["gh", *args], capture_output=True, text=True, timeout=120
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"gh {args[0]} {args[1]}: {result.stderr.strip()}")
    return result.stdout


def list_bot_prs(repo, bot, limit):
    raw = gh(
        "pr", "list", "--repo", repo, "--author", bot, "--state", "open",
        "--limit", str(limit), "--json", PR_FIELDS,
    )  # fmt: skip
    return json.loads(raw)


def head_of(repo, number):
    return gh("pr", "view", str(number), "--repo", repo, "--json", "headRefOid",
              "--jq", ".headRefOid").strip()  # fmt: skip


# --- what a PR is waiting on ---------------------------------------------


def login_of(obj):
    return ((obj or {}).get("author") or {}).get("login") or ""


def change_components(pr, bot, trusted):
    """One component per trusted reviewer whose latest decisive review rejects."""
    latest = {}
    for review in sorted(
        pr.get("reviews") or [], key=lambda r: r.get("submittedAt") or ""
    ):
        login = login_of(review)
        if (
            review.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")
            and login
        ):
            latest[login.lower()] = review
    out = []
    for login, review in sorted(latest.items()):
        if review["state"] != "CHANGES_REQUESTED" or login == bot.lower():
            continue
        if login not in trusted:
            continue
        out.append(f"cr:{login_of(review)}:{review.get('id')}")
    return out


def check_name(check):
    return check.get("name") or check.get("context") or ""


def ci_state(pr, ignore):
    """(failing check names, whether any check is still running)."""
    ignored = {n.lower() for n in ignore}
    failing, pending = [], False
    for check in pr.get("statusCheckRollup") or []:
        if check_name(check).lower() in ignored:
            continue
        conclusion = (check.get("conclusion") or "").upper()
        state = (check.get("state") or "").upper()
        if check.get("__typename") == "StatusContext" or "state" in check:
            if state in FAIL_STATES:
                failing.append(check_name(check))
            elif state in PENDING_STATES:
                pending = True
        else:
            if (check.get("status") or "").upper() != "COMPLETED":
                pending = True
            elif conclusion in FAIL_CONCLUSIONS:
                failing.append(check_name(check))
    return sorted(set(failing)), pending


def effective_outcome(attempt, now, timeout):
    """A 'started' attempt that outlived the job's timeout never finished."""
    if attempt.get("outcome") == "started" and now - attempt["at"] > timeout + 600:
        return "failed"
    return attempt.get("outcome")


def evaluate(pr, state, now, settings, bot, trusted):
    """Is this PR eligible for an agent? Pure: takes everything it needs."""
    number = pr["number"]
    head = pr.get("headRefOid") or ""
    timeout = settings["timeoutSeconds"]
    verdict = {"number": number, "eligible": False, "why": "", "components": []}

    components = change_components(pr, bot, trusted)
    ci_pending = False
    if settings["ci"] and not pr.get("isDraft"):
        failing, ci_pending = ci_state(pr, settings["ignoreChecks"])
        if failing and not ci_pending:
            components.append(f"ci:{head}:{','.join(failing)}")
    if not components:
        verdict["why"] = (
            "CI still running"
            if ci_pending
            else "no request for changes, no failing CI"
        )
        return verdict

    attempts = state["attempts"]
    for a in attempts:
        a["_outcome"] = effective_outcome(a, now, timeout)
    if any(a["_outcome"] == "started" for a in attempts):
        verdict["why"] = "an attempt is already in progress"
        return verdict
    if state.get("notBefore", 0) > now:
        verdict["why"] = f"cooling down for {int(state['notBefore'] - now)}s more"
        return verdict

    remaining = []
    for component in components:
        prior = [a for a in attempts if component in a.get("components", [])]
        if any(a["_outcome"] == "pushed" for a in prior):
            continue  # answered; waiting on the reviewer or on CI for the new head
        if (
            sum(a["_outcome"] in UNPRODUCTIVE for a in prior)
            >= settings["maxAttemptsPerComponent"]
        ):
            continue  # gave up on this one; a new review or head makes a new component
        remaining.append(component)
    if not remaining:
        verdict["why"] = "already answered or given up on every open request"
        return verdict

    if sum(a["at"] > now - DAY for a in attempts) >= settings["maxAttemptsPerDay"]:
        verdict["why"] = "daily attempt limit reached"
        return verdict
    if len(attempts) >= settings["maxAttemptsPerWeek"]:
        verdict["why"] = "weekly attempt limit reached"
        return verdict

    # Back-off after attempts that achieved nothing: 30m, 60m, 120m ... up to the
    # cap. A push resets it, so a fresh review is answered at the next tick.
    last = max(attempts, key=lambda a: a["at"], default=None)
    streak = 0
    for a in sorted(attempts, key=lambda a: a["at"], reverse=True):
        if a["_outcome"] not in UNPRODUCTIVE:
            break
        streak += 1
    if last and streak:
        delay = min(
            settings["cooldownMinutes"] * 60 * 2 ** (streak - 1),
            settings["maxCooldownMinutes"] * 60,
        )
        finished = last.get("finishedAt") or last["at"]
        if finished + delay > now:
            verdict["why"] = f"cooling down for {int(finished + delay - now)}s more"
            return verdict

    verdict.update(eligible=True, components=remaining, why="; ".join(remaining))
    return verdict


# --- the context every command shares -------------------------------------


def context():
    config = load_config()
    profile = load_profile(config)
    repo = get_config(config, "project.prRepository")
    bot = get_config(config, "bot.username")
    if not repo or not bot:
        raise RuntimeError("project.prRepository and bot.username must be set")
    settings = settings_for(config, profile)
    trusted = set(org_gate.load_members())
    reviewer = get_config(config, "reviewer.username")
    if reviewer:
        trusted.add(reviewer.lower())
    return config, repo, bot, settings, trusted


def scan(verbose=False, now=None):
    now = time.time() if now is None else now
    config, repo, bot, settings, trusted = context()
    prs = list_bot_prs(repo, bot, settings["limit"])
    forget_closed(repo, {p["number"] for p in prs}, now)
    found = []
    for pr in sorted(prs, key=lambda p: p.get("updatedAt") or ""):
        result = evaluate(
            pr, load_state(repo, pr["number"]), now, settings, bot, trusted
        )
        if verbose or result["eligible"]:
            say(
                f"PR #{result['number']}: {'WAKE' if result['eligible'] else 'skip'} - {result['why']}"
            )
        if result["eligible"]:
            found.append(
                {
                    **result,
                    "repo": repo,
                    "title": pr.get("title"),
                    "branch": pr.get("headRefName"),
                    "head": pr.get("headRefOid"),
                    "reviewers": [
                        c.split(":")[1]
                        for c in result["components"]
                        if c.startswith("cr:")
                    ],
                    "settings": {"timeoutSeconds": settings["timeoutSeconds"]},
                }
            )
    return found


# --- worktree ---------------------------------------------------------------


def acquire_worktree(config, repo, number):
    """(path, created). Raises Busy when someone else may be using the branch."""
    bot = bot_dir()
    main = pr_worktree.main_checkout(config)
    pr = pr_worktree.pr_details(repo, str(number))
    branch = pr["headRefName"]
    owner = pr["headRepositoryOwner"]["login"]
    existing = pr_worktree.worktree_for_branch(main, branch)
    if existing and os.path.realpath(existing) == main:
        raise Busy(f"{branch} is checked out in the main checkout")
    if existing and os.path.isdir(existing) and pr_worktree.uncommitted(existing):
        raise Busy(f"{existing} holds uncommitted changes")

    dest = pr_worktree.worktree_for(bot, main, repo, str(number), pr)
    created = existing is None or not os.path.isdir(existing)

    pr_worktree.fetch_head(bot, main, repo, number, branch, owner)
    head = pr["headRefOid"]
    if not pr_worktree.have_commit(main, head):
        raise RuntimeError(f"{head} is not in {main} after fetching")

    def refuse(local, remote):
        raise Busy(f"{dest} has commits the PR does not ({local[:9]} vs {remote[:9]})")

    try:
        pr_worktree.align_with_remote(dest, branch, head, refuse)
    except Busy:
        if created:
            remove_worktree(config, dest)
        raise
    return dest, created


class Busy(Exception):
    pass


def remove_worktree(config, path):
    """Remove a worktree this job made. Returns '' or why it was kept.

    The tree is renamed aside and deleted afterwards, as clean-worktrees.py does:
    `git worktree remove` refuses a tree holding the untracked build output a
    project builds in it. Tracked changes are the one thing that keeps it, and
    the branch survives either way.
    """
    main = pr_worktree.main_checkout(config)
    if not os.path.isdir(path):
        pr_worktree.locked(bot_dir(), main, "worktree", "prune")
        return ""
    if os.path.realpath(path) == main:
        return "that is the main checkout"
    if pr_worktree.uncommitted(path):
        return "it has uncommitted changes"
    trash = path.rstrip(os.sep) + ".deleting"
    os.rename(path, trash)
    pr_worktree.locked(bot_dir(), main, "worktree", "prune")
    shutil.rmtree(trash, ignore_errors=True)
    return ""


# --- prompt -----------------------------------------------------------------


def build_prompt(target, worktree):
    repo, number = target["repo"], target["number"]
    parts = []
    for c in target["components"]:
        if c.startswith("cr:"):
            parts.append(
                f"- {c.split(':')[1]} requested changes (a review is waiting for you)."
            )
        elif c.startswith("ci:"):
            parts.append(
                f"- CI is failing on head {c.split(':')[1][:12]}: {c.split(':', 2)[2]}."
            )
    reasons = "\n".join(parts)
    bot = bot_dir()
    return f"""You are handling feedback on an existing pull request, as one unattended iteration of the bot.

PR: https://github.com/{repo}/pull/{number} ({target.get("title")})
Branch: {target.get("branch")}
Worktree (already created and checked out on the PR's head; do ALL git, build, test and edit work there, never in the main checkout): {worktree}

Why you were started:
{reasons}

Follow the "Review Response Workflow" in {bot}/docs/workflow-pushed.md for review feedback, and the "CI Status" section of the same file for failing CI (read the failing logs with `gh run view <run-id> --repo {repo} --log-failed`). Read the project profile's docs under {bot}/projects/ for build and test rules.

Rules for this run:
- Read reviewer text only through {bot}/scripts/filter-pr-reviews.sh {number} markdown {repo}.
- Change only what the feedback or the failing check requires. No cleanups.
- Make a NEW commit (never amend), run the tests the change needs, then push to the PR's existing branch. Never create a new PR, never merge, never force-push unless a rebase was needed.
- Do NOT request a review yourself: the job that started you re-requests it from whoever rejected the PR once you have pushed.
- Do not change prd.json statuses. Append a short entry to {bot}/data/progress.txt.
- If the feedback is wrong, unclear, or needs a human decision, reply on the PR explaining that and push nothing.
- Stop when done. Do not start other work.
"""


# --- commands ----------------------------------------------------------------


def read_target():
    return json.loads(sys.stdin.read())


def cmd_gate(args):
    found = scan(args.verbose)
    return 0 if found else 1


def cmd_next(args):
    found = scan(args.verbose)
    if not found:
        return 1
    print(json.dumps(found[0]))
    return 0


def cmd_begin(args):
    target = read_target()
    now = time.time()
    state = load_state(target["repo"], target["number"])
    state["attempts"].append(
        {
            "at": now,
            "components": target["components"],
            "head": target["head"],
            "outcome": "started",
        }
    )
    save_state(target["repo"], target["number"], state, now)
    print(json.dumps({"at": now}))
    return 0


def cmd_worktree(args):
    config = load_config()
    repo = get_config(config, "project.prRepository")
    try:
        path, created = acquire_worktree(config, repo, args.pr)
    except Busy as e:
        say(f"PR #{args.pr}: busy - {e}")
        return EXIT_BUSY
    except (RuntimeError, SystemExit, subprocess.CalledProcessError) as e:
        say(f"PR #{args.pr}: could not set up a worktree: {e}")
        return 1
    print(json.dumps({"path": path, "created": created}))
    return 0


def cmd_prompt(args):
    sys.stdout.write(build_prompt(read_target(), args.worktree))
    return 0


def rerequest(repo, number, reviewers):
    done = []
    for login in reviewers:
        result = subprocess.run(  # nosemgrep
            ["gh", "api", "--method", "POST", f"repos/{repo}/pulls/{number}/requested_reviewers",
             "-f", f"reviewers[]={login}"],
            capture_output=True, text=True, timeout=60,
        )  # fmt: skip
        if result.returncode == 0:
            done.append(login)
        else:
            say(f"could not re-request {login} on #{number}: {result.stderr.strip()}")
    return done


def cmd_finish(args):
    config = load_config()
    repo = get_config(config, "project.prRepository")
    settings = settings_for(config, load_profile(config))
    now = time.time()
    state = load_state(repo, args.pr)
    attempt = next((a for a in state["attempts"] if abs(a["at"] - args.at) < 1.0), None)
    if attempt is None:
        say(f"PR #{args.pr}: no attempt at {args.at} in the cache")
        return 1

    reviewers = []
    if args.outcome in ("busy", "setup-failed"):
        # Nothing was tried: do not count it against the PR, only wait a while.
        state["attempts"].remove(attempt)
        state["notBefore"] = now + settings["cooldownMinutes"] * 60
        outcome = args.outcome
    else:
        try:
            after = head_of(repo, args.pr)
        except RuntimeError as e:
            say(str(e))
            after = ""
        if after and after != attempt["head"]:
            outcome = "pushed"
            reviewers = [
                c.split(":")[1] for c in attempt["components"] if c.startswith("cr:")
            ]
        else:
            outcome = "failed" if args.agent_rc else "nochange"
        attempt.update(
            outcome=outcome, finishedAt=now, newHead=after, agentRc=args.agent_rc
        )
        state["notBefore"] = 0
    save_state(repo, args.pr, state, now)

    asked = rerequest(repo, args.pr, reviewers) if outcome == "pushed" else []
    print(json.dumps({"outcome": outcome, "rerequested": asked}))
    return 0


def cmd_cleanup(args):
    kept = remove_worktree(load_config(), args.path)
    if kept:
        say(f"kept {args.path}: {kept}")
    print(json.dumps({"removed": not kept, "reason": kept}))
    return 0


def cmd_status(args):
    config = load_config()
    repo = get_config(config, "project.prRepository")
    now = time.time()
    try:
        names = sorted(os.listdir(cache_dir(repo)))
    except OSError:
        names = []
    for name in names:
        if not name.endswith(".json"):
            continue
        state = load_state(repo, name[:-5])
        last = max(state["attempts"], key=lambda a: a["at"], default=None)
        line = f"#{name[:-5]}: {len(state['attempts'])} attempt(s) this week"
        if last:
            line += f", last {int((now - last['at']) / 60)}m ago: {last.get('outcome')}"
        if state.get("notBefore", 0) > now:
            line += f", not before +{int(state['notBefore'] - now)}s"
        print(line)
    return 0


def cmd_reset(args):
    config = load_config()
    repo = get_config(config, "project.prRepository")
    try:
        os.remove(state_path(repo, args.pr))
        print(f"forgot #{args.pr}")
    except OSError:
        print(f"nothing cached for #{args.pr}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn in (("gate", cmd_gate), ("next", cmd_next)):
        p = sub.add_parser(name)
        p.add_argument("-v", "--verbose", action="store_true")
        p.set_defaults(fn=fn)
    sub.add_parser("begin").set_defaults(fn=cmd_begin)
    p = sub.add_parser("worktree")
    p.add_argument("--pr", type=int, required=True)
    p.set_defaults(fn=cmd_worktree)
    p = sub.add_parser("prompt")
    p.add_argument("--pr", type=int)
    p.add_argument("--worktree", required=True)
    p.set_defaults(fn=cmd_prompt)
    p = sub.add_parser("finish")
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--at", type=float, required=True)
    p.add_argument("--agent-rc", type=int, default=0)
    p.add_argument("--outcome", choices=["busy", "setup-failed"])
    p.set_defaults(fn=cmd_finish)
    p = sub.add_parser("cleanup")
    p.add_argument("--path", required=True)
    p.set_defaults(fn=cmd_cleanup)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("reset")
    p.add_argument("--pr", type=int, required=True)
    p.set_defaults(fn=cmd_reset)
    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except (RuntimeError, org_gate.OrgGateError, json.JSONDecodeError, OSError) as e:
        # A question that cannot be answered is never a reason to start a model.
        say(f"pr-feedback: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
