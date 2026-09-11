# brave-core: Testing

Project-specific testing rules for a Chromium-derived checkout. Read this
alongside `docs/testing-requirements.md`, which holds the project-neutral rules.

## Disabling Tests via Filter Files

When a test must be disabled (e.g., upstream Chromium test incompatible with Brave infrastructure), **use the most specific filter file possible**.

### FIRST: Rule out an infrastructure / build / CI root cause

**Do NOT disable a test to work around a broken build or CI environment.** A failure caused by a stale build (wrong revision compiled, stale generated files/buildflags, builder- or cache-specific failure, a landed config not taking effect) is a DevOps regression, not a test problem. Disabling the test hides it. If you see any of those signals, keep the story `pending`, ping the bot owner with the evidence, and END THE ITERATION — see [workflow-pending.md](./workflow-pending.md) step 5 ("Infrastructure / Build / CI Root Cause"). Only disable a test when the root cause is genuinely in the code/test and unfixable, not in the environment that built them.

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


## Chromium Test Detection (filter file modifications)

Applies to the `pending` workflow, step 7.

7. **CHROMIUM TEST DETECTION** (for filter file modifications only):

   If your fix involves adding a test to a filter file (e.g., `test/filters/browser_tests.filter`), determine if it's a Chromium test:

   **Detection Logic:**
   - Look at which test file the test is defined in:
     - If the test is in `./src/brave/chromium_src/**` or `./src/**` but NOT in `./src/brave/**`:
       - This is a **Chromium test** (upstream test that Brave inherits from Chromium)
     - If the test is in `./src/brave/**` (excluding chromium_src):
       - This is a **Brave test** (Brave-specific test)

   **For Chromium Tests - Additional Verification:**

   1. **Check if Chromium has already disabled this test:**
      ```bash
      # Search in upstream Chromium source for the test being disabled or marked flaky
      cd [workingDirectory]/..
      # Check for DISABLED_ prefix
      git grep "DISABLED_<TestName>" chromium/src/
      # Check Chromium's test expectations/filter files
      git grep "<TestName>" chromium/src/testing/buildbot/filters/
      ```
      - If found: **Document that Chromium has also disabled this test**
      - If not found: Note that this is a Brave-specific disable of a Chromium test

   2. **Verify Brave modifications aren't causing the failure:**
      - Extract the directory path of the test file (e.g., if test is in `./src/chrome/browser/ui/test.cc`, directory is `chrome/browser/ui/`)
      - Check if there are Brave-specific modifications in `./src/brave/chromium_src/` for files in that directory:
        ```bash
        # Example: If test is in chrome/browser/ui/tabs/test.cc
        find ./src/brave/chromium_src/chrome/browser/ui/ -type f 2>/dev/null | head -20
        ```
      - If Brave modifications exist in related directories, analyze whether they could be causing the test failure
      - Document findings - this helps determine if the test fails due to Brave changes or is an upstream issue

   3. **Check upstream flakiness data (Chromium tests only):**
      This step only applies to upstream Chromium tests — skip it for Brave-specific tests (defined in `src/brave/`), which will not appear in the Chromium database.
      ```bash
      python3 $TARGET_REPO/script/check-upstream-flake.py "<TestClassName.TestMethod>"
      ```
      - If the verdict is "Known upstream flake" or "Occasional upstream failures":
        Document this finding in the filter file comment and commit message
      - If the verdict is "Stable upstream":
        Investigate Brave-specific causes before disabling
      - Include the flake rate and lookback period in your documentation

   **Store Detection Results for Commit Message and PR:**
   - Make note of whether this is a **Chromium test** or **Brave test**
   - Note whether **Chromium has also disabled it** (include evidence)
   - Note any **Brave modifications** in related code paths
   - This information will be used for commit message and later for PR body

8. Update CLAUDE.md files if you discover reusable patterns (see below)

## PR body fields for an upstream test disable

Applies to the `committed` workflow. The body still takes the four sections in
[pr-descriptions.md](../../../docs/pr-descriptions.md) — these fields go *inside*
them, they do not add sections of their own.

**`## The problem`** — name the test and what it does to people, not just that it
fails: which CI jobs go red, on which platforms and build types, and whether it
blocks merges or just adds noise. If it is a Chromium test, say so in one line
here: **this is an upstream Chromium test** (defined in `./src/`, not
`./src/brave/`), so Brave inherits the failure rather than causing it.

**`## Reproduce`** — a test disable almost never reproduces on a laptop, and that
is the case the `Not reproducible locally:` line exists for. Give the build type
and platform, and link the CI job showing the failure:

