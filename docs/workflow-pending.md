# Status: "pending" (Development)

**Goal: Implement and test the story**

## Implementation Steps

1. **IMPORTANT**: All git operations must be done in `[targetRepoPath from bot config]` directory

   Project-specific: a profile may put each story in its own worktree instead — see the project profile's `docs/repo.md` (path given in the prompt). Read it before the first `cd`; where it applies, it replaces the checkout and branch steps below, and every later `[targetRepoPath from bot config]` in this doc means the worktree.

2. **CRITICAL BRANCH MANAGEMENT**:
   - Change to the git repo: `cd [targetRepoPath from bot config]`
   - Fetch the upstream default branch: `git fetch upstream`
   - A new branch starts from `upstream/master`, never `origin/master`: with
     `project.useFork` true, `origin` is the bot's fork and its default branch is only as
     current as the last push to it. See
     [git-repository.md](./git-repository.md#branch-management-for-each-user-story).

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
      - The upstream version in the issue matches or is older than the fix version

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

   **FIRST: reproduce the problem, and write down exactly how.**

   Before changing anything, make the problem happen and capture three things
   verbatim — you will need them for the PR body, and reconstructing them later
   from memory produces a description no reviewer can act on:

   1. **The steps**, in the order a person takes them. Where a user can see the
      bug, these are what a *user* does in the running product: the screen or
      URL, the setting, the input. Where it is a command, exactly as typed, and
      the directory it ran in.
   2. **What it did** — the failure output or the wrong state, trimmed to the
      lines that matter.
   3. **What it should have done instead.**

   Keep them somewhere you can paste from at PR time (the progress entry is the
   natural place — see [progress-reporting.md](./progress-reporting.md)). A test
   you add that fails on the parent commit and passes with the fix is the
   *evidence* a reviewer verifies with: record its name and both outcomes as
   well as the steps, not instead of them. Only when the change is test-only —
   a disable, a flaky-test fix, a harness change, nothing a user could ever
   observe — is the test the whole reproduction.

   If the problem genuinely cannot be reproduced in this environment — CI-only,
   platform-specific, needs the fleet — record that and the evidence you do have
   (the CI job URL, the crash report, the issue's logs). That is what the PR body
   will carry in place of steps, and it must link the evidence.

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

   **CRITICAL: Infrastructure / Build / CI Root Cause — STOP, do NOT write a PR**

   Before proposing ANY code change (including a test disable/skip), rule out that the root cause is the build or CI environment rather than the code. A test can fail because the thing under test was built wrong, not because the code or test is wrong. **A PR that skips or works around such a failure hides a real DevOps/infrastructure regression and is the wrong outcome.**

   **Signals the root cause is infrastructure, not code:**
   - The compiled binary or generated files reflect a **different source revision** than expected (e.g. a nightly built a SHA that predates a landed change; stale git mirror; wrong branch/tag).
   - Generated files (`gen/**/*.h`, `args.gn`, `args_generated.gni`, buildflags) contain **stale or unexpected values** that do not match current `master`.
   - The failure depends on **build type / builder / cache** (ASAN-only, one CI node only, disappears on a clean build) with no corresponding code difference.
   - A config/default that already landed in code is **not taking effect** in the built artifact.
   - The failure cannot be reproduced from a clean local build of the current source.

   **What to do when infrastructure is the (suspected) root cause:**
   1. **Do NOT create a PR** — not a fix PR, and especially not a PR that disables/skips/guards the test to make the failure "go away." Working around an infra bug in code is a band-aid that masks the real problem.
   2. **Gather the evidence** — the specific SHA/revision built, the stale generated file contents vs. expected, the builder/job URL, and why this points to build/CI rather than code.
   3. **Keep the story `status: "pending"`** and do NOT commit anything.
   4. **Ping the bot owner to investigate** (see below) — this class of failure needs a human with DevOps access to confirm and fix the infrastructure. The bot cannot re-run CI or fix build nodes itself.
   5. **Document the evidence and the ping in `$BOT_DIR/data/progress.txt`**, then END THE ITERATION.

   **How to ping the bot owner (infrastructure escalation):**
   - Read `project.botOwnerGithubHandle` from the bot config provided in the prompt.
   - **If set** (non-empty), post a comment on the GitHub issue @-mentioning the owner with the evidence and your hypothesis. Ask them to confirm whether it's an infra/CI/build problem before any code workaround is considered:
     ```
     @<botOwnerGithubHandle> This test failure looks like an infrastructure/build issue, not a code bug. Evidence:
     - <SHA/revision actually built vs. expected>
     - <stale generated file / value, with builder job URL>
     - <why this indicates CI/build rather than code>
     I'm holding off on any code change (including disabling the test) since a PR would just mask this. Can you confirm the root cause and whether this needs a DevOps fix? Reopen/redirect if I've misdiagnosed it.
     ```
     Replace `<botOwnerGithubHandle>` with the configured handle. Only ever mention this exact configured handle — never guess or invent a username (see the no-hallucinated-mentions rule in CLAUDE.md).
   - **If empty/absent**, do not @-mention anyone; document in progress.txt that the failure appears infrastructure-caused and no owner is configured to escalate to.

   **Only proceed to a code change when you have high confidence the root cause is in the code or the test itself — not the environment that built them.**

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

7. **UPSTREAM TEST DETECTION** (for filter file modifications only):

   Project-specific. See the project profile's `docs/testing.md` (path given in the prompt). Projects without an upstream to inherit tests from can skip this step.

9. **REQUIRED: Self-review your changes before committing:**

   **CRITICAL: Do NOT read best practices docs in the main context — they are 1000+ lines each and will fill the context window, causing compaction. A review skill handles this via chunked parallel subagents.**

   Which reviewer to use depends on what the target repo ships. Check before you
   assume: most target repos have no review skill of their own, and reaching for
   one that is not there wastes an iteration.

   ### If `[targetRepoPath]/.claude/skills/review/SKILL.md` exists

   Run it in local mode. It checks best practices (via auto-discovery and chunked subagents), root cause analysis quality, timing-based fix detection, and more.

   1. `cd [targetRepoPath from bot config]`
   2. Read the review skill instructions at `[targetRepoPath]/.claude/skills/review/SKILL.md`
   3. Follow the **Local Mode** steps (Steps L1–L3, then Common Analysis Steps 3–9)
   4. **Fix all violations automatically** — do not prompt for confirmation, treat every validated violation as "fix all"
   5. If the review verdict is **FAIL**, fix the issues and re-run the review until it passes

   What to skip from that skill:

   - Skip Step 10 (Generate Review Report) — you don't need to produce a formatted report for yourself
   - Skip any "post to GitHub" steps — this is a self-review, not a PR review
   - Skip Step 2 (Research Previous Fix Attempts) — you already did this in step 4 above

   ### If it does not

   Run the harness's own `/code-review` skill over the branch diff, and **give it
   the path the story works in**: `/code-review [targetRepoPath from bot config]`,
   or the story's worktree where the profile uses one. The skill reviews the
   session's working directory, which is the bot directory rather than the target
   repo, so `/code-review` with no path reviews *this* repository's diff and
   returns findings about the loop's own scripts and docs. Those findings look
   plausible, so check the scope it reports before acting on any of them: a review
   naming files your branch did not touch read the wrong tree, and none of it is
   about your change.

   For the project's rules, read what the target repo actually has — the profile's
   `docs/`, and the `AGENTS.md` or `CLAUDE.md` at the repo root — not a
   best-practices index the project may not ship.

   A finding you decline is one you have **verified** is wrong or pre-existing:
   check it against the code and say so in the PR description. Declining a
   finding because you disagree with it is not a review.

   ### What to do with results

   - **If no violations after validation:** Proceed to commit
   - **If violations found:** Fix them, re-run only the affected chunks to confirm the fix
   - This step is mandatory — it catches issues that are easy to miss when focused on implementation. Do NOT skip it.

10. **If ALL tests pass:**
   - Commit ALL changes (must be in `[targetRepoPath from bot config]`)
   - **IMPORTANT**: If fixing security-sensitive issues (XSS, CSRF, buffer overflows, sanitizer issues, etc.), use discretion in commit messages - see [SECURITY.md](../SECURITY.md#public-security-messaging) for guidance
   - **For upstream test disables**: see the project profile's `docs/testing.md` for the required commit-message fields.
11. **CRITICAL: Run presubmit verification AFTER commit, BEFORE creating PR:**

   **First, put the branch on current upstream.** CI builds the merge of the branch with
   the upstream default branch, not the commit that was pushed, so a gate that passes here
   can still fail there:

   ```bash
   git fetch upstream            # through ./scripts/git-repo-lock.sh where runs share this directory
   git rebase upstream/master    # the upstream default branch
   ```

   Do this before the first push, where a rebase is free, and run the presubmit sequence on
   the rebased tip: that is the tree CI will build. If the rebased tree fails on code this
   diff does not touch, check whether upstream is already red before changing anything —
   see [git-repository.md](./git-repository.md#ci-tests-the-merge-not-your-branch-tip).

   After committing, you MUST run the full verification cycle to ensure the commit is valid:
   ```bash
   Run the project's presubmit sequence. The exact commands and their order are
   project-specific — see the project profile's `docs/testing.md` (path given in
   the prompt). Filter-file-only changes may skip build and test steps.
   6. ALL acceptance criteria tests (skip for filter-file-only changes)

   This ensures the final committed state is fully verified. Do NOT create a PR until all checks pass on the final committed state.

   **A gate that fails on a test the diff cannot reach does not block the story.**
   "Run them all" means run them all, not pass them all regardless of cause. Where a
   gate fails on a test in a file this branch does not touch and whose code path the
   change cannot enter, establish that **once** and move on:

   - Say concretely why the diff cannot reach it — the files the commit touches, and
     the fact that the failing test exercises none of them. `git diff --stat
     <upstream-default>..HEAD` is usually the whole argument.
   - Name every failing test in the PR body's test plan, in a `<details>` block, and
     say the gate was otherwise clean. Do not mark the gate as a plain pass.
   - Record it in `$BOT_DIR/data/progress.txt` too, so the next iteration does not
     rediscover it.

   Then continue to step 12. Do **not** re-run the gate hoping for green: suites that
   stand up real servers or wait on a clock fail under load, a different test each
   time, and several runs share this machine. Two runs that fail on non-overlapping
   sets of unrelated tests have already proved the point. Where the target repo has
   its own guidance on this, it wins — read it before applying this rule.

   The rule is narrow. A failure in a file the diff touches is yours. A failure that
   repeats on the *same* test every run is yours until you show otherwise. A compile
   error, a lint, or a formatting diff is never a flake.

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

## Never stop without leaving a record

`progress.txt` gets an entry at a status transition, so an iteration that ends
*without* one writes nothing at all — and everything it learned dies with the
session. The next run then re-clones the state from scratch: it re-runs gates that
already passed, re-derives what the root cause was, and can redo work that is
already committed on the branch. A story has burned three sessions this way.

**Any iteration that ends while the story stays `pending` MUST append a progress
entry first** (see [progress-reporting.md](./progress-reporting.md#for-an-iteration-that-ends-without-a-status-transition)).
This covers every exit, not just a test failure: a gate you could not finish, a
blocked dependency, a rebase you decided against, running out of room. Record the
branch and commit SHA, each gate that passed with its exit code, the one gate that
is outstanding, and the exact next command.

The cost is one script call. The cost of skipping it is a full re-run of a presubmit
sequence that can take hours.

Two traps make this worse than it looks, and both have produced false "all green"
reports:

- **A backgrounded check's completion notification reports the wrong exit code.**
  `make check-x > log 2>&1; echo "EXIT=$?"` ends in the `echo`, so the harness
  reports *`echo`'s* status and a failed check arrives as "exit code 0". Put the
  echo inside the redirect — `{ make check-x; echo "EXIT=$?"; } > log 2>&1` — and
  read the status out of the log. Never quote a notification's exit code as a
  check's result.
- **A container-based check outlives the command that started it.** When the
  foreground `make` is killed, the container it launched usually keeps running, so
  its result is still recoverable: `docker ps` finds it, `docker inspect` confirms
  the mount is *your* worktree, and `docker logs -f <name>` plus `docker wait
  <name>` give you the output and the real exit code. Check for that before paying
  for another cold run.

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

**Consider alternative approaches :**
- **Refactor the test approach**: If a browser test is flaky, could the same functionality be verified with a more reliable unit test?
- **Fix the underlying code**: Sometimes the test is revealing a real bug in the production code
- **Add proper synchronization**: If there's a race condition, add explicit signaling rather than waits

**Before disabling: rule out an infrastructure root cause.**
If the failure shows any of the infrastructure/build/CI signals listed in step 5 (stale revision built, stale generated files, builder/cache-specific failure, a landed config not taking effect), **do NOT disable the test**. Disabling would mask a real DevOps regression. Instead keep the story `pending`, ping the bot owner with the evidence (see step 5's escalation), and END THE ITERATION. A test-disable PR is only appropriate when the test is genuinely unfixable *in code* — never as a way to paper over a broken build.

**Last resort - disable with full documentation:**
If no fix is viable after thorough investigation AND you have ruled out an infrastructure root cause, you may create a PR to disable the test, but you MUST:
- If the project has an upstream flakiness database, include its verdict (see the project profile's `docs/testing.md`)
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
