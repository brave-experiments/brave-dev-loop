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
  assert_file_exists "pre-push hook exists" "$ROOT_DIR/hooks/pre-push"
}

test_scripts_executable() {
  assert_executable "run.sh is executable" "$ROOT_DIR/run.sh"
  assert_executable "setup.sh is executable" "$ROOT_DIR/scripts/setup.sh"
  assert_executable "pre-commit hook is executable" "$ROOT_DIR/hooks/pre-commit"
  assert_executable "pre-push hook is executable" "$ROOT_DIR/hooks/pre-push"
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
# Pre-push Hook Tests
#######################

# A scratch repo with the pre-push hook installed and one commit, echoed as its path.
# $1 is the user.name to configure, which is what decides whether the hook does anything at all.
make_prepush_repo() {
  local git_user="$1" test_dir
  test_dir=$(mktemp -d)

  git -C "$test_dir" init -q
  git -C "$test_dir" config user.name "$git_user"
  git -C "$test_dir" config user.email "$git_user@example.com"

  sed 's/__BOT_USERNAME__/testbot/g' "$ROOT_DIR/hooks/pre-push" > "$test_dir/.git/hooks/pre-push"
  chmod +x "$test_dir/.git/hooks/pre-push"

  printf 'x\n' > "$test_dir/file.txt"
  git -C "$test_dir" add file.txt
  git -C "$test_dir" commit -q -m "test"

  printf '%s\n' "$test_dir"
}

# Run an installed pre-push hook the way git would: from the work tree, with a ref line on stdin.
# $2 is the local sha to report, defaulting to HEAD.
run_prepush_hook() {
  local test_dir="$1" local_sha="${2:-}"
  [ -n "$local_sha" ] || local_sha=$(git -C "$test_dir" rev-parse HEAD)

  ( cd "$test_dir" &&
    printf 'refs/heads/main %s refs/heads/main %s\n' "$local_sha" "$(printf '0%.0s' {1..40})" |
      GH_TOKEN=stub bash .git/hooks/pre-push origin git@github.com:x/y.git ) 2>&1
}

test_prepush_hook_syntax() {
  assert_success "Pre-push hook has valid syntax" \
    bash -n "$ROOT_DIR/hooks/pre-push"
}

test_prepush_hook_ignores_non_bot() {
  # The reason this hook can ship to a repo other people clone: a checkout whose user.name is not
  # the bot's must be unaffected by it, including one nobody ran setup.sh against.
  local test_dir
  test_dir=$(make_prepush_repo "regularuser")

  local result=0
  run_prepush_hook "$test_dir" > /dev/null 2>&1 || result=$?
  rm -rf "$test_dir"

  TESTS_RUN=$((TESTS_RUN + 1))
  if [ $result -eq 0 ]; then
    echo -e "${GREEN}✓${NC} PASS: Pre-push hook allows a push from a non-bot account"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
  else
    echo -e "${RED}✗${NC} FAIL: Pre-push hook blocked a non-bot account (exit code: $result)"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    FAILED_TESTS+=("Pre-push hook allows a push from a non-bot account")
    return 1
  fi
}

test_prepush_hook_allows_bot_commit() {
  local test_dir
  test_dir=$(make_prepush_repo "testbot")

  local result=0
  run_prepush_hook "$test_dir" > /dev/null 2>&1 || result=$?
  rm -rf "$test_dir"

  TESTS_RUN=$((TESTS_RUN + 1))
  if [ $result -eq 0 ]; then
    echo -e "${GREEN}✓${NC} PASS: Pre-push hook allows a commit authored by the bot"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
  else
    echo -e "${RED}✗${NC} FAIL: Pre-push hook blocked the bot's own commit (exit code: $result)"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    FAILED_TESTS+=("Pre-push hook allows a commit authored by the bot")
    return 1
  fi
}

test_prepush_hook_blocks_foreign_commit() {
  # The failure this exists for: a repo configured as the bot, pushing a commit the machine owner
  # authored.
  local test_dir
  test_dir=$(make_prepush_repo "testbot")

  printf 'y\n' > "$test_dir/other.txt"
  git -C "$test_dir" add other.txt
  git -C "$test_dir" -c user.name="Machine Owner" -c user.email="owner@example.com" \
    commit -q -m "not the bot"

  local output
  output=$(run_prepush_hook "$test_dir" || true)
  local result=0
  run_prepush_hook "$test_dir" > /dev/null 2>&1 || result=$?
  rm -rf "$test_dir"

  TESTS_RUN=$((TESTS_RUN + 1))
  if [ $result -ne 0 ] && echo "$output" | grep -q "Machine Owner"; then
    echo -e "${GREEN}✓${NC} PASS: Pre-push hook blocks a commit authored by somebody else"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
  else
    echo -e "${RED}✗${NC} FAIL: Pre-push hook did not block a foreign commit (exit code: $result)"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    FAILED_TESTS+=("Pre-push hook blocks a commit authored by somebody else")
    return 1
  fi
}

test_prepush_hook_allows_branch_deletion() {
  # An all-zero local sha creates no commits, so there is nothing to attribute and nothing to fail
  # on. Reading it as a commit-ish would abort every `git push --delete`.
  local test_dir
  test_dir=$(make_prepush_repo "testbot")

  local result=0
  run_prepush_hook "$test_dir" "$(printf '0%.0s' {1..40})" > /dev/null 2>&1 || result=$?
  rm -rf "$test_dir"

  TESTS_RUN=$((TESTS_RUN + 1))
  if [ $result -eq 0 ]; then
    echo -e "${GREEN}✓${NC} PASS: Pre-push hook allows a branch deletion"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
  else
    echo -e "${RED}✗${NC} FAIL: Pre-push hook blocked a branch deletion (exit code: $result)"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    FAILED_TESTS+=("Pre-push hook allows a branch deletion")
    return 1
  fi
}

#######################
# Hook Installation Tests
#######################

test_hooks_dir_follows_hookspath() {
  # core.hooksPath replaces .git/hooks rather than adding to it, so a hook installed at the latter
  # in a repo that sets the former never runs and never says so.
  local test_dir output
  test_dir=$(mktemp -d)
  git -C "$test_dir" init -q
  git -C "$test_dir" config core.hooksPath .githooks

  ( source "$ROOT_DIR/scripts/lib/repo-hooks.sh" && repo_hooks_dir "$test_dir" ) > /dev/null 2>&1
  output=$( source "$ROOT_DIR/scripts/lib/repo-hooks.sh" && repo_hooks_dir "$test_dir" )
  rm -rf "$test_dir"

  assert_contains "Hooks directory follows core.hooksPath" "/.githooks" "$output"
}

test_hook_install_refuses_tracked_hook() {
  # A target repo with its own checked-in hook of the same name: overwriting it would destroy
  # committed work and show up as a modified tracked file in somebody's checkout.
  local test_dir result=0
  test_dir=$(mktemp -d)
  git -C "$test_dir" init -q
  git -C "$test_dir" config user.name "testbot"
  git -C "$test_dir" config user.email "testbot@example.com"
  git -C "$test_dir" config core.hooksPath .githooks

  mkdir -p "$test_dir/.githooks"
  printf '#!/bin/sh\nexit 0\n' > "$test_dir/.githooks/pre-commit"
  chmod +x "$test_dir/.githooks/pre-commit"
  git -C "$test_dir" add -f .githooks/pre-commit
  git -C "$test_dir" commit -q -m "own hook"

  ( source "$ROOT_DIR/scripts/lib/repo-hooks.sh" &&
    repo_install_hook "$test_dir" "$ROOT_DIR/hooks/pre-commit" pre-commit testbot ) > /dev/null 2>&1 ||
    result=$?

  local still_theirs=1
  grep -q "^exit 0$" "$test_dir/.githooks/pre-commit" && still_theirs=0
  rm -rf "$test_dir"

  TESTS_RUN=$((TESTS_RUN + 1))
  if [ $result -ne 0 ] && [ $still_theirs -eq 0 ]; then
    echo -e "${GREEN}✓${NC} PASS: Hook install refuses to overwrite a hook the target repo tracks"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
  else
    echo -e "${RED}✗${NC} FAIL: Hook install overwrote a tracked hook (exit code: $result)"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    FAILED_TESTS+=("Hook install refuses to overwrite a hook the target repo tracks")
    return 1
  fi
}

test_hook_install_leaves_status_clean() {
  # An in-tree hooks directory means the installed file sits among version-controlled ones, where it
  # would otherwise show up as untracked in the target repo.
  local test_dir status
  test_dir=$(mktemp -d)
  git -C "$test_dir" init -q
  git -C "$test_dir" config user.name "testbot"
  git -C "$test_dir" config user.email "testbot@example.com"
  git -C "$test_dir" config core.hooksPath .githooks
  git -C "$test_dir" commit -q --allow-empty -m "init"

  ( source "$ROOT_DIR/scripts/lib/repo-hooks.sh" &&
    repo_install_hook "$test_dir" "$ROOT_DIR/hooks/pre-commit" pre-commit testbot ) > /dev/null 2>&1

  status=$(git -C "$test_dir" status --porcelain)
  rm -rf "$test_dir"

  TESTS_RUN=$((TESTS_RUN + 1))
  if [ -z "$status" ]; then
    echo -e "${GREEN}✓${NC} PASS: An in-tree installed hook does not show up in git status"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
  else
    echo -e "${RED}✗${NC} FAIL: Installed hook left the target repo dirty ($status)"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    FAILED_TESTS+=("An in-tree installed hook does not show up in git status")
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

  echo "=== Pre-push Hook Tests ==="
  test_prepush_hook_syntax
  test_prepush_hook_ignores_non_bot
  test_prepush_hook_allows_bot_commit
  test_prepush_hook_blocks_foreign_commit
  test_prepush_hook_allows_branch_deletion
  echo ""

  echo "=== Hook Installation Tests ==="
  test_hooks_dir_follows_hookspath
  test_hook_install_refuses_tracked_hook
  test_hook_install_leaves_status_clean
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
