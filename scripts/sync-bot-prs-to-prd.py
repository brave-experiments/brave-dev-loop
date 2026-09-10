#!/usr/bin/env python3
"""Add stories for bot-authored PRs that aren't tracked in prd.json.

Some cron skills (learnable-pattern-search, update-best-practices) open PRs
directly against the target repo without ever creating a story. Those PRs are
invisible to run.sh/select-task.py, so nothing ever pings, rebases, or lands
them. This script closes that gap: every open PR authored by `bot.username`
that isn't already tracked gets appended as a "pushed" story, the same way
add-backlog-to-prd appends discovered issues.

Usage:
  scripts/sync-bot-prs-to-prd.py                 # update data/prd.json in place
  scripts/sync-bot-prs-to-prd.py --dry-run       # report only, write nothing
  scripts/sync-bot-prs-to-prd.py --pr 38869      # only consider this PR

Exit codes:
  0 - success (stories added, or nothing to add)
  2 - error
"""

import argparse
import copy
import json
import os
import re
import subprocess
import sys

_script_dir = os.path.dirname(os.path.abspath(__file__))
_bot_dir = os.path.dirname(_script_dir)
sys.path.insert(0, _script_dir)
from lib.load_config import (
    build_research,
    build_validations,
    get_config,
    load_config,
    load_profile,
    require_config,
    test_step,
)
from lib.prd_store import prd_lock, save_prd

_config = load_config()
_pr_repo = require_config(_config, "project.prRepository")
_issue_repo = require_config(_config, "project.issueRepository")
_bot_user = require_config(_config, "bot.username")
_default_branch = get_config(_config, "project.defaultBranch", "master")
_profile = load_profile(_config, _bot_dir)
_research = build_research(_profile, _config, _bot_dir)

PR_FIELDS = "number,title,url,headRefName,isDraft,body,files"

# "Closes #123", "fixes brave/brave-browser#123", "resolved
# https://github.com/brave/brave-browser/issues/123"
_CLOSING_KEYWORD_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[:\s]+"
    r"(?:(?P<repo>[\w.-]+/[\w.-]+)#|#|"
    r"https?://github\.com/(?P<url_repo>[\w.-]+/[\w.-]+)/issues/)"
    r"(?P<number>\d+)",
    re.IGNORECASE,
)

DOC_SUFFIXES = (".md", ".txt", ".rst")


def run_gh(args):
    """Run a gh command, returning parsed JSON. Exits on failure."""
    try:
        result = subprocess.run(
            ["gh"] + args, capture_output=True, text=True, timeout=120
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"Error running gh {' '.join(args)}: {e}", file=sys.stderr)
        sys.exit(2)
    if result.returncode != 0:
        print(
            f"Error running gh {' '.join(args)}: {result.stderr.strip()}",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"Error parsing gh output: {e}", file=sys.stderr)
        sys.exit(2)


def fetch_bot_prs(pr_number=None, state="open"):
    """Fetch bot-authored PRs from the PR repository."""
    if pr_number is not None:
        pr = run_gh(
            [
                "pr",
                "view",
                str(pr_number),
                "--repo",
                _pr_repo,
                "--json",
                PR_FIELDS + ",author,state",
            ]
        )
        author = (pr.get("author") or {}).get("login")
        if author != _bot_user:
            print(
                f"PR #{pr_number} was authored by {author}, not {_bot_user} — skipping",
                file=sys.stderr,
            )
            return []
        if state != "all" and (pr.get("state") or "").lower() != state.lower():
            print(
                f"PR #{pr_number} state is {pr.get('state')}, not {state} — skipping",
                file=sys.stderr,
            )
            return []
        return [pr]

    return run_gh(
        [
            "pr",
            "list",
            "--repo",
            _pr_repo,
            "--author",
            _bot_user,
            "--state",
            state,
            "--json",
            PR_FIELDS,
            "--limit",
            "100",
        ]
    )


def load_json(path):
    """Load a JSON file, returning None if it doesn't exist or is unreadable."""
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading {path}: {e}", file=sys.stderr)
        sys.exit(2)


def tracked_pr_numbers(*prds):
    """Collect every PR number already referenced by a story in any PRD."""
    numbers = set()
    for prd in prds:
        if not prd:
            continue
        for story in prd.get("stories", []):
            pr_number = story.get("prNumber")
            if isinstance(pr_number, int):
                numbers.add(pr_number)
            for field in ("prUrl", "description"):
                value = story.get(field) or ""
                for match in re.findall(r"/pull/(\d+)", value):
                    numbers.add(int(match))
                for match in re.findall(r"\bPR #(\d+)", value):
                    numbers.add(int(match))
    return numbers


def linked_issue_number(pr):
    """Issue number in the issue repository that this PR closes, if any.

    Recorded as "issue #N" in the story description so add-backlog-to-prd, which
    dedupes on that exact phrase, never adds a second story for the same issue.
    """
    issue_owner = _issue_repo.lower()
    for match in _CLOSING_KEYWORD_RE.finditer(pr.get("body") or ""):
        repo = (match.group("repo") or match.group("url_repo") or "").lower()
        # A bare "#123" refers to the PR repo, not the issue repo.
        if repo and repo == issue_owner:
            return int(match.group("number"))
    return None


