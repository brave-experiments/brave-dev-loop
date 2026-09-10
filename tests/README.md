# Test Suite

Automated tests for brave-dev-loop with proper exit codes.

## Running Tests

**Python unit tests** (pure logic, fast, no network):
```bash
cd brave-dev-loop
python3 -m pytest tests/
```

**Bash integration tests** (GitHub API, file structure, hooks):
```bash
cd brave-dev-loop
./tests/test-suite.sh
```

**Exit codes (test-suite.sh):**
- `0` = All tests passed
- `1` = One or more tests failed

## Test Categories

### 1. GitHub API Tests
- Verifies GitHub CLI is installed and authenticated
- Tests org membership checking with real API calls
- Confirms bbondy is a Brave org member
- Confirms non-members are correctly identified
- Validates org member list fetching

### 2. File Structure Tests
- Checks all required files exist
- Verifies scripts are executable
- Validates skill files are present

### 3. Filtering Script Tests
- Tests filter-issue-json.sh functionality
- Verifies org member cache creation
- Validates JSON and Markdown output formats
- Confirms cache contains expected members

### 4. Pre-commit Hook Tests
- Validates hook syntax
- Tests that the configured bot account is blocked from modifying dependencies
- Tests that non-bot accounts can modify dependencies
- Uses `sed` to replace `__BOT_USERNAME__` placeholder (simulates setup.sh behavior)
- Direct script execution tests (avoids global git hook interference)

### 5. Configuration Tests
- Validates data/prd.json is valid JSON
- Checks for required config sections
- Verifies stories array exists

## Test Output

Tests use color-coded output:
- ✓ **Green** = Test passed
- ✗ **Red** = Test failed
- ⊘ **Yellow** = Test skipped

## Continuous Integration

To use in CI/CD:

```bash
#!/bin/bash
cd brave-dev-loop
./tests/test-suite.sh

if [ $? -eq 0 ]; then
  echo "All tests passed!"
  exit 0
else
  echo "Tests failed!"
  exit 1
fi
```

## Adding New Tests

Add test functions following this pattern:

```bash
test_your_feature() {
  # Use assert helpers
  assert_success "description" command arg1 arg2
  assert_failure "description" command that should fail
  assert_contains "description" "expected string" "$output"
  assert_file_exists "description" "/path/to/file"
  assert_executable "description" "/path/to/script"

  # Or manual assertion
  TESTS_RUN=$((TESTS_RUN + 1))
  if [ condition ]; then
    echo -e "${GREEN}✓${NC} PASS: test name"
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
  else
    echo -e "${RED}✗${NC} FAIL: test name"
    TESTS_FAILED=$((TESTS_FAILED + 1))
    FAILED_TESTS+=("test name")
    return 1
  fi
}
```

Then call it in `run_all_tests()`:

```bash
run_all_tests() {
  echo "=== Your Category ==="
  test_your_feature
  echo ""

  # ... other tests ...
}
```

## Common Issues

### "data/prd.json is not valid JSON"
Your data/prd.json has a syntax error. Validate it:
```bash
jq . data/prd.json
```

### "GitHub CLI not authenticated"
Authenticate with GitHub:
```bash
gh auth login
```

### "Org member tests failing"
Ensure you have access to query the Brave organization:
```bash
gh api orgs/brave/members
```

### Global git hooks interfering
Some environments have global git hooks (like git-secrets) that can interfere with pre-commit hook tests. The test suite runs hooks directly to avoid this, but be aware if you see unexpected behavior.

## Test Coverage

Current test coverage:
- ✅ GitHub API authentication and org membership checks
- ✅ Real API calls (no mocking - validates actual GitHub integration)
- ✅ File structure and permissions
- ✅ Filtering scripts with cache validation
- ✅ Pre-commit hook logic for bot and regular users
- ✅ Configuration file validation

## Performance

Test suite typically completes in 5-10 seconds:
- GitHub API calls: ~2-3 seconds (with caching)
- File checks: <1 second
- Hook tests: ~2-3 seconds
- Configuration tests: <1 second

## Concurrency

`test_concurrency.py` covers run slots, claims and serialized PRD writes. It
spawns real processes and kills them, because a lock is only worth testing
against what actually happens to a run.

`sim/sim.sh` builds a throwaway deployment in `/tmp/botsim` — a copy of this
repo with a fake agent binary and a dummy PRD — so `./run.sh` can be run for
real, several times at once, without spending tokens or touching anything:

```bash
tests/sim/sim.sh build      # create it
tests/sim/sim.sh run 2 2    # 2 concurrent runs, 2 iterations each
tests/sim/sim.sh status
tests/sim/sim.sh clean
```
