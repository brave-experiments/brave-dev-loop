#!/usr/bin/env python3
"""Add stories for open issues assigned to the bot that aren't in prd.json.

Fetches every open issue assigned to `bot.username` in
`project.issueRepository` and appends a story for each one the PRD doesn't
already reference. The triage axes of a story still pending are brought up to
date with its issue's labels; nothing else about an existing story is modified.

This is the whole /add-backlog-to-prd sync — no LLM involved. The skill and
`make backlog` both call this script so there is one implementation.

Usage:
  scripts/add-backlog-to-prd.py                     # update data/prd.json in place
  scripts/add-backlog-to-prd.py --dry-run           # report only, write nothing
  scripts/add-backlog-to-prd.py --issues-file -     # read issue JSON from stdin

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
from lib import triage
from lib.load_config import (
    build_research,
    build_validations,
    get_config,
    load_config,
    load_profile,
    require_config,
    require_matching_profile,
    resolve_target_repo,
    test_binary,
    test_step,
)
from lib.prd_store import prd_lock, save_prd

_config = load_config()
_issue_repo = require_config(_config, "project.issueRepository")
_bot_user = require_config(_config, "bot.username")
_project_name = require_config(_config, "project.name")
_profile = load_profile(_config, _bot_dir)
_research = build_research(_profile, _config, _bot_dir)

ISSUE_FIELDS = "number,title,url,labels"


def target_repo_dir():
    """Absolute path of the target repo, or None when it isn't configured."""
    return resolve_target_repo(_config, _bot_dir)


