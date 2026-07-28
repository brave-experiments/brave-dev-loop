# Testing Requirements

## CRITICAL: Test Execution Requirements

**YOU MUST RUN ALL ACCEPTANCE CRITERIA TESTS - NO EXCEPTIONS**

**Exception: Filter-file-only changes** — If the ONLY changed files are test filter files (`test/filters/*.filter`), skip acceptance criteria tests entirely. No build is needed either. Proceed directly to presubmit (`pnpm run format` and `pnpm run presubmit` only).

- **NEVER skip tests** because they "take too long" - this is NOT acceptable
- If tests take hours, that's expected - run them anyway
- Use `run_in_background: true` for long-running commands (builds, test suites)
- Use high timeout values: `timeout: 3600000` (1 hour) or `timeout: 7200000` (2 hours)
- Monitor background tasks with TaskOutput tool
- If ANY test fails, the story does NOT complete - DO NOT update status to "committed"
- DO NOT commit code unless ALL acceptance criteria tests pass
- DO NOT rationalize skipping tests for any reason

## Headless / SSH Environments (Linux)

On Linux without a display server, browser tests will crash with errors like `kElementOffscreen`, X display errors, or `message_pump` DCHECKs. Use `xvfb-run` to provide a virtual display:

```bash
xvfb-run ./browser_tests --gtest_filter=YourTest.*
```

If `xvfb-run` is not available, install it with `sudo apt-get install xvfb`. On macOS, browser tests run natively without a virtual display.

## Test Scope: Run ALL Tests in File

**CRITICAL: When running tests, run ALL tests within the entire test file, not just a single test fixture.**

- If you modify or work with a test file, identify ALL test fixtures in that file
- Use `--gtest_filter` with colon-separated patterns to run ALL fixtures
- A single test file often contains multiple test fixtures (e.g., `FooTest`, `FooTestWithFeature`, `FooTestDisabled`)
- Running all fixtures in the file catches test interactions, shared state issues, and side effects
- Examples:
  - ❌ WRONG: `--gtest_filter=AdBlockServiceTest.OneCase` (single test case)
  - ❌ WRONG: `--gtest_filter=AdBlockServiceTest.*` (only one fixture)
  - ✅ CORRECT: `--gtest_filter=AdBlockServiceTest.*:AdBlockServiceTestWithFeature.*` (all fixtures in file)

**Process:**
1. Identify which test file(s) you're working with
2. Examine the file to find ALL test fixture names (all `TEST_F(FixtureName, ...)` declarations)
3. Build a gtest_filter that includes all fixtures: `Fixture1.*:Fixture2.*:Fixture3.*`
4. Run all fixtures together to ensure comprehensive testing

## Build Failure Recovery

**If `pnpm run build` fails**, first determine whether the failure is related to your changes or not.

### Failure unrelated to your changes
If the build error is in code you did not modify, run from `src/brave`:
```bash
cd src/brave
pnpm run sync --no-history
```
Then retry the build. **Only attempt this recovery once** — if the build still fails after syncing, do not repeat it. Investigate or report the issue.

### Failure related to your changes or unknown cause
Run these steps in order from `src/brave`:
```bash
cd src/brave
git fetch
git rebase origin/master
pnpm run sync --no-history
```
Then retry the build.

## ABSOLUTE RULE: No Test = No Pass

**IF YOU CANNOT RUN A TEST, THE STORY CANNOT BE MARKED AS PASSING. PERIOD.**

This means:
- ❌ "Test not runnable in local environment" → Story FAILS, keep status: "pending"
- ❌ "Feature not enabled in dev build" → Story FAILS, keep status: "pending"
- ❌ "Test environment not configured" → Story FAILS, keep status: "pending"
- ❌ "Test would take too long" → Story FAILS, keep status: "pending"
- ❌ "Fix addresses root cause but test can't verify" → Story FAILS, keep status: "pending"

**The ONLY acceptable outcome is:**
- ✅ Test runs AND passes → Update status to "committed"
- ❌ Test runs AND fails → Keep status: "pending", fix the issue
- ❌ Test cannot run for ANY reason → Keep status: "pending", document the blocker

**NO EXCEPTIONS. NO EXCUSES. NO RATIONALIZATIONS.**

If a test cannot be run, you must:
1. Document the exact blocker in progress.txt
2. Keep status: "pending"
3. Do NOT commit any changes
4. Move on to the next story

