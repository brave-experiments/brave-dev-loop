# Status: "pending" (Development)

**Goal: Implement and test the story**

## Implementation Steps

1. **IMPORTANT**: All git operations must be done in `[targetRepoPath from bot config]` directory

2. **CRITICAL BRANCH MANAGEMENT**:
   - Change to the git repo: `cd [targetRepoPath from bot config]`
   - Checkout master: `git checkout master`
   - Pull latest changes: `git pull origin master`

   **Check if story already has a branch:**
   - If story has `branchName` field with a value: Use that existing branch (`git checkout <branchName>`)
   - If story has NO `branchName` or it's null: Create NEW branch following naming convention below
   - Store the branch name:
     ```bash
     python3 $BOT_DIR/scripts/update-prd-status.py set-branch <story-id> --branch <branch-name>
     ```

   **Branch Naming Format:**
   - Pattern: `fix-<descriptive-name-in-kebab-case>`
   - Example: `fix-solana-provider-test`, `fix-ai-chat-task-ui`
   - Derive from story title, keeping it concise and descriptive
   - Max length: 50 characters
   - Only lowercase letters, numbers, and hyphens
   - No prefixes like `bot/` or `user/` (keep simple)

   **If branch name already exists remotely:**
   - This indicates a previous incomplete attempt
   - Story should have `branchName` field set - use that instead
   - If push fails due to existing branch, check story's `branchName` field

   **NEVER create a new branch if one already exists for this story!**

3. **Check for existing pull requests**

   Before starting implementation, verify that no one else (including other bots) has already put up a PR for this issue:

   **Extract issue number from story:**
   - Look at the story's `description` field for the issue number (e.g., "issue #50022")
   - Look at the first item in `acceptanceCriteria` (usually "Fetch issue #XXXXX details...")
   - Extract the numeric issue number

   **Check for linked or related pull requests:**
   ```bash
   # Search for PRs that reference this issue number
   gh pr list --repo $ISSUE_REPO --search "<issue-number>" --json number,title,state,url,author
   ```

   **Also check the PR repository (where PRs are created):**
   ```bash
   gh pr list --repo $PR_REPO --search "<issue-number>" --json number,title,state,url,author
   ```

   **Analyze the results:**

   **⚠️ CRITICAL: Special Handling for Intermittent Test Failures**

   If this story is fixing an intermittent test failure (has `testFilter` field or description mentions flaky/intermittent test), apply these rules INSTEAD of the normal duplicate PR logic:

   1. **If the GitHub issue is still OPEN** → **DO NOT SKIP** (assume re-occurrence)
      - An open issue means the test is still failing intermittently
      - Previous fix attempts may not have addressed the root cause
      - Proceed with implementation (continue to step 4)

   2. **Only skip as duplicate if ALL of these are true:**
      - There is a merged PR that attempted to fix this test
      - The GitHub issue was created BEFORE that PR was merged (check timestamps)
      - No new test failures reported since the PR merge date (check issue comments/activity)
      - The Chromium version in the issue matches or is older than the fix version

   3. **How to check timestamps:**
      ```bash
      # Get issue creation date
      gh issue view <issue-number> --repo $ISSUE_REPO --json createdAt

      # Get PR merge date (if merged)
      gh pr view <pr-number> --repo $PR_REPO --json mergedAt

      # If issue.createdAt > pr.mergedAt, this is a RE-OCCURRENCE → work on it
      # If issue.createdAt < pr.mergedAt AND no new comments after merge → may be duplicate
      ```

   4. **When in doubt, DO NOT SKIP** - intermittent tests often have multiple root causes

   ---

   **For NON-test-failure stories (normal duplicate check):**

   - If an **open** PR exists that clearly addresses this issue:
     - Check the PR author and description to confirm it's for the same issue
     - Update the story status:
       ```bash
       python3 $BOT_DIR/scripts/update-prd-status.py skipped <story-id> --reason "PR #XXXXX already exists for this issue"
       ```
     - Document in `$BOT_DIR/data/progress.txt` that you found an existing PR
     - **Post a comment on the GitHub issue** (if not already commented):
       ```bash
       gh issue comment <issue-number> --repo $ISSUE_REPO --body "$(cat <<'EOF'
       This issue is already being addressed by PR #XXXXX (in the PR repository).

       Skipping duplicate work.
       EOF
       )"
       ```
     - **END THE ITERATION** - Move to next story

   - If a **closed/merged** PR exists:
     - The issue might already be fixed
     - Verify if the issue is still open or if it was properly closed
     - If the issue is still open despite a merged PR, proceed with investigation

   - If **no PR exists** or only unrelated PRs were found:
     - Proceed with implementation (continue to step 4)

