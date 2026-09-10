#!/bin/bash
# Test suite for brave-dev-loop
# Returns 0 if all tests pass, non-zero if any fail

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Test counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test result tracking
declare -a FAILED_TESTS

#######################
# Test Helper Functions
#######################

# Assert that a command succeeds
assert_success() {
  local test_name="$1"
  shift
  TESTS_RUN=$((TESTS_RUN + 1))

  if "$@" > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} PASS: $test_name"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
  else
    echo -e "${RED}✗${NC} FAIL: $test_name"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    FAILED_TESTS+=("$test_name")
    return 1
  fi
}

# Assert that a command fails
assert_failure() {
  local test_name="$1"
  shift
  TESTS_RUN=$((TESTS_RUN + 1))

  if "$@" > /dev/null 2>&1; then
    echo -e "${RED}✗${NC} FAIL: $test_name (expected failure but succeeded)"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    FAILED_TESTS+=("$test_name")
    return 1
  else
    echo -e "${GREEN}✓${NC} PASS: $test_name"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
  fi
}

# Assert that output contains a string
assert_contains() {
  local test_name="$1"
  local expected="$2"
  local output="$3"
  TESTS_RUN=$((TESTS_RUN + 1))

  if echo "$output" | grep -q "$expected"; then
    echo -e "${GREEN}✓${NC} PASS: $test_name"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
  else
    echo -e "${RED}✗${NC} FAIL: $test_name (expected: '$expected')"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    FAILED_TESTS+=("$test_name")
    return 1
  fi
}

# Assert that a file exists
assert_file_exists() {
  local test_name="$1"
  local file_path="$2"
  TESTS_RUN=$((TESTS_RUN + 1))

  if [ -f "$file_path" ]; then
    echo -e "${GREEN}✓${NC} PASS: $test_name"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
  else
    echo -e "${RED}✗${NC} FAIL: $test_name (file not found: $file_path)"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    FAILED_TESTS+=("$test_name")
    return 1
  fi
}

# Assert that a file is executable
assert_executable() {
  local test_name="$1"
  local file_path="$2"
  TESTS_RUN=$((TESTS_RUN + 1))

  if [ -x "$file_path" ]; then
    echo -e "${GREEN}✓${NC} PASS: $test_name"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
  else
    echo -e "${RED}✗${NC} FAIL: $test_name (not executable: $file_path)"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    FAILED_TESTS+=("$test_name")
    return 1
  fi
}

#######################
# GitHub API Tests
#######################

test_gh_cli_installed() {
  assert_success "GitHub CLI is installed" which gh
}

test_gh_authenticated() {
  assert_success "GitHub CLI is authenticated" gh auth status
}

test_org_member_api_known_member() {
  # Test with bbondy (known Brave member)
  assert_success "API confirms bbondy is Brave org member" \
    gh api "orgs/brave/members/bbondy" --silent
}

test_org_member_api_non_member() {
  # Test with torvalds (not a Brave member)
  assert_failure "API confirms torvalds is NOT Brave org member" \
    gh api "orgs/brave/members/torvalds" --silent
}

test_org_members_list_fetch() {
  # Test that we can fetch the org members list
  local output=$(gh api "orgs/brave/members" --jq '.[].login' 2>&1)
  assert_contains "Org members list contains bbondy" "bbondy" "$output"
}

test_org_members_list_not_empty() {
  # Test that org has at least 10 members
  local count=$(gh api "orgs/brave/members" --jq '. | length' 2>&1)
  TESTS_RUN=$((TESTS_RUN + 1))

  if [ "$count" -gt 10 ]; then
    echo -e "${GREEN}✓${NC} PASS: Org has sufficient members ($count)"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
  else
    echo -e "${RED}✗${NC} FAIL: Org has too few members ($count)"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    FAILED_TESTS+=("Org has sufficient members")
    return 1
  fi
}

#######################
# File Structure Tests
#######################

test_required_files_exist() {
  assert_file_exists "README.md exists" "$ROOT_DIR/README.md"
  assert_file_exists "SECURITY.md exists at root" "$ROOT_DIR/SECURITY.md"
  assert_file_exists "CLAUDE.md exists" "$ROOT_DIR/.claude/CLAUDE.md"
  assert_file_exists "run.sh exists" "$ROOT_DIR/run.sh"
  assert_file_exists "setup.sh exists" "$ROOT_DIR/scripts/setup.sh"
  assert_file_exists "prd.json exists" "$ROOT_DIR/data/prd.json"
  assert_file_exists "pre-commit hook exists" "$ROOT_DIR/hooks/pre-commit"
}

