# Testing Requirements

## CRITICAL: Test Execution Requirements

**YOU MUST RUN ALL ACCEPTANCE CRITERIA TESTS - NO EXCEPTIONS**

**Exception: Filter-file-only changes** — If the ONLY changed files are test filter files (`test/filters/*.filter`), skip acceptance criteria tests entirely. No build is needed either. Proceed directly to presubmit (formatting and presubmit checks only).

- **NEVER skip tests** because they "take too long" - this is NOT acceptable
- If tests take hours, that's expected - run them anyway
- Wait for a long check with `./scripts/wait-gate.sh`, never with `sleep N; grep logfile`
  (see [Waiting for a long check](#waiting-for-a-long-check))
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

## Waiting for a long check

An iteration cannot end its turn to wait — ending the turn ends the iteration — so
a check that outlasts a single tool call has to be waited on from inside one.
`./scripts/wait-gate.sh` is how:

```bash
# The simple case: run the gates and block until they answer.
./scripts/wait-gate.sh run --dir <worktree> "<build command>" "<test command>"

# The overlapping case: start them, do something else, then collect.
LOGS=$(./scripts/wait-gate.sh start --dir <worktree> "<test command>")
#   ... self-review the diff while the tests run ...
./scripts/wait-gate.sh wait "$LOGS"
```

It polls the gates every couple of seconds and returns when they return, reading
each verdict from the gate's own `EXIT=` line. Exit 0 all passed, 1 one failed,
2 the timeout expired with a gate still running — call `wait` again; the gates
are still going and nothing needs restarting.

**Never `sleep N; grep logfile`.** It was 13% of all the time the loop has ever
spent: the sleep costs whatever is left of it once the gate is done, a guess that
was short costs another turn, and averaged four minutes a poll to learn something
the gate knew already.

**Never read a check's result from the status of a pipeline.** The session shell
is zsh, which has no `pipefail`, so `make check 2>&1 | tail -40` reports *tail's*
exit code and a failing check looks green. A backgrounded
`make check > log 2>&1; echo "EXIT=$?"` hides it a second way — the notification
reports the echo's status. `wait-gate.sh` puts the echo inside the redirect for
this reason; where you must do it by hand, so should you.

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

- **ALL** acceptance criteria tests must pass - this is non-negotiable. A test that
  failed under load and passes when re-run on its own has passed; re-run it rather
  than abandoning the iteration, and name it in the PR body. See step 11 of
  [workflow-pending.md](./workflow-pending.md)
- **Presubmit must pass** before creating a PR - run it once, on the rebased tree,
  and commit that tree unchanged (workflow-pending.md step 9). Running it again on
  the tree it already passed on buys nothing
- Do NOT commit broken code
- Do NOT skip tests for any reason
- Keep changes focused and minimal
- Follow existing code patterns
- Report ALL test results in progress.txt