4. **Research previous fix attempts**

   Before implementing, search for previous PRs that attempted to fix this same issue (including closed/failed attempts):

   ```bash
   # Search for closed/merged PRs that reference this issue
   gh api search/issues --method GET \
     -f q="repo:$PR_REPO is:pr <issue-number> OR <test-name-or-keywords>" \
     --jq '.items[] | {number, title, state, html_url, user: .user.login}'
   ```

   **If previous attempts are found:**
   - Gather the diffs from those PRs to understand what was tried:
     ```bash
     gh pr diff <pr-number> --repo $PR_REPO
     ```
   - Read any review comments to understand why the approach may have failed
   - Use this context to inform your approach - avoid repeating failed strategies

   **This research is especially important for:**
   - Intermittent test failures (often have multiple fix attempts)
   - Issues that have been open for a long time
   - Issues with significant discussion threads

5. **Implement the user story**

   **CRITICAL: Investigate Production Code First (Test Failures)**

   When fixing intermittent test failures, you MUST investigate the production code being tested BEFORE looking at the test code. Intermittent failures often reveal real bugs — race conditions, missing synchronization, incorrect state management — in the code under test. Do not default to "fix the test."

   **Mandatory investigation steps for test failure stories:**
   1. **Read the production code** that the failing test exercises. Understand what it does and how it handles concurrency, state, and edge cases.
   2. **Determine if the test is catching a real bug.** If the production code has a race condition, missing lock, incorrect assumption, or other defect, fix the production code — not the test.
   3. **Only modify test code if the test itself is wrong** — e.g., incorrect assertions, missing waits for genuinely async operations, wrong test setup. A test that intermittently catches a real bug is a *good* test.
   4. **Both may need fixes** — sometimes the production code has a bug AND the test has a separate issue.

   The fix can be in:
   - **Production code** (the code being tested) - if the implementation has a real bug
   - **Test code** (the test itself) - if the test has bugs, incorrect assumptions, or is testing the wrong thing
   - **Both** - sometimes both the implementation and test need corrections

   **If you cannot determine whether the bug is in production code or test code, default to investigating the production code more deeply.** Do not take the easy path of changing the test.

   **CRITICAL: Minimal Change Scope**

   Only make changes that are **directly necessary** to fix the issue. Do NOT:
   - **Rename existing methods, variables, or classes** unless the rename itself is the fix
   - **Move methods or functions** between files/classes/threads unless directly required — if a method already handles cross-thread calls properly, don't move it
   - **Refactor surrounding code** — "cleanup" changes clutter the diff and make review harder
   - **Add unnecessary abstractions, helpers, or wrappers** around existing working code

   Every changed line must be **directly justified** by the fix. If a reviewer would ask "why was this changed?" and the answer isn't the fix itself, don't change it. Unnecessary changes obscure the important ones and make it harder to verify correctness.

   **SCOPE CHANGES TO YOUR PR ONLY**: All code changes must be confined to the current story's feature branch and PR. Do not make changes to other branches, other PRs, or unrelated code outside the scope of the current task, unless presubmit or format requires the changes.

6. **CRITICAL**: Run **ALL** acceptance criteria tests - **YOU MUST NOT SKIP ANY**

   **Exception: Filter-file-only changes** — If the ONLY changed files are test filter files (`test/filters/*.filter`), skip this step entirely. Do not run tests or build. Proceed to step 9 (self-review).

   See [testing-requirements.md](./testing-requirements.md) for complete test execution requirements.

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

