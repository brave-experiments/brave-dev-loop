#!/usr/bin/env python3
import copy
import json
import os
import re
import subprocess
import sys

_script_dir = os.path.dirname(os.path.abspath(__file__))
_bot_dir = os.path.join(_script_dir, "..", "..", "..")
sys.path.insert(0, os.path.join(_bot_dir, "scripts"))
from lib.load_config import load_config, get_config, require_config

_config = load_config()
_issue_repo = require_config(_config, "project.issueRepository")
_project_name = require_config(_config, "project.name")
_bp_docs_dir = get_config(_config, "bestPractices.docsDir", ".")
_bp_index_file = get_config(_config, "bestPractices.indexFile", "best_practices.md")
_best_practices_path = os.path.join(_bp_docs_dir, _bp_index_file)


def find_test_location(test_class_name):
    """
    Determine if a test is a Brave test or Chromium test by running git grep.
    Returns 'brave' if found in src/brave, 'chromium' if found in src only, or 'unknown'.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    brave_dir = os.path.join(script_dir, "..", "..", "..", "..", "src", "brave")
    chromium_dir = os.path.join(script_dir, "..", "..", "..", "..", "src")

    try:
        result = subprocess.run(
            ["git", "grep", "-l", test_class_name],
            cwd=brave_dir,
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
            ["git", "grep", "-l", test_class_name, "--", ".", ":!brave"],
            cwd=chromium_dir,
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


# Read GitHub issues and existing PRD
if len(sys.argv) < 2:
    print(
        "Usage: cat github_issues.json | python3 update-prd-with-issues.py path/to/prd.json",
        file=sys.stderr,
    )
    sys.exit(1)

prd_path = sys.argv[1]

# Read GitHub issues from stdin
github_issues = json.loads(sys.stdin.read())

# Read existing PRD or create empty structure
if os.path.exists(prd_path):
    with open(prd_path, "r") as f:
        prd = json.load(f)
else:
    prd = {
        "projectName": f"{_project_name} Backlog",
        "description": f"Issues from {_issue_repo} repository to be resolved",
        "config": {},
        "stories": [],
    }

# Detect which key the PRD uses for stories
stories_key = "stories" if "stories" in prd else "stories"

# Create a deep copy of existing stories for verification
original_stories = copy.deepcopy(prd[stories_key])
existing_story_count = len(prd[stories_key])

# Extract existing issue numbers from PRD
existing_issues = set()
for story in prd[stories_key]:
    desc = story["description"]
    matches = re.findall(r"issue #(\d+)", desc)
    for match in matches:
        existing_issues.add(int(match))

# Find the highest existing ID number and priority
max_id = 0
max_priority = 0
for story in prd[stories_key]:
    id_num = int(story["id"].split("-")[1])
    if id_num > max_id:
        max_id = id_num
    if story["priority"] > max_priority:
        max_priority = story["priority"]

# Process each GitHub issue and add if missing
new_stories = []
for issue in github_issues:
    issue_num = issue["number"]

    # Skip if already in PRD
    if issue_num in existing_issues:
        continue

    max_id += 1
    max_priority += 1

    if is_disabled_test_issue(issue):
        story = build_disabled_test_story(max_id, max_priority, issue)
    elif is_test_issue(issue):
        story = build_test_story(max_id, max_priority, issue)
    else:
        story = build_generic_story(max_id, max_priority, issue)

    new_stories.append(story)

# SAFETY CHECK: Verify existing stories were not modified
for i in range(existing_story_count):
    if prd[stories_key][i] != original_stories[i]:
        print(
            f"ERROR: Existing story {prd[stories_key][i]['id']} was modified!",
            file=sys.stderr,
        )
        print(
            "This is a bug - existing stories should never be changed.", file=sys.stderr
        )
        sys.exit(1)

# Add new stories to PRD (appends to end, doesn't modify existing)
prd[stories_key].extend(new_stories)

# Output updated PRD
print(json.dumps(prd, indent=2))

# Print summary to stderr
print(f"\nAdded {len(new_stories)} new issues to PRD", file=sys.stderr)
for story in new_stories:
    issue_match = re.search(r"issue #(\d+)", story["description"])
    issue_num = issue_match.group(1) if issue_match else "unknown"
    title = story.get("testFilter", story["title"])
    print(f"  {story['id']}: {title} (#{issue_num})", file=sys.stderr)