def find_test_location(test_class_name):
    """
    Determine if a test lives in the target repo or in its parent checkout.
    Returns 'brave' if found in the target repo, 'chromium' if found only in
    the surrounding checkout, or 'unknown'.
    """
    target_dir = target_repo_dir()
    if not target_dir:
        return "unknown"
    parent_dir = os.path.dirname(target_dir)
    target_name = os.path.basename(target_dir)

    try:
        result = subprocess.run(
            ["git", "grep", "-l", test_class_name],
            cwd=target_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return "brave"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    try:
        result = subprocess.run(
            ["git", "grep", "-l", test_class_name, "--", ".", f":!{target_name}"],
            cwd=parent_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return "chromium"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return "unknown"


def label_names(issue):
    """Every label name on an issue, however the API spelled them."""
    names = []
    for label in issue.get("labels", []):
        names.append(label.get("name", "") if isinstance(label, dict) else str(label))
    return names


def has_label(issue, label_name):
    """Check if an issue has a specific label."""
    return label_name in label_names(issue)


def axis_prefixes():
    """Label prefix for each triage axis, from the project's profile.

    A project whose issues are not labelled this way defines none, and its
    stories carry no triage block at all.
    """
    return triage.axis_prefixes(_profile)


def read_triage(issue):
    """The issue's triage axes as {axis: 1..5}, omitting every unlabelled one."""
    return triage.read_labels(label_names(issue), axis_prefixes())


def is_test_issue(issue):
    """Check if an issue is a test failure based on title or labels."""
    if issue["title"].startswith("Test failure: "):
        return True
    if has_label(issue, "bot/type/test"):
        return True
    return False


def disabled_test_label():
    """Label marking an issue as a disabled test, or "" when the project has none.

    The profile owns it; `labels.disabledTestLabel` in config.json is honoured
    as a fallback so deployments that set it by hand keep working.
    """
    from_profile = (_profile.get("labels") or {}).get("disabledTest")
    if from_profile is not None:
        return from_profile
    return get_config(_config, "labels.disabledTestLabel", "") or ""


def is_disabled_test_issue(issue):
    """Check if an issue is about a disabled test based on title or labels."""
    title = issue["title"].lower()
    if title.startswith("disabled test:"):
        return True
    label = disabled_test_label()
    if label and has_label(issue, label):
        return True
    return False


def extract_disabled_test_name(title):
    """Extract test name from various disabled test issue title formats."""
    # "Disabled test: TestClass.TestMethod"
    m = re.match(r"^Disabled test:\s*(.+)$", title, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Backtick-quoted test name: "... `TestClass/TestClass.Method/Param` ..."
    m = re.search(r"`([^`]+)`", title)
    if m:
        return m.group(1).strip()

    # "Re-enable TestName test" or "Re-enable TestName"
    m = re.search(r"[Rr]e-?enable\s+(\S+?)(?:\s+test)?\.?\s*$", title)
    if m:
        return m.group(1).strip()

    # "Investigate failure of TestName"
    m = re.search(r"failure of\s+(\S+)", title)
    if m:
        return m.group(1).strip()

    # Fallback: return full title cleaned up
    return title.strip()


def extract_disabled_search_term(test_name):
    """Extract the method name to search for DISABLED_ prefix.
    Handles parameterized tests like TestClass/TestClass.Method/1 by
    stripping the parameter suffix and extracting the method name."""
    # Strip trailing /N parameter suffix (e.g., /1, /0, /SomeParam)
    name = re.sub(r"/\w+$", "", test_name) if "/" in test_name else test_name
    # Get the method part after the last dot
    if "." in name:
        return name.split(".")[-1]
    return name


def build_test_story(story_id, priority, issue):
    """Build a user story for a test failure issue."""
    issue_num = issue["number"]
    title = issue["title"]
    test_name = re.sub(r"^.*?failure:\s*", "", title, flags=re.IGNORECASE).strip()
    test_class_name = test_name.split(".")[0]
    test_location = find_test_location(test_class_name)

    if "AlternateTestParams" in test_name or "PartitionAlloc" in test_name:
        test_type = "unit_test"
        suite = "unit"
    else:
        test_type = "browser_test"
        suite = "browser"

    acceptance_criteria = [
        *_research,
        f"Fetch issue #{issue_num} details from {_issue_repo} GitHub API",
        "Analyze stack trace and identify root cause - determine whether this is a real bug in production code, a test-only issue, or both. Read the production code being tested, not just the test. If the test is catching a genuine bug, fix the production code",
        "Implement fix targeting the correct layer (production code, test code, or both)",
        *build_validations(
            _profile,
            test_step(
                _profile,
                "testFix",
                test_binary(_profile, suite, test_location),
                test_name,
            ),
        ),
    ]

    return {
        "id": f"US-{story_id:03d}",
        "title": f"Fix test: {test_name}",
        "description": f"As a developer, I need to fix the intermittent failure in {test_name} (issue #{issue_num}).",
        "testType": test_type,
        "testLocation": test_location,
        "testFilter": test_name,
        "acceptanceCriteria": acceptance_criteria,
        "priority": priority,
        "status": "pending",
        "prNumber": None,
        "lastActivityBy": None,
        "branchName": None,
        "prUrl": None,
    }


def build_disabled_test_story(story_id, priority, issue):
    """Build a user story for a disabled test issue."""
    issue_num = issue["number"]
    title = issue["title"]
    test_name = extract_disabled_test_name(title)

    # For parameterized tests like TestClass/TestClass.Method/1, use the class part
    class_part = (
        test_name.split("/")[0] if "/" in test_name else test_name.split(".")[0]
    )
    test_location = find_test_location(class_part)

    # Determine test type heuristic
    if (
        "UnitTest" in test_name
        or "AlternateTestParams" in test_name
        or "PartitionAlloc" in test_name
    ):
        test_type = "unit_test"
        suite = "unit"
    else:
        test_type = "browser_test"
        suite = "browser"

    acceptance_criteria = [
        *_research,
        f"Fetch issue #{issue_num} details from {_issue_repo} GitHub API",
        f"Find where the test is disabled by searching for DISABLED_{extract_disabled_search_term(test_name)} in the source code using git grep",
        "Use git blame on the line that disables the test to find the commit that disabled it, and read the commit message to understand WHY it was disabled",
        "Investigate whether the original reason for disabling has been resolved (e.g., upstream fix landed, dependency updated, flaky infrastructure fixed)",
        "If the underlying issue is fixed: re-enable the test by removing the DISABLED_ prefix. If the issue is NOT yet fixed: fix the root cause first, then re-enable the test",
        *build_validations(
            _profile,
            test_step(
                _profile,
                "disabledTest",
                test_binary(_profile, suite, test_location),
                test_name,
            ),
        ),
    ]

    return {
        "id": f"US-{story_id:03d}",
        "title": f"Re-enable disabled test: {test_name}",
        "description": f"As a developer, I need to investigate and re-enable the disabled test {test_name} (issue #{issue_num}). The test was previously disabled and needs to be investigated to determine if the underlying issue is resolved, then re-enabled.",
        "testType": test_type,
        "testLocation": test_location,
        "testFilter": test_name,
        "acceptanceCriteria": acceptance_criteria,
        "priority": priority,
        "status": "pending",
        "prNumber": None,
        "lastActivityBy": None,
        "branchName": None,
        "prUrl": None,
    }


def build_generic_story(story_id, priority, issue):
    """Build a user story for a non-test issue."""
    issue_num = issue["number"]
    title = issue["title"]

    acceptance_criteria = [
        *_research,
        f"Fetch issue #{issue_num} details from {_issue_repo} GitHub API",
        "Analyze the issue and identify what needs to change",
        "Implement the fix or feature",
        *build_validations(_profile, test_step(_profile, "generic")),
    ]

    return {
        "id": f"US-{story_id:03d}",
        "title": title,
        "description": f"Resolve issue #{issue_num}: {title}",
        "acceptanceCriteria": acceptance_criteria,
        "priority": priority,
        "status": "pending",
        "prNumber": None,
        "lastActivityBy": None,
        "branchName": None,
        "prUrl": None,
    }


def build_story(story_id, priority, issue):
    """Dispatch to the story builder that matches the issue type."""
    if is_disabled_test_issue(issue):
        story = build_disabled_test_story(story_id, priority, issue)
    elif is_test_issue(issue):
        story = build_test_story(story_id, priority, issue)
    else:
        story = build_generic_story(story_id, priority, issue)

    axes = read_triage(issue)
    if axes:
        story["triage"] = axes
    return story


def refresh_triage(stories, issues):
    """Bring every pending story's axes up to date with its issue's labels.

    The axes move after intake: somebody raises an urgency, or sizes an issue
    that arrived unjudged. A story ordered by the labels as they were the day it
    was created is ordered by nothing anybody can still see, and since this
    script is the only thing that reads them, the label would never reach the
    loop at all.

    Only pending work is touched. A story that has left "pending" has a PR, and
    its place in the queue is already decided by that PR rather than by the
    axes. Returns one entry per story whose triple actually moved.
    """
    if not axis_prefixes():
        return []
    by_number = {issue["number"]: issue for issue in issues}
    moved = []
    for story in stories:
        if story.get("status") != "pending":
            continue
        number = story_issue_number(story)
        issue = by_number.get(number)
        if issue is None:
            continue
        was = story.get("triage") or {}
        now = read_triage(issue)
        if now == was:
            continue
        if now:
            story["triage"] = now
        else:
            story.pop("triage", None)
        moved.append(
            {"id": story.get("id"), "issueNumber": number, "from": was, "to": now}
        )
    return moved


def without_triage(story):
    """A story minus its triage block, for the safety check below.

    The axes are the one field this script may rewrite on a story it did not
    create. Everything else belongs to the operator or to the workflow that is
    running the story, and overwriting any of it is what that check is for.
    """
    return {key: value for key, value in story.items() if key != "triage"}


def fetch_assigned_issues():
    """Fetch open issues assigned to the bot in the issue repository."""
    args = [
        "issue",
        "list",
        "--repo",
        _issue_repo,
        "--assignee",
        _bot_user,
        "--state",
        "open",
        "--json",
        ISSUE_FIELDS,
        "--limit",
        "100",
    ]
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


def read_issues_file(path):
    """Read issue JSON from a file, or from stdin when path is '-'."""
    try:
        raw = sys.stdin.read() if path == "-" else open(path).read()
        return json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Error reading issues from {path}: {e}", file=sys.stderr)
        sys.exit(2)


def load_json(path):
    """Load a JSON file, returning None if it doesn't exist."""
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading {path}: {e}", file=sys.stderr)
        sys.exit(2)


def story_issue_number(story):
    """Issue number a story references in its description, or None."""
    match = re.search(r"issue #(\d+)", story.get("description") or "")
    return int(match.group(1)) if match else None


def tracked_issue_numbers(*prds):
    """Collect every issue number already referenced by a story in any PRD."""
    numbers = set()
    for prd in prds:
        if not prd:
            continue
        for story in prd.get("stories", []):
            for match in re.findall(r"issue #(\d+)", story.get("description") or ""):
                numbers.add(int(match))
    return numbers


def empty_prd():
    """A fresh PRD skeleton, used when data/prd.json doesn't exist yet."""
    return {
        "projectName": f"{_project_name} Backlog",
        "description": f"Issues from {_issue_repo} repository to be resolved",
        "config": {},
        "stories": [],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Add stories for open issues assigned to the bot"
    )
    parser.add_argument(
        "--prd",
        default=os.path.join(_bot_dir, "data", "prd.json"),
        help="Path to prd.json (created if missing)",
    )
    parser.add_argument(
        "--archived-prd",
        default=os.path.join(_bot_dir, "data", "prd.archived.json"),
        help="Path to prd.archived.json (checked for already-tracked issues)",
    )
    parser.add_argument(
        "--issues-file",
        help="Read issue JSON from this file ('-' for stdin) instead of calling gh",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be added, write nothing",
    )
    args = parser.parse_args()
    # Acceptance criteria come from the profile, and a story keeps the ones
    # it was written with. Writing them from a profile nobody chose leaves
    # wrong criteria in the PRD long after the config is fixed.
    require_matching_profile(_config, _bot_dir)

    if args.issues_file:
        issues = read_issues_file(args.issues_file)
    else:
        issues = fetch_assigned_issues()

    # Reading the PRD and writing it back is one critical section: a status
    # update from a concurrent run landing between the two would be erased by
    # our write. The GitHub fetch above deliberately stays outside it —
    # holding the PRD lock across a network call would stall every run for as
    # long as GitHub takes to answer.
    with prd_lock(args.prd):
        prd = load_json(args.prd)
        if prd is None:
            prd = empty_prd()
        archived = load_json(args.archived_prd)
        known = tracked_issue_numbers(prd, archived)

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
        for issue in issues:
            if issue["number"] in known:
                continue
            max_id += 1
            max_priority += 1
            new_stories.append(build_story(max_id, max_priority, issue))

        retriaged = refresh_triage(stories, issues)

        # SAFETY CHECK: nothing about an existing story but its axes may change
        for i in range(existing_count):
            if without_triage(stories[i]) != without_triage(original_stories[i]):
                print(
                    f"ERROR: Existing story {stories[i].get('id')} was modified!",
                    file=sys.stderr,
                )
                return 2

        if (new_stories or retriaged) and not args.dry_run:
            stories.extend(new_stories)
            save_prd(args.prd, prd)

    verb = "Would add" if args.dry_run else "Added"
    print(
        f"{verb} {len(new_stories)} new issue(s) to the PRD "
        f"({len(issues)} assigned issue(s) checked, {len(known)} already tracked)",
        file=sys.stderr,
    )
    for story in new_stories:
        issue_num = story_issue_number(story) or "unknown"
        label = story.get("testFilter", story["title"])
        print(f"  {story['id']}: {label} (#{issue_num})", file=sys.stderr)

    if retriaged:
        verb = "Would re-triage" if args.dry_run else "Re-triaged"
        print(f"{verb} {len(retriaged)} pending story/stories", file=sys.stderr)
        for moved in retriaged:
            print(
                f"  {moved['id']} (#{moved['issueNumber']}): "
                f"{triage.format_triage(moved['from']) or 'unjudged'} -> "
                f"{triage.format_triage(moved['to']) or 'unjudged'}",
                file=sys.stderr,
            )

    print(
        json.dumps(
            {
                "added": [
                    {
                        "id": s["id"],
                        "issueNumber": story_issue_number(s),
                        "title": s["title"],
                        "status": s["status"],
                        "priority": s["priority"],
                        "triage": s.get("triage") or {},
                    }
                    for s in new_stories
                ],
                "retriaged": retriaged,
                "checked": len(issues),
                "alreadyTracked": len(known),
                "issueRepository": _issue_repo,
                "dryRun": args.dry_run,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