9. **REQUIRED: Self-review using the target repo's `/review` skill (local mode):**

   **CRITICAL: Do NOT read best practices docs in the main context — they are 1000+ lines each and will fill the context window, causing compaction. The review skill handles this via chunked parallel subagents.**

   After implementing and passing tests, run the `/review` skill from the target repo in local mode to self-review your changes. This checks best practices (via auto-discovery and chunked subagents), root cause analysis quality, timing-based fix detection, and more.

   ### How to run

   1. `cd [targetRepoPath from bot config]`
   2. Read the review skill instructions at `[targetRepoPath]/.claude/skills/review/SKILL.md`
   3. Follow the **Local Mode** steps (Steps L1–L3, then Common Analysis Steps 3–9)
   4. **Fix all violations automatically** — do not prompt for confirmation, treat every validated violation as "fix all"
   5. If the review verdict is **FAIL**, fix the issues and re-run the review until it passes

   ### What to skip from the review skill

   - Skip Step 10 (Generate Review Report) — you don't need to produce a formatted report for yourself
   - Skip any "post to GitHub" steps — this is a self-review, not a PR review
   - Skip Step 2 (Research Previous Fix Attempts) — you already did this in step 4 above

   ### What to do with results

   - **If no violations after validation:** Proceed to commit
   - **If violations found:** Fix them, re-run only the affected chunks to confirm the fix
   - This step is mandatory — it catches issues that are easy to miss when focused on implementation. Do NOT skip it.