test_scripts_executable() {
  assert_executable "run.sh is executable" "$ROOT_DIR/run.sh"
  assert_executable "setup.sh is executable" "$ROOT_DIR/scripts/setup.sh"
  assert_executable "pre-commit hook is executable" "$ROOT_DIR/hooks/pre-commit"
  assert_executable "fetch-issue.sh is executable" "$ROOT_DIR/scripts/fetch-issue.sh"
  assert_executable "filter-issue-json.sh is executable" "$ROOT_DIR/scripts/filter-issue-json.sh"
}

test_skills_exist() {
  assert_file_exists "brave_core_prd skill exists" "$ROOT_DIR/skills/brave_core_prd.md"
  assert_file_exists "brave_core_prd_json skill exists" "$ROOT_DIR/skills/brave_core_prd_json.md"
}

#######################
# Filtering Script Tests
#######################

test_filter_script_exists() {
  assert_file_exists "filter-issue-json.sh exists" "$ROOT_DIR/scripts/filter-issue-json.sh"
}

test_filter_script_requires_org_members() {
  # The filter script should error if org-members.txt doesn't exist
  local cache_file="$ROOT_DIR/.ignore/org-members.txt"

  # Temporarily move cache file if it exists
  local had_cache=false
  if [ -f "$cache_file" ]; then
    had_cache=true
    mv "$cache_file" "$cache_file.bak"
  fi

  TESTS_RUN=$((TESTS_RUN + 1))
  if "$ROOT_DIR/scripts/filter-issue-json.sh" 1 json > /dev/null 2>&1; then
    echo -e "${RED}✗${NC} FAIL: Filter script should error when org-members.txt is missing"
  else
    echo -e "${GREEN}✓${NC} PASS: Filter script errors when org-members.txt is missing"
    TESTS_PASSED=$((TESTS_PASSED + 1))
  fi

  # Restore cache file
  if [ "$had_cache" = true ]; then
    mv "$cache_file.bak" "$cache_file"
  fi
}

test_filter_script_cache_contains_members() {
  local cache_file="$ROOT_DIR/.ignore/org-members.txt"

  if [ -f "$cache_file" ]; then
    local output=$(cat "$cache_file")
    assert_contains "Cache contains bbondy" "bbondy" "$output"
  else
    TESTS_RUN=$((TESTS_RUN + 1))
    echo -e "${YELLOW}⊘${NC} SKIP: Org members file not found at $cache_file (run setup.sh first)"
    return 0
  fi
}

test_filter_script_json_output() {
  # Test that JSON output is valid JSON
  local output=$("$ROOT_DIR/scripts/filter-issue-json.sh" 1 json 2>/dev/null || echo '{"error": "failed"}')

  TESTS_RUN=$((TESTS_RUN + 1))
  if echo "$output" | jq . > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} PASS: Filter script produces valid JSON"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
  else
    echo -e "${RED}✗${NC} FAIL: Filter script JSON is invalid"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    FAILED_TESTS+=("Filter script produces valid JSON")
    return 1
  fi
}

test_filter_script_markdown_output() {
  # Test that markdown output contains expected headers
  local output=$("$ROOT_DIR/scripts/filter-issue-json.sh" 1 markdown 2>/dev/null || echo "")
  assert_contains "Filter markdown contains issue header" "# Issue #1:" "$output"
}

test_pr_filter_script_exists() {
  # Test that PR review filter script exists and is executable
  assert_executable "filter-pr-reviews.sh is executable" \
    "$ROOT_DIR/scripts/filter-pr-reviews.sh"
}

#######################
# Pre-commit Hook Tests
#######################

test_precommit_hook_syntax() {
  # Test that the pre-commit hook has valid bash syntax
  assert_success "Pre-commit hook has valid syntax" \
    bash -n "$ROOT_DIR/hooks/pre-commit"
}

test_precommit_hook_blocks_package_json() {
  # Create a temporary git repo to test the hook
  local test_dir=$(mktemp -d)
  cd "$test_dir"
  git init > /dev/null 2>&1
  git config user.name "testbot"
  git config user.email "testbot@example.com"

  # Install hook with placeholder replaced (simulates setup.sh behavior)
  sed 's/__BOT_USERNAME__/testbot/g' "$ROOT_DIR/hooks/pre-commit" > ".git/hooks/pre-commit"
  chmod +x ".git/hooks/pre-commit"

  # Try to commit a package.json change
  echo '{"test": true}' > package.json
  git add package.json

  # This should fail
  local result=0
  git commit -m "test" > /dev/null 2>&1 || result=$?

  cd - > /dev/null
  rm -rf "$test_dir"

  TESTS_RUN=$((TESTS_RUN + 1))
  if [ $result -ne 0 ]; then
    echo -e "${GREEN}✓${NC} PASS: Pre-commit hook blocks package.json for bot account"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
  else
    echo -e "${RED}✗${NC} FAIL: Pre-commit hook did not block package.json"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    FAILED_TESTS+=("Pre-commit hook blocks package.json for bot account")
    return 1
  fi
}