Only update status to "committed" when you have ACTUAL PROOF the test ran and passed.

## Example of Running Long Tests in Background

```javascript
// Start build in background
Bash({
  command: "cd brave && pnpm run build",
  run_in_background: true,
  timeout: 7200000,  // 2 hours
  description: "Build brave browser (may take 1-2 hours)"
})

// Later, check on the build with TaskOutput
TaskOutput({
  task_id: "task-xxx",  // Use the task ID returned from the background command
  block: true,
  timeout: 7200000
})

// Run tests in background
Bash({
  command: "cd brave && pnpm run test brave_browser_tests",
  run_in_background: true,
  timeout: 7200000,
  description: "Run brave_browser_tests (may take hours)"
})
```

## Front-End Testing Requirements

**When changes include `.ts`, `.tsx`, or `.js` files, you MUST run:**

```bash
cd [targetRepoPath from bot config]
pnpm run test-unit      # Run front-end unit tests
pnpm run build-storybook  # Verify Storybook builds successfully
```

Both must pass before committing. These are in addition to any C++ tests or other acceptance criteria tests.

---

## C++ Testing Best Practices (Chromium/Brave)

**CRITICAL: Follow these guidelines when writing C++ tests for Chromium/Brave codebase.**

**📖 READ FIRST:** Before implementing any test fixes, read best_practices.md (in `$TARGET_REPO/docs/`) for comprehensive async testing patterns, including:
- Avoiding nested run loops (EvalJs inside RunUntil)
- JavaScript evaluation patterns
- Navigation and timing issues
- Test isolation principles

### ❌ NEVER Use RunUntilIdle() - YOU MUST REPLACE IT

**DO NOT use `RunLoop::RunUntilIdle()` for asynchronous testing.**

This is explicitly forbidden by Chromium style guide because it causes flaky tests:
- May run too long and timeout
- May return too early if events depend on different task queues
- Creates unreliable, non-deterministic tests

**CRITICAL: If you find RunUntilIdle() in a test, DO NOT just delete it. You MUST replace it with one of the proper patterns below. Simply removing it will break the test because async operations won't complete.**

### ✅ REQUIRED: Replace RunUntilIdle() with These Patterns

When you encounter `RunLoop::RunUntilIdle()`, replace it with one of these approved patterns:

#### Option 1: TestFuture (PREFERRED for callbacks)

**BEFORE (WRONG):**
```cpp
object_under_test.DoSomethingAsync(callback);
task_environment_.RunUntilIdle();  // WRONG - causes flaky tests
```

**AFTER (CORRECT):**
```cpp
TestFuture<ResultType> future;
object_under_test.DoSomethingAsync(future.GetCallback());
const ResultType& actual_result = future.Get();  // Waits for callback
// Now you can assert on actual_result
```

#### Option 2: QuitClosure() + Run() (for manual control)

**BEFORE (WRONG):**
```cpp
object_under_test.DoSomethingAsync();
task_environment_.RunUntilIdle();  // WRONG
```

**AFTER (CORRECT):**
```cpp
base::RunLoop run_loop;
object_under_test.DoSomethingAsync(run_loop.QuitClosure());
run_loop.Run();  // Waits specifically for this closure
```

#### Option 3: RunLoop with explicit quit in observer/callback

**BEFORE (WRONG):**
```cpp
TriggerAsyncOperation();
task_environment_.RunUntilIdle();  // WRONG
```

**AFTER (CORRECT):**
```cpp
base::RunLoop run_loop;
auto quit_closure = run_loop.QuitClosure();
// Pass quit_closure to your observer or callback
// OR call std::move(quit_closure).Run() when operation completes
run_loop.Run();  // Waits for explicit quit
```

#### Option 4: base::test::RunUntil() (for condition-based waiting)

**BEFORE (WRONG):**
```cpp
TriggerAsyncOperation();
task_environment_.RunUntilIdle();  // WRONG - waits for all tasks
```

**AFTER (CORRECT):**
```cpp
int destroy_count = 0;
TriggerAsyncOperation();
EXPECT_TRUE(base::test::RunUntil([&]() { return destroy_count == 1; }));
// Waits for SPECIFIC condition to become true
```