```markdown
## Reproduce
Not reproducible locally: fails only on Windows ASAN official builds.
https://ci.brave.com/job/<...> — `WatermarkSettingsCommandLineBrowserTest.GetColors`,
red on the last 14 consecutive runs.
```

If you *can* reproduce it, give the command instead — that is strictly better. A
disable touches only test and filter files, so the command is the whole
reproduction here; pass `--test-only-change` to `check-pr-body.py` and it will
not ask for the user-facing steps that a product bug needs.

**`## The fix`** — why this filter file and this pattern, and the root cause in
plain language. Then the findings from Chromium Test Detection (step 7 above), as
a short list:

- **Chromium upstream status**: [Chromium has also disabled this test / Chromium has not disabled this test / Evidence of upstream bug: crbug.com/XXXXX]
- **Brave modifications**: [Brave does not modify this code area / Brave has modifications in ./src/brave/chromium_src/[path] that may affect this test]
- **Upstream flake rate**: [N% over the last 30 days per LUCI Analysis, from `check-upstream-flake.py` / not applicable, Brave-specific test]

A disable is a judgement call a reviewer has to agree with, so the evidence that
it is upstream's problem and not ours is the substance of the PR — it earns its
space above `<details>`.

## C++ Testing (Chromium APIs)

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

## Why the CI-checks rule exists

Real failure this rule exists to prevent: PR #38603 was rebased onto a Chromium 152 roll in which `Browser::profile()` had been removed. The branch stopped compiling (`error: no member named 'profile' in 'Browser'`). The bot never looked at the checks and posted three owner escalations across two weeks asking for a CI re-run, while a compile failure was already sitting on the PR.

## Commit message fields for an upstream test disable

- **For Chromium test disables (filter file modifications)**: If you detected this is a Chromium test in step 7, include in commit message:
     - State clearly that it's a **Chromium test** (e.g., "Disable Chromium test..." or "This is an upstream Chromium test...")
     - If Chromium has also disabled it, mention that explicitly (e.g., "Chromium has also disabled this test" or "Already disabled upstream")
     - If Brave modifications might be related, mention what was found (e.g., "Brave modifies chrome/browser/ui/ via chromium_src")

## Upstream flake check

- For Chromium tests (not Brave-specific tests): run `python3 $TARGET_REPO/script/check-upstream-flake.py "<TestName>"` and include the results


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
git fetch upstream
git rebase upstream/master
pnpm run sync --no-history
```
Then retry the build.


## Front-End Testing Requirements

**When changes include `.ts`, `.tsx`, or `.js` files, you MUST run:**

```bash
cd [targetRepoPath from bot config]
pnpm run test-unit      # Run front-end unit tests
pnpm run build-storybook  # Verify Storybook builds successfully
```

Both must pass before committing. These are in addition to any C++ tests or other acceptance criteria tests.

---

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


## Presubmit command sequence (pending workflow, step 11)

cd [targetRepoPath from bot config]
   pnpm run format      # Check/fix formatting
   pnpm run presubmit   # Run presubmit checks
   pnpm run gn_check    # Verify GN configuration (skip for filter-file-only changes)
   pnpm run build       # Verify build succeeds (skip for filter-file-only changes)
   # If any .ts/.tsx/.js files were changed:
   pnpm run test-unit        # Run front-end unit tests
   pnpm run build-storybook  # Verify Storybook builds
   ```

   **For filter-file-only changes** (only `test/filters/*.filter` modified): run only `pnpm run format` and `pnpm run presubmit`. Skip `gn_check`, `build`, and all test runs — filter files don't affect build configuration or compiled code.

   **If presubmit or any verification fails:**
   - Fix the issues
   - Stage and commit the fixes
   - **Re-run the ENTIRE verification cycle again** (format, presubmit, gn_check, build, tests)
   - Repeat until ALL verifications pass consecutively

   **IMPORTANT: Multiple iterations require full re-verification.** If you make ANY changes after initial commit (including formatting fixes, presubmit fixes, or any other modifications), you MUST re-run:
   1. `pnpm run format`
   2. `pnpm run presubmit`
   3. `pnpm run gn_check` (skip for filter-file-only changes)
   4. `pnpm run build` (skip for filter-file-only changes)
   5. If any `.ts`/`.tsx`/`.js` files changed: `pnpm run test-unit` and `pnpm run build-storybook`

## PR body verification checklist

- [x] Ran pnpm run format - passed
- [x] Ran pnpm run presubmit - passed
- [x] Ran pnpm run gn_check - passed
- [x] Ran pnpm run build - passed
- [x] Ran pnpm run test [test-name] - passed [N/N times]
