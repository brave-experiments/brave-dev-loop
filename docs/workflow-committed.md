# Status: "committed" (Push and Create PR)

**Goal: Push branch and open pull request**

**IMPORTANT: This is the ONLY state where you should create a new PR. If status is "pushed", the PR already exists - NEVER create a duplicate PR.**

**NOTE: This step happens in the SAME iteration as "pending" → "committed" when all tests pass. Only proceed here if you just transitioned to "committed" in this iteration, OR if you're picking up a story that's already in "committed" status.**

## Steps

1. Change to git repo: `cd [targetRepoPath from bot config]`

2. Get branch name from story's `branchName` field

3. Push the branch: `git push -u origin <branch-name>`

4. **Scan other open issues for overlap with this fix:**

   Before writing the PR, check whether this same fix resolves (or is substantially similar to) other open issues. This avoids duplicate PRs and surfaces issues that can be closed together. Check both issues assigned to the bot AND a broader search of the issue repo — a related issue may not be assigned to the bot account.

   ```bash
   # 1. Open issues assigned to the bot account
   gh issue list --repo $ISSUE_REPO --assignee @me --state open \
     --json number,title,labels --limit 100

   # 2. Broader search of the issue repo for related open issues.
   #    Search by the key terms of THIS fix — e.g. the test/suite name,
   #    the file or component, or the crash/flake signature.
   gh issue list --repo $ISSUE_REPO --state open --search "<key terms>" \
     --json number,title,labels --limit 50
   ```

   Pick search terms from the fix itself: the failing test or suite name, the source file or component, or the crash/flake signature. Skim the results for genuine matches; don't over-broaden the query.

   For each candidate issue (excluding the one this story already addresses), decide:

   - **Same fix closes it too** — the change in this PR also resolves the other issue (e.g. same root cause, same test, same flaky target). Add an additional `Closes #<other-issue>` line to the PR body so it is closed on merge, and apply the same labels to that issue in step 7.
   - **Substantially similar / should be combined** — the other issue is not closed by the current diff but is close enough that fixing both together is clearly better than two separate PRs (e.g. adjacent tests in the same file, same subsystem, same disable mechanism). Expand the current branch to cover it, note it in the PR Summary, and add a `Closes #<other-issue>` line. Keep the combined scope coherent — do not bundle unrelated fixes just because they are assigned to the same account.
   - **Unrelated** — leave it alone.

   When in doubt, keep PRs separate. Only combine when the fixes genuinely share a root cause or change the same code, and the combined PR stays reviewable. Record any issues you decide to combine or co-close in `$BOT_DIR/data/progress.txt`.

