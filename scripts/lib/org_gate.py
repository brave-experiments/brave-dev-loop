"""Keep PRs from non-members of the org away from every review job.

A review reads the PR's diff, description and source tree into a model, so a PR
from someone outside the org is untrusted input that no review job should feed
to one. This is the single gate, and it involves no model: the author is looked
up in .ignore/org-members.txt, and a PR whose author is not listed is dropped
and gets one fixed comment.

Both review paths use it, whichever project profile (brave-core, bravebot, ...)
they run for:

  * the review-request queue (scripts/lib/review-requests.sh), before the poll
    wakes a session, and
  * prepare-review.py, which every sweep and every single-PR review goes
    through, before it fetches a diff or writes a prompt.

It fails closed. A missing or empty members file stops the job rather than
letting every author through, because an empty list used to mean "no filter".

    python3 org_gate.py filter-queue --repo brave/brave-core < "<number> <author>" lines
"""

import argparse
import os
import subprocess  # nosemgrep
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

from lib.load_config import bot_dir  # noqa: E402

SKIP_MARKER = "<!-- review-skipped-not-org-member -->"
SKIP_COMMENT = f"Review skipped because not a registered org member.\n\n{SKIP_MARKER}"


class OrgGateError(RuntimeError):
    """The gate cannot answer. Callers stop; they never fall through to review."""


def members_path():
    # The override is for tests; nothing in the scheduled jobs sets it.
    return os.environ.get("BOT_ORG_MEMBERS_FILE") or os.path.join(
        bot_dir(), ".ignore", "org-members.txt"
    )


def load_members(path=None):
    """Lowercased logins of the org's members. Raises when there are none."""
    path = path or members_path()
    try:
        with open(path) as f:
            members = {line.strip().lower() for line in f if line.strip()}
    except FileNotFoundError:
        raise OrgGateError(
            f"org members file not found at {path}; run `make setup` to create it"
        ) from None
    if not members:
        raise OrgGateError(f"org members file {path} is empty")
    return members


def is_member(login, members):
    return bool(login) and login.lower() in members


def _gh(args, **kwargs):
    return subprocess.run(  # nosemgrep
        ["gh", *args], capture_output=True, text=True, timeout=60, **kwargs
    )


def _current_login():
    result = _gh(["api", "user", "--jq", ".login"])
    return result.stdout.strip() if result.returncode == 0 else ""


def already_commented(repo, number, login):
    """Did this account already leave the skip comment? Raises when GitHub cannot say."""
    result = _gh(
        [
            "api",
            f"repos/{repo}/issues/{number}/comments",
            "--paginate",
            "--jq",
            '.[] | [.user.login, (.body | contains("' + SKIP_MARKER + '"))] | @tsv',
        ]
    )
    if result.returncode != 0:
        raise OrgGateError(
            f"could not read comments on #{number}: {result.stderr.strip()}"
        )
    for line in result.stdout.splitlines():
        user, _, has_marker = line.partition("\t")
        if has_marker == "true" and user.lower() == login.lower():
            return True
    return False


def comment_once(repo, number, log):
    """Post the fixed skip comment unless this account already has."""
    login = _current_login()
    if not login:
        raise OrgGateError("could not determine the gh login to comment as")
    if already_commented(repo, number, login):
        return False
    result = _gh(
        [
            "api",
            f"repos/{repo}/issues/{number}/comments",
            "-X",
            "POST",
            "-f",
            f"body={SKIP_COMMENT}",
        ]
    )
    if result.returncode != 0:
        raise OrgGateError(f"could not comment on #{number}: {result.stderr.strip()}")
    log(f"#{number}: author is not an org member; skipped and commented")
    return True


def split_prs(repo, prs, log):
    """Return (members' PRs, skipped PRs); comment once on each skipped one.

    `prs` are dicts with "number" and an "author" that is a login or a
    {"login": ...} object. A failed comment is logged, not raised: the PR is
    dropped either way and the next run tries the comment again.
    """
    members = load_members()
    allowed, skipped = [], []
    for pr in prs:
        author = pr.get("author") or ""
        if isinstance(author, dict):
            author = author.get("login", "")
        (allowed if is_member(author, members) else skipped).append(pr)
    for pr in skipped:
        try:
            comment_once(repo, pr["number"], log)
        except (OrgGateError, subprocess.SubprocessError) as e:
            log(f"#{pr['number']}: not commenting on skipped PR: {e}")
    return allowed, skipped


def _log(msg):
    print(msg, file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser(
        "filter-queue",
        help='read "<number> <author>" lines on stdin, print the members\' numbers',
    )
    q.add_argument("--repo", required=True)
    args = parser.parse_args(argv)

    prs = []
    for line in sys.stdin:
        number, _, author = line.strip().partition(" ")
        if number:
            prs.append({"number": int(number), "author": author})
    try:
        allowed, _ = split_prs(args.repo, prs, _log)
    except OrgGateError as e:
        _log(f"org gate: {e}")
        return 1
    for pr in allowed:
        print(pr["number"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