def is_docs_only(pr):
    """True when the PR touches nothing but documentation files."""
    files = [f.get("path", "") for f in pr.get("files") or []]
    if not files:
        return False
    return all(path.lower().endswith(DOC_SUFFIXES) for path in files)


def build_pr_story(story_id, priority, pr):
    """Build a "pushed" story for an already-open bot PR."""
    pr_number = pr["number"]
    title = pr["title"]
    issue_number = linked_issue_number(pr)
    docs_only = is_docs_only(pr)

    description = f"Land PR #{pr_number} in {_pr_repo}: {title}."
    if issue_number:
        description += f" Resolves issue #{issue_number}."
    description += (
        " This PR was opened directly by a cron skill with no story attached,"
        " so it is tracked here to keep review responses and merge follow-up"
        " running through the normal pushed workflow."
    )

    acceptance_criteria = [
        f"Review the existing PR #{pr_number} ({pr['url']}) — do NOT create a new PR",
        f"Rebase the branch on {_default_branch} if the PR is out of date or has conflicts",
        "Address all review feedback from Brave org reviewers and push the updates",
        "Verify CI is green on the current head after every push or rebase",
        "Wait for a maintainer to merge — never merge or force-merge the PR yourself",
    ]
    # A code PR has to survive the same checks a story's own work does, so the
    # steps come from the profile rather than being spelled out here -- what
    # counts as "the checks" is the project's answer, not this script's.
    if not docs_only:
        acceptance_criteria[3:3] = [
            *_research,
            *build_validations(_profile, test_step(_profile, "generic")),
        ]

    return {
        "id": f"US-{story_id:03d}",
        "title": title,
        "description": description,
        "acceptanceCriteria": acceptance_criteria,
        "priority": priority,
        "status": "pushed",
        "prNumber": pr_number,
        # "bot", not "reviewer": the pushed workflow re-derives who went last
        # from the real review data, and seeding "reviewer" would fake URGENT.
        "lastActivityBy": "bot",
        "branchName": pr["headRefName"],
        "prUrl": pr["url"],
        "sourcedFromPr": True,
        "docsOnly": docs_only,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Add stories for untracked bot-authored PRs"
    )
    parser.add_argument(
        "--prd",
        default=os.path.join(_bot_dir, "data", "prd.json"),
        help="Path to prd.json",
    )
    parser.add_argument(
        "--archived-prd",
        default=os.path.join(_bot_dir, "data", "prd.archived.json"),
        help="Path to prd.archived.json (checked for already-tracked PRs)",
    )
    parser.add_argument("--pr", type=int, help="Only consider this PR number")
    parser.add_argument(
        "--state",
        default="open",
        choices=["open", "closed", "merged", "all"],
        help="PR state to sync (default: open)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be added, write nothing",
    )
    args = parser.parse_args()

    prs = fetch_bot_prs(pr_number=args.pr, state=args.state)

    # Read and write are one critical section, so a concurrent run's status
    # update cannot be erased by our write. The GitHub fetch above stays
    # outside it — see the same note in add-backlog-to-prd.py.
    with prd_lock(args.prd):
        prd = load_json(args.prd)
        if prd is None:
            print(f"Error: {args.prd} does not exist", file=sys.stderr)
            return 2
        archived = load_json(args.archived_prd)
        known = tracked_pr_numbers(prd, archived)

        stories = prd.setdefault("stories", [])
        original_stories = copy.deepcopy(stories)
        existing_count = len(stories)

        max_id = 0
        max_priority = 0
        for story in stories:
            try:
                id_num = int(story["id"].split("-")[1])
            except (KeyError, IndexError, ValueError):
                id_num = 0
            max_id = max(max_id, id_num)
            max_priority = max(max_priority, story.get("priority") or 0)

        new_stories = []
        for pr in sorted(prs, key=lambda p: p["number"]):
            if pr["number"] in known:
                continue
            if pr.get("isDraft"):
                print(
                    f"  skipping draft PR #{pr['number']}: {pr['title']}",
                    file=sys.stderr,
                )
                continue
            max_id += 1
            max_priority += 1
            new_stories.append(build_pr_story(max_id, max_priority, pr))

        # SAFETY CHECK: existing stories must never be touched
        for i in range(existing_count):
            if stories[i] != original_stories[i]:
                print(
                    f"ERROR: Existing story {stories[i].get('id')} was modified!",
                    file=sys.stderr,
                )
                return 2

        if new_stories and not args.dry_run:
            stories.extend(new_stories)
            save_prd(args.prd, prd)

    verb = "Would add" if args.dry_run else "Added"
    print(
        f"{verb} {len(new_stories)} untracked bot PR(s) to the PRD "
        f"({len(prs)} bot PR(s) checked, {len(known)} already tracked)",
        file=sys.stderr,
    )
    for story in new_stories:
        print(
            f"  {story['id']}: PR #{story['prNumber']} — {story['title']}",
            file=sys.stderr,
        )

    print(
        json.dumps(
            {
                "added": [
                    {
                        "id": s["id"],
                        "prNumber": s["prNumber"],
                        "prUrl": s["prUrl"],
                        "title": s["title"],
                    }
                    for s in new_stories
                ],
                "checked": len(prs),
                "dryRun": args.dry_run,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
