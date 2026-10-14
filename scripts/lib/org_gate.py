"""Keep PRs from non-members of the org away from every review job.

A review reads the PR's diff, description and source tree into a model, so a PR
from someone outside the org is untrusted input that no review job should feed
to one. This is the single gate, and it involves no model: the author is looked
up in .ignore/org-members.txt, and a PR whose author is not listed is dropped
and is logged; nothing is posted on it.

Both review paths use it, whichever project profile (brave-core, bravebot, ...)
they run for:

  * the review-request queue (scripts/lib/review-requests.sh), before the poll
    wakes a session, and
  * prepare-review.py, which every sweep and every single-PR review goes
    through, before it fetches a diff or writes a prompt.

It fails closed. A missing or empty members file stops the job rather than
letting every author through, because an empty list used to mean "no filter".

    python3 org_gate.py filter-queue < "<number> <author>" lines
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

from lib.load_config import bot_dir  # noqa: E402

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


def split_prs(prs):
    """Return (members' PRs, skipped PRs). Nothing is posted on a skipped PR.

    `prs` are dicts with "number" and an "author" that is a login or a
    {"login": ...} object.
    """
    members = load_members()
    allowed, skipped = [], []
    for pr in prs:
        author = pr.get("author") or ""
        if isinstance(author, dict):
            author = author.get("login", "")
        (allowed if is_member(author, members) else skipped).append(pr)
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
    args = parser.parse_args(argv)

    prs = []
    for line in sys.stdin:
        number, _, author = line.strip().partition(" ")
        if number:
            prs.append({"number": int(number), "author": author})
    try:
        allowed, _ = split_prs(prs)
    except OrgGateError as e:
        _log(f"org gate: {e}")
        return 1
    for pr in allowed:
        print(pr["number"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
