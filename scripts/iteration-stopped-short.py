#!/usr/bin/env python3
"""Say whether an iteration ended before its story got as far as a PR.

An iteration on a pending story ends one of two ways the workflow allows:
update-prd-status.py moves the story on, or a progress entry says why it stayed
(docs/workflow-pending.md, "Never stop without leaving a record"). A committed
story is the same, one step on: it has no PR until it is pushed, and an entry
recording its arrival at committed says nothing about why it stopped there. A
session that ends its turn to wait on a gate, or answers a "continue" as if
nothing was asked, does neither. In a --print run that turn was the whole
session, so the work it had not pushed and every check it was waiting on end
there too; in the terminal it sits waiting on a person who is not there.

Two ways to ask:

  After the agent exits, run.sh passes the story on the command line and gets one
  JSON object on stdout:

    stoppedShort  the story started and ended pending or committed, with no entry
                  for it in the progress log past --progress-offset that says why
    work          one line on what git shows of the story's work
    entry         the progress entry to write when nobody else did
    resumePrompt  what to tell the session when run.sh resumes it

  While the agent runs, Claude Code calls `--hook STATE` as its Stop hook, with
  the hook event on stdin and run.sh's STATE file naming the story. A stop that
  leaves the story short is refused, with the reason the model reads next, up to
  MAX_BLOCKS times an iteration; STATE keeps the count.

Nothing here writes to the PRD or the progress log: run.sh decides whether to
resume the session, and pipes `entry` to append-progress.sh when it stops trying.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from subprocess import CalledProcessError  # nosemgrep

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.pr_worktree import run, worktree_for_branch

BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHORT_STATUSES = ("pending", "committed")
MAX_BLOCKS = 5
# Under this much of the iteration left, a session is told to write its entry
# rather than start anything: it is killed at the limit, entry or not.
WRAP_UP_SECONDS = 600
ARRIVAL = re.compile(r"(?:→|->)\s*committed\b")


def story_in_prd(prd_path, story_id):
    with open(prd_path) as f:
        prd = json.load(f)
    for story in prd.get("stories", []):
        if story.get("id") == story_id:
            return story
    return {}


def recorded(progress_path, offset, story_id, end_status="pending"):
    """Whether a heading past `offset` says why the story stopped where it is.

    Every entry opens with a `## ` heading naming its story, whatever the rest
    of it says. The id has to stand alone: US-31 is not an entry for US-313.
    A log shorter than the offset was rotated, so all of it is new. For a story
    that ended committed, the heading that recorded it arriving there does not
    count: that entry was written on the way to the PR, not about its absence.
    """
    try:
        with open(progress_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(offset if f.tell() >= offset else 0)
            text = f.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return False
    heading = re.compile(
        r"^##\s.*(?<![\w-])" + re.escape(story_id) + r"(?![\w-]).*$", re.MULTILINE
    )
    for match in heading.finditer(text):
        if end_status == "committed" and ARRIVAL.search(match.group(0)):
            continue
        return True
    return False


def work_in(repo, branch):
    """What git shows of the branch: its worktree, head, and what only it holds.

    Untracked files count: a new source file is work as much as an edit is, and
    build output is what .gitignore is for.
    """
    found = {"worktree": None, "head": None, "uncommitted": None, "unpushed": None}
    if not branch:
        return found
    try:
        worktree = worktree_for_branch(repo, branch)
        if not worktree or not os.path.isdir(worktree):
            return found
        found["worktree"] = worktree
        found["head"] = run("git", "-C", worktree, "rev-parse", "--short", "HEAD")
        status = run("git", "-C", worktree, "status", "--porcelain")
        found["uncommitted"] = len(status.splitlines())
        found["unpushed"] = int(
            run(
                "git",
                "-C",
                worktree,
                "rev-list",
                "--count",
                "HEAD",
                "--not",
                "--remotes",
            )
        )
    except (CalledProcessError, ValueError):
        pass
    return found


def describe(branch, found):
    if not branch:
        return "The story has no branch recorded, so there is no worktree to report."
    if not found["worktree"]:
        return f"No worktree holds {branch}."
    if found["uncommitted"] is None:
        return f"{found['worktree']} holds {branch}; git could not read it."
    return (
        f"{found['worktree']} holds {branch} at {found['head']}, with "
        f"{found['uncommitted']} uncommitted file(s) and "
        f"{found['unpushed']} commit(s) no remote has."
    )


def assess(prd, story_id, start_status, start_branch, progress_file, offset, repo):
    story = story_in_prd(prd, story_id)
    end_status = story.get("status") or ""
    branch = story.get("branchName") or start_branch or None
    was_recorded = recorded(progress_file, offset, story_id, end_status)
    found = work_in(repo, branch)
    return {
        "stoppedShort": start_status in SHORT_STATUSES
        and end_status in SHORT_STATUSES
        and not was_recorded,
        "startStatus": start_status,
        "endStatus": end_status,
        "recorded": was_recorded,
        "branch": branch,
        **found,
        "work": describe(branch, found),
    }


def doc(name):
    return os.path.join(BOT_DIR, "docs", name)


def next_step(status):
    """What the workflow asks of a story in this status before the iteration ends."""
    if status == "committed":
        return (
            f"It is committed with no PR. Carry on with {doc('workflow-committed.md')}: "
            "push the branch, open the draft PR and move the story to pushed. If the "
            "push or the PR fails, append an entry saying why, then end."
        )
    return (
        f"Carry on with {doc('workflow-pending.md')} from where you stopped, "
        "re-running any check that had not finished, and take the story to the "
        "status the workflow gives it: committed, then pushed with a draft PR. If "
        "it cannot get there in this iteration, append the entry "
        f"{doc('progress-reporting.md')} describes for an iteration that ends "
        "without a transition, then end."
    )


def waiting():
    return (
        "Do not end your turn for any other reason. Wait on a long check with "
        f"{os.path.join(BOT_DIR, 'scripts', 'wait-gate.sh')}, never by ending the turn."
    )


def entry(story_id, status, work, resumed, session_id, log, ended="exit"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    after = f", resumed {resumed} time(s)" if resumed else ""
    how = (
        "the session wrote nothing for too long and run.sh stopped it"
        if ended == "quiet"
        else "the session ended"
    )
    if status == "committed":
        what = f"{how} with the story committed, no PR, and nothing saying why"
    else:
        what = f"{how} without moving the story or saying why"
    lines = [
        f"## {now} - {story_id} - Status: {status} (iteration ended, no transition)",
        f"- Why it stopped: {what}{after}. run.sh wrote this entry from git, so it "
        "cannot say which gates passed.",
        f"- Work left: {work}",
    ]
    if log:
        lines.append(f"- Artefacts: {log} -- read its end before re-running anything.")
    if session_id:
        lines.append(f"- **Resume command:** `claude -r {session_id}`")
    lines.append("---")
    return "\n".join(lines) + "\n"


def resume_prompt(story_id, status, work, ended="exit"):
    if ended == "quiet":
        why = (
            f"The session wrote nothing for too long, with story {story_id} still "
            f"{status} and no progress entry for it, so run.sh stopped it and every "
            "background task, gates included, with it: an API error, a stalled call, "
            "or a turn that ended waiting on an answer nobody was there to give."
        )
    else:
        why = (
            f"Your turn ended with story {story_id} still {status} and no progress "
            "entry for it. This iteration runs with --print, so ending a turn ended "
            "the session: nothing you were waiting on will report back, and every "
            "background task, gates included, was stopped with it."
        )
    return f"{why}\n{work}\n\n{next_step(status)} {waiting()}"


def stop_reason(story_id, status, work, left, block):
    if status == "committed":
        where = f"Story {story_id} is committed but has no PR, and no entry says why."
    else:
        where = f"Story {story_id} is still pending, and no entry says why."
    if left < WRAP_UP_SECONDS:
        step = (
            f"Under {WRAP_UP_SECONDS // 60} minutes of this iteration are left and the "
            "session is killed at the limit. Append the entry "
            f"{doc('progress-reporting.md')} describes for an iteration that ends "
            f"without a transition now, with {os.path.join(BOT_DIR, 'scripts', 'append-progress.sh')}, "
            "then end."
        )
    else:
        step = f"About {left // 60} minutes of this iteration are left. {next_step(status)} {waiting()}"
    return f"{where} {work}\n\n{step}\n\n(Stop check {block} of {MAX_BLOCKS}.)"


def write_state(path, state):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def hook(state_path, event):
    """The Stop hook: refuse a stop that leaves the story short, a few times."""
    with open(state_path) as f:
        state = json.load(f)
    if event.get("session_id") and event.get("session_id") != state.get("sessionId"):
        return None
    blocks = int(state.get("blocks") or 0)
    if blocks >= MAX_BLOCKS:
        return None
    found = assess(
        state["prd"],
        state["storyId"],
        state["startStatus"],
        state.get("startBranch") or "",
        state["progressFile"],
        int(state.get("progressOffset") or 0),
        state["repo"],
    )
    if not found["stoppedShort"]:
        return None
    blocks += 1
    state["blocks"] = blocks
    write_state(state_path, state)
    left = int(state["seconds"]) - (int(time.time()) - int(state["startedAt"]))
    return {
        "decision": "block",
        "reason": stop_reason(
            state["storyId"], found["endStatus"], found["work"], left, blocks
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--hook", metavar="STATE", help="run as Claude Code's Stop hook"
    )
    parser.add_argument("--prd")
    parser.add_argument("--story-id")
    parser.add_argument("--start-status")
    parser.add_argument("--start-branch", default="")
    parser.add_argument("--progress-file")
    parser.add_argument("--progress-offset", type=int, default=0)
    parser.add_argument("--repo", help="the target repo's main checkout")
    parser.add_argument("--resumed", type=int, default=0)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--log", default="")
    parser.add_argument(
        "--ended",
        choices=("exit", "quiet"),
        default="exit",
        help="quiet: the watchdog stopped a session that had written nothing",
    )
    args = parser.parse_args(argv)

    if args.hook:
        # A hook that fails must not wedge the session: any error lets it stop.
        try:
            decision = hook(args.hook, json.load(sys.stdin))
        except Exception as e:  # noqa: BLE001
            print(f"iteration-stopped-short: {e}", file=sys.stderr)
            return 0
        if decision:
            print(json.dumps(decision))
        return 0

    missing = [
        f"--{name.replace('_', '-')}"
        for name in ("prd", "story_id", "start_status", "progress_file", "repo")
        if getattr(args, name) is None
    ]
    if missing:
        parser.error("required: " + ", ".join(missing))

    found = assess(
        args.prd,
        args.story_id,
        args.start_status,
        args.start_branch,
        args.progress_file,
        args.progress_offset,
        args.repo,
    )
    status = found["endStatus"]
    found["entry"] = entry(
        args.story_id,
        status,
        found["work"],
        args.resumed,
        args.session_id,
        args.log,
        args.ended,
    )
    found["resumePrompt"] = resume_prompt(
        args.story_id, status, found["work"], args.ended
    )
    print(json.dumps(found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
