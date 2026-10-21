# Status: "committed" (Push and Create PR)

**Goal: Push branch and open pull request**

**IMPORTANT: This is the ONLY state where you should create a new PR. If status is "pushed", the PR already exists - NEVER create a duplicate PR.**

**NOTE: This step happens in the SAME iteration as "pending" → "committed" when all tests pass. Only proceed here if you just transitioned to "committed" in this iteration, OR if you're picking up a story that's already in "committed" status.**

## Steps

1. Change to git repo: `cd [targetRepoPath from bot config]` — or to the story's worktree, where the project profile's `docs/repo.md` uses one

2. Get branch name from story's `branchName` field

3. Push the branch: `git push -u origin <branch-name>`

   The branch must already sit on current upstream, with the presubmit sequence passed
   there ([workflow-pending.md](./workflow-pending.md) step 9). Where upstream moved while
   those checks ran, rebase and re-run them before pushing: a rebase before the first push
   is free, and one afterwards costs a force push.

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
   the shape it defines. The three things it must deliver first: **whether a
   person using the product can see any difference**, **how to reproduce the
   problem**, and **exactly what the problem is** — before any mechanism, and
   before any identifier. If a user can see the bug, the reproduction is the
   steps a *user* takes in the running product; a test command on its own shows a
   reviewer nothing they could have seen for themselves. If the diff changes no
   behaviour at all — a spec, a refactor, a doc, tests — the `User impact:` line
   is where you say so, and it is the first thing the reviewer reads.

   **SECURITY NOTE**: If this PR fixes a security-sensitive issue, use discretion in the title and description. See [SECURITY.md](../SECURITY.md#public-security-messaging) for detailed guidance on avoiding detailed vulnerability disclosure in public messages.

   The `## The fix` section ends by showing the thing itself, pasted from a real
   run. Fenced blocks count against no length budget, so a block costs less than
   the paragraph that would describe it. For a full-screen terminal program its
   output is not its screen: capture a raw run and replay it with
   `$BOT_DIR/scripts/terminal-screenshot.py` rather than pasting redirected
   output — the profile's `docs/testing.md` gives the recipe. When the diff
   touches a path the profile lists under `uiPaths` the checker requires a screen
   there, and nothing written in the body opts out of it.

   Write the body to a file first — it has to be checked before it becomes a PR:

````bash
cat > /tmp/pr-body-<story-id>.md <<'EOF'
Closes $ISSUE_REPO#<issue-number>

User impact: [one line. What a person using the product can now see. If nobody
can see anything — a spec, a refactor, a doc, a CI fix, tests — write "none" and
say which, so the reviewer reads the sections below as a document rather than as
a change in behaviour.]

## The problem
[2-4 sentences. What goes wrong, who hits it, what they observe. Plain language:
no type names, no file paths, no upstream citations. The symptom, not the cause.]

## Reproduce
1. [what a person does first in the running product — the screen or URL, the
   setting, the input; a command exactly as typed, with its directory]
2. [the step that shows the bug]

[What you observed, verbatim where it is output, and what you expected instead.]

[The test you added, on one line: name it and say it fails on the parent commit.
It is the evidence, not the reproduction. A test invocation on its own is enough
only when the diff touches nothing but tests — then pass --test-only-change to
the checker. If the problem cannot be reproduced here at all, replace everything
above with a line beginning "Not reproducible locally:" giving the reason and a
link to the evidence — the CI job, the crash report, the logs.]

## The fix
[2-5 sentences. The mechanism now, in plain language, and why it fixes the symptom
above. Do not enumerate the diff — the reviewer has the Files Changed tab. No bare
clause, story or ticket id: say the rule in English, or link the id to the clause.]

[Then show it, if there is anything to look at: the screen as it now renders, the
file the product now writes, the output that is now different — pasted from a real
run into a fenced block, trimmed to the part that changed. Use one block per thing
a person would otherwise have to be told: the before and the after where the
difference is the point, and each step where the change is a sequence. Delete this
slot only when the change is invisible.]

[If the project inherits tests from an upstream: see the project profile's `docs/testing.md` for the extra PR-body fields it requires here.]

## Test plan
- [x] Ran the project's presubmit sequence - passed (list the actual commands; see the project profile's `docs/testing.md`)
- [ ] CI passes cleanly
EOF

python3 $BOT_DIR/scripts/check-pr-body.py --body-file /tmp/pr-body-<story-id>.md \
  --diff-base <the ref this worktree was branched from; the profile's docs/repo.md names it>
````

   **The checker must pass before you create the PR.** Run it from the story
   worktree, so `--diff-base` can see the diff. Errors are structural —
   fix them. Warnings are the filler and reproduction heuristics; read each one
   and fix it unless you can say why it is wrong. Anything that genuinely needs
   depth (a table, a benchmark, a long root-cause chain, an upstream citation)
   goes in a `<details>` block, which the checker does not count against the
   length budget.

   **Then account for the sentences this branch wrote into the source.** A string
   in the source ships in one language whatever the reader's is, and beside text
   that does come from a catalog it leaves them half a sentence in their own:

   ```bash
   python3 $BOT_DIR/scripts/check-untranslated.py \
     --diff-base <the same ref as above>
   ```

   Every line it prints is one of two things and only the author can tell which:
   something **a person reads**, which belongs in the project's message catalog —
   the output says where and how — or something **a machine reads**, a prompt or
   a tool description, which has to stay in the source because translating it
   would change what the program does. Move the first. For the second, say in the
   body which strings stayed in the source and why. This one exits 0 either way:
   it cannot tell the two apart, which is exactly why leaving a finding
   unaccounted for is how an interface reaches the reviewer half in English. A
   project whose profile declares no `localization` block has nothing to check
   here, and the command says so.

   **CRITICAL: Always include labels when creating the PR.** Determine which labels apply (see label rules below) and pass them directly to `gh pr create` using `--label` flags.

   **IMPORTANT**: Whether the PR opens as a draft is the project profile's call: read `prDraft` in `projects/<profile>/profile.json`. `true` or absent: pass `--draft`, which allows human review before marking ready. `false`: omit `--draft` and open the PR ready for review. Do not open it as a draft and flip it afterwards: a draft requests no reviewers, so the reminder path in [workflow-pushed.md](./workflow-pushed.md) never reaches it.

   ```bash
   gh pr create [--draft] --title "Story title" \
     --label "<each label the profile's rules give you>" \
     --body-file /tmp/pr-body-<story-id>.md
   ```

   **IMPORTANT**:
   - **The `Closes` line MUST be the very first line of the PR body**, above `## The problem`. Use the fully-qualified cross-repo form `Closes $ISSUE_REPO#<issue-number>` (substitute `$ISSUE_REPO` with the `issueRepository` value from the bot config). Issues live in the issue repository and PRs in the PR repository, so a bare `Closes #<n>` will NOT auto-close the cross-repo issue. Put the closing keyword + issue link at the TOP, never at the bottom.
   - **`Closes` is the default; `Part of` is the exception, and it has to be earned.** `Part of` leaves the issue open. Used on a PR that does everything the issue asks, it strands a finished issue, and the loop then spends a story re-discovering that nothing is left. Before choosing it, go through what the issue asks for, clause by clause, and what its comments added, and name the piece this PR does not do. If you can name one, the PR is partial. If you cannot, or the only thing left is a follow-up you would like to do, the PR closes the issue.
   - **If this PR fixes only part of the issue**, open with `Part of $ISSUE_REPO#<issue-number>` instead of `Closes`. A `Remaining:` line directly under `User impact:` is optional, and worth adding only when this PR leaves work that no other open issue already covers:

     ```markdown
     Part of $ISSUE_REPO#<issue-number>

     User impact: <as usual>

     Remaining: <the piece of the issue this PR does not do, in one line>
     ```

     `scripts/check-pr-body.py` recognises the `Part of` line itself and does not need `--no-closes` for it. It does not require the `Remaining:` line; it rejects one that is empty or says nothing is left, because that PR should have said `Closes`. Never add a `Closes` line to quiet the checker on work that does not close the issue. Once this story merges, the next `scripts/add-backlog-to-prd.py` sync re-adds the issue as a `Finish issue #<n>` story naming this PR.
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
   - **Read the PR back before recording anything about it.** `gh pr create` printing a URL does not say what state the PR is in, and the next iteration will trust whatever you write:
     ```bash
     gh pr view <pr-number> --json state,isDraft,reviewRequests,assignees,labels
     ```
     Record the values it returns, not the flags you passed. If `isDraft` disagrees with the profile's `prDraft`, or a label or assignee from steps 5-7 is missing, fix it now and read again.
   - **If the body opens with `Part of`, comment on the issue what is left, but only if it needs saying.** Work you left on purpose may already have its own issue; do not restate it. Post the comment only when both hold: the leftover work could not reasonably be done in this PR, and no other open issue covers it. Check before posting:
     ```bash
     gh issue list --repo $ISSUE_REPO --state open --search "<keywords from the leftover work>"
     ```
     If an open issue covers it, post nothing. Otherwise the issue stays open after this PR merges, and the next person to open it sees an open issue and a merged PR with nothing connecting them. Say what this PR does, what it does not, and link the PR by its full URL (the issue and the PR live in different repositories, so a bare `#<n>` points at the wrong one):
     ```bash
     gh issue comment <issue-number> --repo $ISSUE_REPO --body "$(cat <<'EOF'
     https://github.com/$PR_REPO/pull/<number> fixes part of this issue and does not close it.

     Done there: <one line>

     Remaining:
     - <each piece of the issue still open, as its own line>
     EOF
     )"
     ```
     Substitute the real values before running it: the quoted heredoc does not expand `$PR_REPO`. Spell the remaining items out as a list where there is more than one. Do not @-mention anyone, and apply the SECURITY NOTE above to the wording. Skip it only if you have already commented about this PR. If the comment fails, record that in `$BOT_DIR/data/progress.txt` and carry on: the PR exists, so the status still moves. A `Closes` PR needs no comment.
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