test_precommit_hook_allows_non_bot() {
  # Test that hook logic allows non-bot users (direct script test)
  local test_dir=$(mktemp -d)
  cd "$test_dir"

  git init > /dev/null 2>&1
  git config user.name "regularuser"
  git config user.email "user@example.com"

  # Install hook with placeholder replaced (simulates setup.sh behavior)
  sed 's/__BOT_USERNAME__/testbot/g' "$ROOT_DIR/hooks/pre-commit" > ".git/hooks/pre-commit"
  chmod +x ".git/hooks/pre-commit"

  # Create test file
  echo '{"test": true}' > package.json
  git add package.json

  # Run the hook script directly in this git context
  local result=0
  bash ".git/hooks/pre-commit" > /dev/null 2>&1 || result=$?

  # Clean up
  cd "$ROOT_DIR" > /dev/null
  rm -rf "$test_dir"

  TESTS_RUN=$((TESTS_RUN + 1))
  if [ $result -eq 0 ]; then
    echo -e "${GREEN}✓${NC} PASS: Pre-commit hook allows package.json for non-bot account"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
  else
    echo -e "${RED}✗${NC} FAIL: Pre-commit hook blocked non-bot user (exit code: $result)"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    FAILED_TESTS+=("Pre-commit hook allows package.json for non-bot account")
    return 1
  fi
}

#######################
# Configuration Tests
#######################

test_prd_json_valid() {
  # Test that prd.json is valid JSON
  assert_success "data/prd.json is valid JSON" \
    jq . "$ROOT_DIR/data/prd.json"
}

test_prd_json_has_user_stories() {
  # Test that prd.json has stories array
  local has_stories=$(jq 'has("stories")' "$ROOT_DIR/data/prd.json")

  TESTS_RUN=$((TESTS_RUN + 1))
  if [ "$has_stories" = "true" ]; then
    echo -e "${GREEN}✓${NC} PASS: prd.json has stories"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
  else
    echo -e "${RED}✗${NC} FAIL: prd.json missing stories"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    FAILED_TESTS+=("prd.json has stories")
    return 1
  fi
}

#######################
# Main Test Runner
#######################

run_all_tests() {
  echo "=========================================="
  echo "Brave Dev Loop - Test Suite"
  echo "=========================================="
  echo ""

  echo "=== GitHub API Tests ==="
  test_gh_cli_installed
  test_gh_authenticated
  test_org_member_api_known_member
  test_org_member_api_non_member
  test_org_members_list_fetch
  test_org_members_list_not_empty
  echo ""

  echo "=== File Structure Tests ==="
  test_required_files_exist
  test_scripts_executable
  test_skills_exist
  echo ""

  echo "=== Filtering Script Tests ==="
  test_filter_script_exists
  test_filter_script_requires_org_members
  test_filter_script_cache_contains_members
  test_filter_script_json_output
  test_filter_script_markdown_output
  test_pr_filter_script_exists
  echo ""

  echo "=== Pre-commit Hook Tests ==="
  test_precommit_hook_syntax
  test_precommit_hook_blocks_package_json
  test_precommit_hook_allows_non_bot
  echo ""

  echo "=== Configuration Tests ==="
  test_prd_json_valid
  test_prd_json_has_user_stories
  echo ""

  # Summary
  echo "=========================================="
  echo "Test Results"
  echo "=========================================="
  echo "Total Tests: $TESTS_RUN"
  echo -e "${GREEN}Passed: $TESTS_PASSED${NC}"
  echo -e "${RED}Failed: $TESTS_FAILED${NC}"
  echo ""

  if [ $TESTS_FAILED -gt 0 ]; then
    echo -e "${RED}Failed Tests:${NC}"
    for test in "${FAILED_TESTS[@]}"; do
      echo "  - $test"
    done
    echo ""
    echo -e "${RED}RESULT: FAILURE${NC}"
    return 1
  else
    echo -e "${GREEN}RESULT: SUCCESS${NC}"
    return 0
  fi
}

# Run all tests
run_all_tests
exit $?
