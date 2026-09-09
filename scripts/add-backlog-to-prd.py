#!/usr/bin/env python3
"""Add stories for open issues assigned to the bot that aren't in prd.json.

Fetches every open issue assigned to `bot.username` in
`project.issueRepository` and appends a story for each one the PRD doesn't
already reference. Existing stories are never modified.

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
from lib.load_config import get_config, load_config, require_config

_config = load_config()
_issue_repo = require_config(_config, "project.issueRepository")
_bot_user = require_config(_config, "bot.username")
_project_name = require_config(_config, "project.name")
_bp_docs_dir = get_config(_config, "bestPractices.docsDir", ".")
_bp_index_file = get_config(_config, "bestPractices.indexFile", "best_practices.md")
_best_practices_path = os.path.join(_bp_docs_dir, _bp_index_file)

ISSUE_FIELDS = "number,title,url,labels"


def target_repo_dir():
    """Absolute path of the target repo, or None when it isn't configured.

    Relative `project.targetRepoPath` values resolve against the bot repo's
    parent directory, the same way run.sh resolves them.
    """
    path = get_config(_config, "project.targetRepoPath", "")
    if not path:
        return None
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(_bot_dir), path)
    return os.path.normpath(path)


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


def has_label(issue, label_name):
    """Check if an issue has a specific label."""
    labels = issue.get("labels", [])
    for label in labels:
        name = label.get("name", "") if isinstance(label, dict) else str(label)
        if name == label_name:
            return True
    return False


def is_test_issue(issue):
    """Check if an issue is a test failure based on title or labels."""
    if issue["title"].startswith("Test failure: "):
        return True
    if has_label(issue, "bot/type/test"):
        return True
    return False


def is_disabled_test_issue(issue):
    """Check if an issue is about a disabled test based on title or labels."""
    title = issue["title"].lower()
    if title.startswith("disabled test:"):
        return True
    if has_label(issue, "disabled-brave-test"):
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
        test_binary = "brave_unit_tests" if test_location == "brave" else "unit_tests"
    else:
        test_type = "browser_test"
        test_binary = (
            "brave_browser_tests" if test_location == "brave" else "browser_tests"
        )

    acceptance_criteria = [
        f"Read {_best_practices_path} to identify which best practice sub-documents apply, then read those sub-documents",
        f"Fetch issue #{issue_num} details from {_issue_repo} GitHub API",
        "Analyze stack trace and identify root cause - determine whether this is a real bug in production code, a test-only issue, or both. Read the production code being tested, not just the test. If the test is catching a genuine bug, fix the production code",
        "Implement fix targeting the correct layer (production code, test code, or both)",
        "Build the project (must pass)",
        "Format the code (must pass)",
        "Commit changes, then run the /review skill from the target repo in a fresh subagent (read .claude/skills/review/SKILL.md and follow Local Mode steps); report all findings back to the main context; fix any violations and commit the fixes (must pass)",
        f"Run the test: {test_binary} --gtest_filter={test_name} (must pass - run 5 times to verify consistency, unless this is a filter file change only)",
        "Run presubmit checks (must pass)",
        "Run pnpm run format one final time; if it makes any changes, amend the last commit with the formatting fixes",
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
        test_binary = "brave_unit_tests" if test_location == "brave" else "unit_tests"
    else:
        test_type = "browser_test"
        test_binary = (
            "brave_browser_tests" if test_location == "brave" else "browser_tests"
        )

    acceptance_criteria = [
        f"Read {_best_practices_path} to identify which best practice sub-documents apply, then read those sub-documents",
        f"Fetch issue #{issue_num} details from {_issue_repo} GitHub API",
        f"Find where the test is disabled by searching for DISABLED_{extract_disabled_search_term(test_name)} in the source code using git grep",
        "Use git blame on the line that disables the test to find the commit that disabled it, and read the commit message to understand WHY it was disabled",
        "Investigate whether the original reason for disabling has been resolved (e.g., upstream fix landed, dependency updated, flaky infrastructure fixed)",
        "If the underlying issue is fixed: re-enable the test by removing the DISABLED_ prefix. If the issue is NOT yet fixed: fix the root cause first, then re-enable the test",
        "Build the project (must pass)",
        "Format the code (must pass)",
        "Commit changes, then run the /review skill from the target repo in a fresh subagent (read .claude/skills/review/SKILL.md and follow Local Mode steps); report all findings back to the main context; fix any violations and commit the fixes (must pass)",
        f"Run the test: {test_binary} --gtest_filter={test_name} (must pass - run 5 times to verify consistency)",
        "Run presubmit checks (must pass)",
        "Run pnpm run format one final time; if it makes any changes, amend the last commit with the formatting fixes",
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
        f"Read {_best_practices_path} to identify which best practice sub-documents apply, then read those sub-documents",
        f"Fetch issue #{issue_num} details from {_issue_repo} GitHub API",
        "Analyze the issue and identify what needs to change",
        "Implement the fix or feature",
        "Build the project (must pass)",
        "Format the code (must pass)",
        "Commit changes, then run the /review skill from the target repo in a fresh subagent (read .claude/skills/review/SKILL.md and follow Local Mode steps); report all findings back to the main context; fix any violations and commit the fixes (must pass)",
        "Find and run relevant tests to verify the change (must pass)",
        "Run presubmit checks (must pass)",
        "Run pnpm run format one final time; if it makes any changes, amend the last commit with the formatting fixes",
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
        return build_disabled_test_story(story_id, priority, issue)
    if is_test_issue(issue):
        return build_test_story(story_id, priority, issue)
    return build_generic_story(story_id, priority, issue)


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

    prd = load_json(args.prd)
    if prd is None:
        prd = empty_prd()
    archived = load_json(args.archived_prd)

    if args.issues_file:
        issues = read_issues_file(args.issues_file)
    else:
        issues = fetch_assigned_issues()

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
        tmp_path = args.prd + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(prd, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, args.prd)

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
                    }
                    for s in new_stories
                ],
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