**Use this when:** You need to wait for a specific state change that you can check with a boolean condition (e.g., counter reaches value, object becomes ready, child count changes).

**KEY POINT: Always wait for a SPECIFIC completion signal or condition, not just "all idle tasks".**

## Test Quality Standards

**Test in Isolation:**
- Use fakes rather than real dependencies
- Prevents cascading test failures
- Produces more maintainable, modular code

**Test the API, Not Implementation:**
- Focus on public interfaces
- Allows internal implementation changes without breaking tests
- Provides accurate usage examples for other developers

## Test Types & Purpose

**Unit Tests:** Test individual components in isolation. Should be fast and pinpoint exact failures.

**Integration Tests:** Test component interactions. Slower and more complex than unit tests.

**Browser Tests:** Run inside a browser process instance for UI testing.

**E2E Tests:** Run on actual hardware. Slowest but detect real-world issues.

## Common Patterns

**Friending Tests:** Use the `friend` keyword sparingly to access private members, but prefer testing public APIs first.

**Mojo Testing:** Reference "Stubbing Mojo Pipes" documentation for unit testing Mojo calls.

## Disabling Tests via Filter Files

When a test must be disabled (e.g., upstream Chromium test incompatible with Brave infrastructure), **use the most specific filter file possible**.

### Filter File Naming Convention

Filter files are located in `test/filters/` and follow the pattern:
```
{test_suite}-{platform}-{variant}.filter
```

### Specificity Levels (prefer most specific)

1. **Most specific**: `browser_tests-windows-asan.filter` - Platform + sanitizer
2. **Platform specific**: `browser_tests-windows.filter` - Single platform
3. **Least specific**: `browser_tests.filter` - All platforms (avoid if possible)

### Before Disabling a Test

