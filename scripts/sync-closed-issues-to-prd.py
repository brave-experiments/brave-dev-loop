#!/usr/bin/env python3
"""Retire pending stories whose issue has already been closed.

Intake asks GitHub for open issues only, so a story is created while its issue
is open and nothing revisits that afterwards. sync-merged-prs-to-prd.py closes
the loop for a story that has a pull request of its own; a story still waiting
for its first one has nothing GitHub can be asked about, and an issue somebody
else's pull request closes leaves its story sitting in the pending queue.

An agent iteration is then the only thing that discovers it: clone, read the
issue, search for what fixed it, write the conclusion — a session to reach a
status one API call decides. US-094 (brave/bravebot#116) did exactly that, four
days after PR #349 closed the issue as COMPLETED and left the story pending.

This is the issue end of the same reconciliation: sync-bot-prs-to-prd.py adopts
the bot's open PRs, sync-merged-prs-to-prd.py retires the ones that land, and
this retires a story whose issue was settled without the bot.

Only pending stories with no pull request are considered. A committed or pushed
story has commits or an open PR that a person still has to dispose of, and
retiring it here would orphan them with nothing said. Nor does this comment on
the issue the way an agent marking a story invalid does: that notification is
there so a watcher of an *open* issue is not left wondering, and a closed issue
already records who closed it and, for a merge, what did it.

The status is terminal and archive-prd.py moves it out of the PRD, so if the
issue is reopened intake will not re-add it (add-backlog-to-prd.py treats any
non-merged story as a judgement it must not overturn). Putting the work back
means restoring the story through lib.prd_store.mutate_prd by hand.

Usage:
  scripts/sync-closed-issues-to-prd.py              # update data/prd.json in place
  scripts/sync-closed-issues-to-prd.py --dry-run    # report only, change nothing

Exit codes:
  0 - success (stories retired, or nothing to retire)
  2 - error
"""

import argparse
import json
import os
import re
import subprocess  # nosemgrep
import sys

_script_dir = os.path.dirname(os.path.abspath(__file__))
_bot_dir = os.path.dirname(_script_dir)
sys.path.insert(0, _script_dir)
from lib.load_config import load_config, require_config  # noqa: E402
from lib.prd_store import load_prd, prd_lock, save_prd  # noqa: E402

_config = load_config()
_issue_repo = require_config(_config, "project.issueRepository")

# `gh issue view --json stateReason` only exists from gh 2.46, and an unknown
# field fails the whole read, so the state comes from GraphQL instead -- every
# gh version can reach it, and it names the fields the same way.
ISSUE_STATE_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) { state stateReason closedAt }
  }
}
"""


def fetch_issue(number):
    """State of one issue, or None when GitHub cannot be asked about it.

    A single unreachable issue must not cost the whole pass: this runs before a
    run's first iteration, and reconciling the other stories is still worth
    doing.
    """
    owner, _, name = _issue_repo.partition("/")
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={ISSUE_STATE_QUERY}",
                "-f",
                f"owner={owner}",
                "-f",
                f"name={name}",
                "-F",
                f"number={number}",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"  issue #{number}: could not be read ({e})", file=sys.stderr)
        return None
    if result.returncode != 0:
        print(
            f"  issue #{number}: could not be read ({result.stderr.strip()})",
            file=sys.stderr,
        )
        return None
    try:
        issue = json.loads(result.stdout)["data"]["repository"]["issue"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"  issue #{number}: unreadable response ({e})", file=sys.stderr)
        return None
    if issue is None:
        print(f"  issue #{number}: no such issue", file=sys.stderr)
    return issue


def story_issue_number(story):
    """Issue number a story references in its description, or None."""
    match = re.search(r"issue #(\d+)", story.get("description") or "")
    return int(match.group(1)) if match else None


def is_candidate(story):
    """True when a closed issue is enough to retire this story on its own."""
    return story.get("status") == "pending" and story.get("prNumber") is None


def candidate_issue_numbers(prd):
    """Issue numbers of the pending stories that have no pull request yet."""
    numbers = []
    for story in prd.get("stories", []):
        if not is_candidate(story):
            continue
        number = story_issue_number(story)
        if number is not None:
            numbers.append(number)
    return sorted(set(numbers))


def reason(number, issue):
    """Why the story was retired, for the field a reader actually sees."""
    state_reason = issue.get("stateReason") or "CLOSED"
    closed_at = issue.get("closedAt")
    when = f" on {closed_at}" if closed_at else ""
    return (
        f"Issue #{number} was closed as {state_reason}{when}, before this story "
        f"was worked. Retired by scripts/sync-closed-issues-to-prd.py without an "
        f"iteration, so the issue thread has not been read and no comment was "
        f"posted on it."
    )


def retire(story, number, issue):
    """Move one story to "invalid".

    The fields match `update-prd-status.py invalid` so a story retired here is
    indistinguishable from one an iteration retired.
    """
    story["status"] = "invalid"
    story["skipReason"] = reason(number, issue)


def main():
    parser = argparse.ArgumentParser(
        description="Retire pending stories whose issue has already been closed"
    )
    parser.add_argument(
        "--prd",
        default=os.path.join(_bot_dir, "data", "prd.json"),
        help="Path to prd.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be retired, change nothing",
    )
    args = parser.parse_args()

    if not os.path.exists(args.prd):
        print(f"Error: {args.prd} does not exist", file=sys.stderr)
        return 2

    # The GitHub reads stay outside the lock, as in the sibling syncs: holding
    # the PRD across one call per story would stall every other run.
    candidates = candidate_issue_numbers(load_prd(args.prd))
    closed = {}
    for number in candidates:
        issue = fetch_issue(number)
        if issue and issue.get("state") == "CLOSED":
            closed[number] = issue

    retired = []
    with prd_lock(args.prd):
        prd = load_prd(args.prd)
        for story in prd.get("stories", []):
            # Re-checked under the lock: another run's iteration may have moved
            # this story since the issue numbers were collected, and rewriting
            # it would undo whatever else that iteration recorded.
            if not is_candidate(story):
                continue
            number = story_issue_number(story)
            issue = closed.get(number)
            if not issue:
                continue
            retire(story, number, issue)
            retired.append(
                {
                    "id": story["id"],
                    "issueNumber": number,
                    "title": story.get("title"),
                    "stateReason": issue.get("stateReason"),
                }
            )
        if retired and not args.dry_run:
            save_prd(args.prd, prd)

    verb = "Would retire" if args.dry_run else "Retired"
    print(
        f"{verb} {len(retired)} closed issue(s) of {len(candidates)} pending "
        f"story(ies) with an issue",
        file=sys.stderr,
    )
    for story in retired:
        print(
            f"  {story['id']}: issue #{story['issueNumber']} closed as "
            f"{story['stateReason'] or 'CLOSED'}",
            file=sys.stderr,
        )

    print(
        json.dumps(
            {
                "retired": retired,
                "checked": len(candidates),
                "dryRun": args.dry_run,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
