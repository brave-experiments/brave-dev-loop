# Status: "committed" (Push and Create PR)

**Goal: Push branch and open pull request**

**IMPORTANT: This is the ONLY state where you should create a new PR. If status is "pushed", the PR already exists - NEVER create a duplicate PR.**

**NOTE: This step happens in the SAME iteration as "pending" → "committed" when all tests pass. Only proceed here if you just transitioned to "committed" in this iteration, OR if you're picking up a story that's already in "committed" status.**

## Steps

1. Change to git repo: `cd [targetRepoPath from bot config]` — or to the story's worktree, where the project profile's `docs/repo.md` uses one

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
   - **Substantially similar / should be combined** — the other issue is not closed by the current diff but is close enough that fixing both together is clearly better than two separate PRs (e.g. adjacent tests in the same file, same subsystem, same disable mechanism). Expand the current branch to cover it, note it under `## The problem`, and add a `Closes #<other-issue>` line. Keep the combined scope coherent — do not bundle unrelated fixes just because they are assigned to the same account.
   - **Unrelated** — leave it alone.

   When in doubt, keep PRs separate. Only combine when the fixes genuinely share a root cause or change the same code, and the combined PR stays reviewable. Record any issues you decide to combine or co-close in `$BOT_DIR/data/progress.txt`.

5. **Write the PR body, then check it before creating the PR.**

   The reviewer is a busy human who has not seen this code and did not read the
   issue. Read **[pr-descriptions.md](./pr-descriptions.md)** and write the body to
   the shape it defines. The two things it must deliver first: **how to reproduce
   the problem**, and **exactly what the problem is** — before any mechanism, and
   before any identifier.

   **SECURITY NOTE**: If this PR fixes a security-sensitive issue, use discretion in the title and description. See [SECURITY.md](../SECURITY.md#public-security-messaging) for detailed guidance on avoiding detailed vulnerability disclosure in public messages.

   Write the body to a file first — it has to be checked before it becomes a PR:

````bash
cat > /tmp/pr-body-<story-id>.md <<'EOF'
Closes $ISSUE_REPO#<issue-number>

## The problem
[2-4 sentences. What goes wrong, who hits it, what they observe. Plain language:
no type names, no file paths, no upstream citations. The symptom, not the cause.]

## Reproduce
```sh
[the exact command, and the directory it runs in if not the repo root]
```
[What it does today, and what it does with this branch. A test you added counts,
if you say it fails on the parent commit. If it truly cannot be reproduced here,
replace the block above with a line beginning "Not reproducible locally:" giving
the reason and a link to the evidence — the CI job, the crash report, the logs.]

## The fix
[2-5 sentences. The mechanism now, in plain language, and why it fixes the symptom
above. Do not enumerate the diff — the reviewer has the Files Changed tab.]

[If the project inherits tests from an upstream: see the project profile's `docs/testing.md` for the extra PR-body fields it requires here.]

## Test plan
- [x] Ran the project's presubmit sequence - passed (list the actual commands; see the project profile's `docs/testing.md`)
- [ ] CI passes cleanly
EOF

python3 $BOT_DIR/scripts/check-pr-body.py --body-file /tmp/pr-body-<story-id>.md
````

   **The checker must pass before you create the PR.** Errors are structural —
   fix them. Warnings are the filler heuristics; read each one and fix it unless
   you can say why it is wrong. Anything that genuinely needs depth (a table, a
   benchmark, a long root-cause chain, an upstream citation) goes in a
   `<details>` block, which the checker does not count against the length budget.

   **CRITICAL: Always include labels when creating the PR.** Determine which labels apply (see label rules below) and pass them directly to `gh pr create` using `--label` flags.

   **IMPORTANT**: Always create PRs in draft state using the `--draft` flag. This allows for human review before marking ready.

   ```bash
   gh pr create --draft --title "Story title" \
     --label "<each label the profile's rules give you>" \
     --body-file /tmp/pr-body-<story-id>.md
   ```

   **IMPORTANT**:
   - **The `Closes` line MUST be the very first line of the PR body**, above `## The problem`. Use the fully-qualified cross-repo form `Closes $ISSUE_REPO#<issue-number>` (substitute `$ISSUE_REPO` with the `issueRepository` value from the bot config). Issues live in the issue repository and PRs in the PR repository, so a bare `Closes #<n>` will NOT auto-close the cross-repo issue. Put the closing keyword + issue link at the TOP, never at the bottom.
   - Fill in actual test commands and results from acceptance criteria
   - If front-end files were changed, add checkboxes for the project's front-end test commands to the test plan
   - Keep the last checkbox "CI passes cleanly" unchecked
   - Do NOT add "Generated with Claude Code" or similar attribution
   - Capture the PR number from the output
   - **The `--label` flag above is a placeholder.** Determine the actual labels from the profile's rules in step 7 below, and pass one `--label` per label. Pass none if the profile defines none.
   - If step 4 identified other issues this fix also closes, add an additional `Closes $ISSUE_REPO#<number>` line for each one at the TOP of the PR body (one per line, immediately below the primary `Closes` line, above `## The problem`).

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

   **Labels are project-specific.** The project profile owns them: apply
   `labels.pr` from `projects/<profile>/profile.json` to the PR, and whatever
   the profile's `docs/labels.md` says on top of that (a profile that has no
   such doc has no further rules). A profile with an empty `labels.pr` means a
   PR with no labels — that is a valid outcome, not a step you skipped.

   Never invent a label, and never create one in the target repo: `gh pr create
   --label` fails outright on a label the repo does not have, taking the PR
   creation with it. If a label the profile names does not exist there, create
   the PR without it and record the mismatch in `$BOT_DIR/data/progress.txt`.

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