1. **Identify which CI jobs fail** - Check issue labels (bot/platform/*, bot/arch/*) and CI job URLs
2. **Determine if failure is platform-specific** - e.g., Windows-only APIs, macOS behavior
3. **Determine if failure is build-type-specific** - e.g., ASAN/MSAN/UBSAN, OFFICIAL builds
4. **Check upstream flakiness data (Chromium tests only)** - This step only applies to upstream Chromium tests (tests defined in `src/` but NOT in `src/brave/`). It does not apply to Brave-specific tests. Check the LUCI Analysis database:
   ```bash
   python3 $TARGET_REPO/script/check-upstream-flake.py "<TestSuite.TestMethod>"
   ```
   This queries the Chromium CI flakiness database at `analysis.api.luci.app` and returns a verdict:
   - **Known upstream flake** (>=5% flake rate): Safe to filter. Include the flake rate in the filter comment.
   - **Occasional upstream failures** (1-5%): Consider filtering. Document the upstream instability.
   - **Stable upstream** (<1%): Test is stable in Chromium — investigate Brave-specific causes before disabling.
   - **Not found**: Test may be Brave-specific or use a different ID format. Check manually at https://ci.chromium.org/ui/p/chromium/test-search

   **Note:** Brave-specific tests (defined in `src/brave/`) will not appear in the Chromium upstream database. Do not run this check for Brave tests — it will return "Not found" which is expected and not useful.

   Use `--days 60` for a wider lookback window, or `--json` for machine-readable output.
5. **Choose the most specific filter file** - Create one if it doesn't exist

### Examples

| Failure Scope | Correct Filter File |
|---------------|---------------------|
| Windows ASAN only | `browser_tests-windows-asan.filter` |
| All Windows builds | `browser_tests-windows.filter` |
| Linux UBSAN only | `unit_tests-linux-ubsan.filter` |
| All platforms (Brave-specific) | `browser_tests.filter` |

### Supported Sanitizer Filters in `testUtils.js`

The `getApplicableFilters()` function in `build/commands/lib/testUtils.js` supports these sanitizer-specific filters:

| Sanitizer | Config Check | Example Filter File |
|-----------|-------------|---------------------|
| ASAN | `config.isAsan()` | `browser_tests-linux-asan.filter` |
| UBSAN | `config.is_ubsan` | `unit_tests-linux-ubsan.filter` |
| MSAN | `config.is_msan` | `browser_tests-linux-msan.filter` |

All three sanitizer types follow the same naming pattern: `{suite}-{platform}-{sanitizer}.filter`

### Red Flags (Overly Broad Disables)

- ❌ Adding to `browser_tests.filter` when failure only reported on one platform
- ❌ Adding to general filter when failure only on sanitizer builds (ASAN/MSAN/UBSAN)
- ❌ No investigation of which CI configurations actually fail
- ❌ Disabling an entire test suite or parameterized set when only specific test methods fail

### Filter Pattern Specificity

**CRITICAL: Disable only the specific tests that actually fail — never use overly broad patterns.**

When adding entries to filter files, match exactly the failing tests. Do not disable more tests than necessary.

| Scenario | Bad (too broad) | Good (specific) |
|----------|----------------|-----------------|
| One test method fails | `-MyTestSuite.*` | `-MyTestSuite.FailingMethod` |
| One parameterized instance fails | `-MyTestSuite.TestMethod/*` | `-MyTestSuite.TestMethod/FailingParam` |
| Two methods fail in a suite of 20 | `-MyTestSuite.*` | `-MyTestSuite.FailingA`<br>`-MyTestSuite.FailingB` |
| All parameterized instances fail | `-MyTestSuite.TestMethod/Param1`<br>`-MyTestSuite.TestMethod/Param2` | `-MyTestSuite.TestMethod/*` (OK here — all actually fail) |

**Rules:**
1. **List individual failing tests** — don't use wildcards to disable an entire fixture or parameterized set unless every test in it actually fails
2. **Verify scope before committing** — check the issue/CI logs to confirm exactly which test methods fail, not just the suite name
3. **Wildcards are only appropriate** when every test matching the pattern genuinely fails (e.g., all parameterized instances of a single method)
4. **When in doubt, be more specific** — it's better to list 5 individual test entries than to use one wildcard that also silently disables 15 passing tests

### Filter Entry Documentation

Always include a comment explaining:
1. **Why** the test is disabled
2. **What** specific condition causes the failure
3. **Why** this filter file was chosen (if not obvious)
4. **Upstream flakiness data** (if applicable) — include flake rate and lookback period from `check-upstream-flake.py`

```
# This test fails on Windows ASAN because ScopedInstallDetails defaults to
# STABLE channel, blocking command line switches that only work on non-STABLE.
# Windows-specific: ScopedInstallDetails only used on Windows.
# ASAN-specific: Only OFFICIAL builds return STABLE; non-OFFICIAL return UNKNOWN.
-WatermarkSettingsCommandLineBrowserTest.GetColors
```

```
# Known upstream flake: 1.8% flake rate over 30 days per LUCI Analysis.
# Mojo data pipe race condition — completion signal arrives before data is
# flushed through the consumer side.
-WebUIURLLoaderFactoryTest.RangeRequest/*
```

**Organization conventions:**

- **Group tests by shared root cause** - Tests that fail for the same reason go under one comment section
- **Specific reasons get their own section** - Don't add tests with detailed/specific failure reasons to generic catch-all sections (e.g., don't put a test with a specific race condition under "# Flaky upstream.")
- **Blank line before new comment sections** - When adding a new comment section, ensure there is an empty line separating it from the previous section's test entries. Do not place a comment line immediately after a test entry without a blank line in between.

Example: A test with a specific viewport race condition should not be grouped under "# Flaky upstream." - it needs its own section explaining that specific race condition.

## Disabled Test Investigations (Fixing / Re-enabling)

When investigating disabled tests — whether to fix, re-enable, or properly manage them — follow these guidelines.

### Prefer Filter Files Over chromium_src `#define DISABLED_` Overrides

Some chromium_src overrides disable upstream tests using the `#define TestName DISABLED_TestName` pattern, then include the original test file and add Brave-specific replacement tests. This pattern should be avoided. Instead:

1. **Disable upstream tests via filter files** — Add entries to the appropriate `test/filters/*.filter` file instead of using `#define DISABLED_` in a chromium_src override.
2. **Move Brave-specific tests to Brave test targets** — Any Brave replacement tests should live in the appropriate Brave test target (`brave_unit_tests`, `brave_browser_tests`, or `brave_components_unittests`), not inside a chromium_src override.
3. **Remove the chromium_src override** — Once the filter entries and Brave tests are relocated, delete the override file entirely.

**Real example — `chromium_src/chrome/browser/metrics/chrome_metrics_service_client_unittest.cc`:**

```cpp
// ❌ WRONG - chromium_src override that disables + replaces tests
#define TestRegisterUKMProviders DISABLED_TestRegisterUKMProviders
#define TestRegisterMetricsServiceProviders \
  DISABLED_TestRegisterMetricsServiceProviders

#include <chrome/browser/metrics/chrome_metrics_service_client_unittest.cc>

#undef TestRegisterMetricsServiceProviders
#undef TestRegisterUKMProviders

TEST_F(ChromeMetricsServiceClientTest, BraveTestRegisterUKMProviders) {
  // Brave-specific test...
}

TEST_F(ChromeMetricsServiceClientTest, BraveRegisterMetricsServiceProviders) {
  // Brave-specific test...
}
```

**✅ CORRECT approach — split into three changes:**

1. Add filter entries to disable the upstream tests:
```
# test/filters/unit_tests.filter
# Upstream test expects UKM providers; Brave disables UKM entirely.
-ChromeMetricsServiceClientTest.TestRegisterUKMProviders
-ChromeMetricsServiceClientTest.TestRegisterMetricsServiceProviders
```

2. Move Brave replacement tests to the appropriate Brave test target (e.g., `brave_unit_tests`):
```cpp
// brave/browser/metrics/chrome_metrics_service_client_unittest.cc
TEST_F(ChromeMetricsServiceClientTest, BraveTestRegisterUKMProviders) {
  // ...
}
TEST_F(ChromeMetricsServiceClientTest, BraveRegisterMetricsServiceProviders) {
  // ...
}
```

3. Delete the chromium_src override file.

**Why filter files are preferred over chromium_src `#define DISABLED_`:**

- **Visibility** — Filter files are centralized in `test/filters/` and easy to audit for re-enablement
- **Low maintenance** — No rebase conflicts when upstream renames or moves tests
- **Specificity** — Can target specific platforms or sanitizer builds
- **Reversibility** — Removing a filter line is trivial; chromium_src overrides require verifying nothing else depends on them

### Red Flags During Investigation

- ❌ A chromium_src override that uses `#define DISABLED_` — should be filter file entries
- ❌ Brave-specific tests living inside a chromium_src override — should be in a Brave test target
- ❌ Keeping a chromium_src override "just in case" when a filter file would suffice

See also: [chromium_src Overrides](./best-practices/chromium-src-overrides.md) for general override guidelines.

## Presubmit Requirements

**CRITICAL: Presubmit must pass BEFORE creating a pull request.**

After committing your changes, run the full verification cycle:

```bash
cd [targetRepoPath from bot config]
pnpm run format      # Check/fix formatting
pnpm run presubmit   # Run presubmit checks
pnpm run gn_check    # Verify GN configuration (skip for filter-file-only changes)
pnpm run build       # Verify build succeeds
# If any .ts/.tsx/.js files were changed:
pnpm run test-unit        # Run front-end unit tests
pnpm run build-storybook  # Verify Storybook builds
```

**For filter-file-only changes** (only `test/filters/*.filter` modified): run only `pnpm run format` and `pnpm run presubmit`. Skip `gn_check`, `build`, and all acceptance criteria test runs.

**If presubmit fails:**
1. Fix the issues identified by presubmit
2. Stage and commit the fixes
3. **Re-run the ENTIRE verification cycle** (format, presubmit, gn_check, build)
4. **Re-run ALL acceptance criteria tests**
5. Repeat until everything passes consecutively

**Why this matters:**
- Presubmit catches formatting issues, lint errors, and other problems before CI
- If you make changes after initial commit (including presubmit fixes), you must verify the new state
- The PR should only be created when the final committed state passes all checks
- Skipping this step leads to failed CI and wasted review cycles

**Multiple iterations = full re-verification:**

If you make ANY changes after the initial commit (formatting fixes, presubmit fixes, additional code changes), you MUST re-run:
1. `pnpm run format`
2. `pnpm run presubmit`
3. `pnpm run gn_check`
4. `pnpm run build`
5. ALL acceptance criteria tests

Do NOT create a PR until all verifications pass on the final committed state.

## Quality Requirements

- **ALL** acceptance criteria tests must pass - this is non-negotiable
- **Presubmit must pass** before creating a PR - run it after every commit
- Do NOT commit broken code
- Do NOT skip tests for any reason
- Keep changes focused and minimal
- Follow existing code patterns
- Report ALL test results in progress.txt
