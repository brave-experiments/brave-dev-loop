# Testing Requirements

## CRITICAL: Test Execution Requirements

**YOU MUST RUN ALL ACCEPTANCE CRITERIA TESTS - NO EXCEPTIONS**

**Exception: Filter-file-only changes** — If the ONLY changed files are test filter files (`test/filters/*.filter`), skip acceptance criteria tests entirely. No build is needed either. Proceed directly to presubmit (formatting and presubmit checks only).

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

Project-specific. See the project profile's `docs/testing.md` (path given in the prompt) for the sync/rebase recovery steps for this codebase.

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
  command: "<the project's build command>",
  run_in_background: true,
  timeout: 7200000,  // 2 hours
  description: "Build the project (may take a long time)"
})

// Later, check on the build with TaskOutput
TaskOutput({
  task_id: "task-xxx",  // Use the task ID returned from the background command
  block: true,
  timeout: 7200000
})

// Run tests in background
Bash({
  command: "<the project's test command>",
  run_in_background: true,
  timeout: 7200000,
  description: "Run the test suite (may take a long time)"
})
```

## Front-End Testing Requirements

Project-specific. See the project profile's `docs/testing.md` for front-end test commands.

## C++ Testing Best Practices

Project-specific. See the project profile's `docs/testing.md` (path given in the prompt) for the async/task-environment rules that apply to this codebase.

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

Project-specific. See the project profile's `docs/testing.md` (path given in the prompt) for filter-file conventions, specificity rules, upstream flake checks, and override guidance.

## Presubmit Requirements

Project-specific. See the project profile's `docs/testing.md` for the presubmit commands and order.

## Quality Requirements

- **ALL** acceptance criteria tests must pass - this is non-negotiable
- **Presubmit must pass** before creating a PR - run it after every commit
- Do NOT commit broken code
- Do NOT skip tests for any reason
- Keep changes focused and minimal
- Follow existing code patterns
- Report ALL test results in progress.txt