5. Create PR using gh CLI with structured format:

   **SECURITY NOTE**: If this PR fixes a security-sensitive issue, use discretion in the title and description. See [SECURITY.md](../SECURITY.md#public-security-messaging) for detailed guidance on avoiding detailed vulnerability disclosure in public messages.

   **IMPORTANT**: Always create PRs in draft state using the `--draft` flag. This allows for human review before marking ready.

   **CRITICAL: Always include labels when creating the PR.** Determine which labels apply (see label rules below) and pass them directly to `gh pr create` using `--label` flags.

   ```bash
   gh pr create --draft --title "Story title" \
     --label "ai-generated" \
     --label "QA/No" \
     --label "release-notes/exclude" \
     --body "$(cat <<'EOF'
Closes $ISSUE_REPO#<issue-number>

## Summary
[Brief description of what this PR does and why]

[If this is a Chromium test being disabled, add a clear note:]
**Note: This is a Chromium test** (located in `./src/` not `./src/brave/`).

## Root Cause
[Description of the underlying issue that needed to be fixed]

[If this is a Chromium test, include:]
- **Chromium upstream status**: [Chromium has also disabled this test / Chromium has not disabled this test / Evidence of upstream bug: crbug.com/XXXXX]
- **Brave modifications**: [Brave does not modify this code area / Brave has modifications in ./src/brave/chromium_src/[path] that may affect this test]

## Fix
[Description of how the fix addresses the root cause]

## Test Plan
- [x] Ran pnpm run format - passed
- [x] Ran pnpm run presubmit - passed
- [x] Ran pnpm run gn_check - passed
- [x] Ran pnpm run build - passed
- [x] Ran pnpm run test [test-name] - passed [N/N times]
- [ ] CI passes cleanly
EOF
)"
   ```

   **IMPORTANT**:
   - **The `Closes` line MUST be the very first line of the PR body**, above `## Summary`. Use the fully-qualified cross-repo form `Closes $ISSUE_REPO#<issue-number>` (substitute `$ISSUE_REPO` with the `issueRepository` value from the bot config). Issues live in the issue repository and PRs in the PR repository, so a bare `Closes #<n>` will NOT auto-close the cross-repo issue. Put the closing keyword + issue link at the TOP, never at the bottom.
   - Fill in actual test commands and results from acceptance criteria
   - If `.ts`/`.tsx`/`.js` files were changed, add checkboxes for `pnpm run test-unit` and `pnpm run build-storybook` to the test plan
   - Keep the last checkbox "CI passes cleanly" unchecked
   - Do NOT add "Generated with Claude Code" or similar attribution
   - Capture the PR number from the output
   - **The `--label` flags above are an example for test fixes.** Adjust labels based on the rules in step 7 below. The `ai-generated` label is ALWAYS required.
   - If step 4 identified other issues this fix also closes, add an additional `Closes $ISSUE_REPO#<number>` line for each one at the TOP of the PR body (one per line, immediately below the primary `Closes` line, above `## Summary`).

6. **Assign the PR to yourself (the bot account):**

   ```bash
   gh pr edit <pr-number> --add-assignee @me
   ```

   This makes it clear who is responsible for the PR and helps with tracking.

7. **Set appropriate labels on the PR and linked issues:**

   Labels should have been applied during PR creation in step 5 via `--label` flags. If any labels were missed, add them now:

   ```bash
   # Add missing labels to PR (if not already applied during creation)
   gh pr edit <pr-number> --add-label "label1,label2"

   # Add labels to linked issue (ALWAYS required - cannot be done during PR creation)
   gh issue edit <issue-number> --add-label "label1,label2" --repo $ISSUE_REPO
   ```

   ### Label Rules

   **MANDATORY for ALL PRs (no exceptions):**
   - `ai-generated` — MUST be on every bot-created PR

   **For test issue fixes (add to BOTH PR and linked issue):**
   - `QA/No` — manual QA not needed
   - `release-notes/exclude` — not user-facing
   - `CI/skip` — **only if the change is limited to filter files** (e.g., `test/filters/`). This skips unnecessary CI for trivial filter-only changes.

   **For other PRs:**
   - `release-notes/exclude` — add to both PR and linked issue for changes typical users wouldn't care about (code cleanup, refactors, internal tooling, etc.)
   - `QA/No` — use judgment based on whether manual QA testing is needed

   **OS/platform labels (ALWAYS add to the linked issue):**

   Determine which platform(s) the fix relates to, then add the matching OS label(s) to the linked issue:
   - `OS/Desktop` — the fix targets desktop (Windows, macOS, Linux)
   - `OS/Android` — the fix targets Android
   - `OS/iOS` — the fix targets iOS

   Add all that apply (a cross-platform fix may span multiple platforms). Base the decision on the affected code's location, platform-specific build flags/guards (e.g. `BUILDFLAG(IS_ANDROID)`, `BUILDFLAG(IS_IOS)`, desktop-only code paths), and where the issue was reported.

   ```bash
   gh issue edit <issue-number> --add-label "OS/Desktop" --repo $ISSUE_REPO
   ```

8. **If push or PR creation succeeds:**
   - Update the PRD status:
     ```bash
     python3 $BOT_DIR/scripts/update-prd-status.py pushed <story-id> --pr-number <number>
     ```
   - Append to `$BOT_DIR/data/progress.txt` (see [progress-reporting.md](./progress-reporting.md))
   - **Send Signal notification** (no-op if not configured):
     ```bash
     $BOT_DIR/scripts/signal-notify.sh "PR created: #<number> - <title> https://github.com/$PR_REPO/pull/<number>"
     ```
   - **END THE ITERATION** - Stop processing

9. **If push or PR creation fails:**
   - DO NOT update status in prd.json (keep as "committed")
   - Document failure in `$BOT_DIR/data/progress.txt`
   - **END THE ITERATION** - Stop processing