10. **If ALL tests pass:**
   - Commit ALL changes (must be in `[targetRepoPath from bot config]`)
   - **IMPORTANT**: If fixing security-sensitive issues (XSS, CSRF, buffer overflows, sanitizer issues, etc.), use discretion in commit messages - see [SECURITY.md](../SECURITY.md#public-security-messaging) for guidance
   - **For Chromium test disables (filter file modifications)**: If you detected this is a Chromium test in step 7, include in commit message:
     - State clearly that it's a **Chromium test** (e.g., "Disable Chromium test..." or "This is an upstream Chromium test...")
     - If Chromium has also disabled it, mention that explicitly (e.g., "Chromium has also disabled this test" or "Already disabled upstream")
     - If Brave modifications might be related, mention what was found (e.g., "Brave modifies chrome/browser/ui/ via chromium_src")

11. **CRITICAL: Run presubmit verification AFTER commit, BEFORE creating PR:**

   After committing, you MUST run the full verification cycle to ensure the commit is valid:
   ```bash
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
   6. ALL acceptance criteria tests (skip for filter-file-only changes)

   This ensures the final committed state is fully verified. Do NOT create a PR until all checks pass on the final committed state.

12. **Once all verifications pass:**
   - Update the PRD status:
     ```bash
     python3 $BOT_DIR/scripts/update-prd-status.py committed <story-id> --branch <branch-name>
     ```
   - Append your progress to `$BOT_DIR/data/progress.txt` (see [progress-reporting.md](./progress-reporting.md))
   - **Continue in same iteration:** Do NOT mark story as checked yet - proceed immediately to push and create PR (see [workflow-committed.md](./workflow-committed.md))

13. **If ANY tests fail:**
   - DO NOT commit changes
   - Keep `status: "pending"`
   - Keep `branchName` (so we can continue on same branch next iteration)
   - Document failure in `$BOT_DIR/data/progress.txt`
   - **END THE ITERATION** - Stop processing

## Retry Policy for Persistent Failures

If a story keeps failing across multiple iterations:

1. **First Failure**: Document the failure, keep trying
2. **Second Failure**: Analyze root cause more deeply, try different approach
3. **Third+ Failure**: **Step back and reconsider the strategy entirely**

### Strategic Reconsideration (Third+ Failure)

When multiple attempts have failed, consider whether the fundamental approach is wrong:

**Review previous attempts:**
- Use `gh api` to search for any previous PRs that attempted this fix (see step 4)
- Gather diffs from those PRs to understand what has already been tried
- Read review comments and discussions for insights into why approaches failed

**Question your assumptions:**
- If using polling, waiting, or timing-based approaches: Will the expected state ever actually occur?
- Could the intermittent behavior indicate a real underlying problem rather than a test issue?
- Is there a race condition that no amount of waiting will reliably fix?

**Consider alternative approaches (for Brave code):**
- **Refactor the test approach**: If a browser test is flaky, could the same functionality be verified with a more reliable unit test?
- **Fix the underlying code**: Sometimes the test is revealing a real bug in the production code
- **Add proper synchronization**: If there's a race condition, add explicit signaling rather than waits

**Last resort - disable with full documentation:**
If no fix is viable after thorough investigation, you may create a PR to disable the test, but you MUST:
- For Chromium tests (not Brave-specific tests): run `python3 $TARGET_REPO/script/check-upstream-flake.py "<TestName>"` and include the results
- Document all previous fix attempts (including PRs by others)
- Explain why each approach failed
- Describe the fundamental issue that makes the test unfixable
- Confirm no other options remain for making the test reliable
- Include upstream flake rate in the filter file comment if the test is a known upstream flake

**Update progress.txt with:**
- What was tried across all attempts
- Why the current strategy isn't working
- What blockers exist (missing dependencies, environment issues, fundamental design issues, etc.)
- Mark story with: "BLOCKED - Requires manual intervention" if no path forward

**Important**: Count failures per implementation approach, not just per iteration. If you try a completely different fix strategy, that's a new attempt.

The goal is to avoid infinite loops on impossible tasks while still giving sufficient retry attempts for legitimate failures or initial misunderstandings.

## Problem-Solving Approach

**CRITICAL: No Workarounds or Band-Aids**

- **NEVER use workarounds** - Every fix must address the root cause
- **NEVER add arbitrary waits or sleep statements** - If you think you need a wait, you don't understand the problem
- **NEVER make changes that "fix" the test by altering execution timing** - This is the most common type of fake fix. The problem disappears locally but the race condition still exists and will inevitably return. Examples include (but are not limited to):
  - Adding logging, console.log(), or debug output
  - Adding meaningless operations (variable assignments, loops, function calls)
  - Reordering unrelated code
  - Adding includes or forward declarations that change compilation order
  - Refactoring code in ways that accidentally change execution order
  - **ANY change where you cannot explain the synchronization mechanism it provides**
- **If your "fix" works but you can't explain WHY it addresses the race condition, it's not a real fix**
- **Understand the problem deeply** before attempting a fix:
  - Read relevant code thoroughly
  - Understand the data flow and control flow
  - Identify the actual root cause, not just symptoms
  - **Determine whether the issue is in production code, test code, or both** - don't assume the test is always at fault. Intermittent test failures frequently indicate real bugs in production code (race conditions, missing synchronization, incorrect state). Investigate the production code FIRST.
- **Fixes must be high-confidence solutions** that address the core issue
- If you cannot understand the root cause with high confidence, keep the story as `status: "pending"` and document why
- Temporary hacks or arbitrary timing adjustments are NOT acceptable solutions

## Update CLAUDE.md Files

Before committing, check if any edited files have learnings worth preserving in nearby CLAUDE.md files:

1. **Identify directories with edited files** - Look at which directories you modified
2. **Check for existing CLAUDE.md** - Look for CLAUDE.md in those directories or parent directories
3. **Add valuable learnings** - If you discovered something future developers/agents should know:
   - API patterns or conventions specific to that module
   - Gotchas or non-obvious requirements
   - Dependencies between files
   - Testing approaches for that area
   - Configuration or environment requirements

**Examples of good CLAUDE.md additions:**
- "When modifying X, also update Y to keep them in sync"
- "This module uses pattern Z for all API calls"
- "Tests require the dev server running on PORT 3000"
- "Field names must match the template exactly"

**Do NOT add:**
- Story-specific implementation details
- Temporary debugging notes
- Information already in progress.txt

Only update CLAUDE.md if you have **genuinely reusable knowledge** that would help future work in that directory.

## Consolidate Patterns

See **[docs/learnable-patterns.md](./learnable-patterns.md)** for the complete guide on identifying and capturing reusable patterns, including how to use `progress.txt` for lightweight patterns and when to create documentation PRs.

## Browser Testing (If Available)

For any story that changes UI, verify it works in the browser if you have browser testing tools configured (e.g., via MCP):

1. Navigate to the relevant page
2. Verify the UI changes work as expected
3. Take a screenshot if helpful for the progress log

If no browser tools are available, note in your progress report that manual browser verification is needed.
