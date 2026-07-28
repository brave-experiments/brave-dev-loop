# Status: "skipped" and "invalid"

## Status: "skipped" (Intentionally Skipped)

**Goal: None - story is intentionally skipped**

This story has been intentionally skipped and will not be worked on. During task selection, skipped stories should not be picked (they're in the SKIP priority category). If you encounter a skipped story, simply move to the next story in priority order during task selection.

Stories can be manually set to "skipped" status when:
- The story is blocked indefinitely and should be skipped
- The story is intentionally deferred for later work
- The work is valid but deprioritized

**NOTE:** If the work is already done elsewhere or is a duplicate, use "invalid" status instead.

### IMPORTANT: Notify GitHub Issue When Skipping

When you change a story's status to "skipped" (from any status), you MUST check if there's a GitHub issue associated with it and notify stakeholders:

1. **Check if story has GitHub issue reference:**
   - Look for issue number in story's `description` or `issueUrl` field
   - If no issue is referenced, skip notification step

2. **Check for existing bot comment:**
   - Use `gh issue view <issue-number> --json comments` to check if we've already commented
   - Look for a comment from the bot account explaining the skip reason
   - If our comment already exists, skip notification step

3. **Post notification comment if needed:**
   ```bash
   gh issue comment <issue-number> --body "$(cat <<'EOF'
   This issue has been marked as skipped by the bot because:
   [Brief explanation of why - e.g., "already fixed on master", "no longer reproducible", "superseded by other work"]

   [Additional context if relevant - e.g., commit that fixed it, related PR, etc.]
   EOF
   )"
   ```

4. **Ping the bot owner to verify (investigation-based skips only):**

   When the skip decision rests on the bot's own investigation/judgment rather than an objective fact — e.g. "no longer reproducible", "already fixed on master", "root cause is infrastructure not code", "no code change can fix it" — the conclusion should be verified by a human before the issue stays closed. A skip that is purely operational (intentionally deferred, waiting on a known blocker) does NOT need this step.

   - Read `project.botOwnerGithubHandle` from the bot config provided in the prompt.
   - **If `botOwnerGithubHandle` is set** (non-empty), @-mention the owner in the same notification comment (or a follow-up comment) asking them to verify and confirm the close:
     ```
     @<botOwnerGithubHandle> I've skipped this based on the investigation above. Could you verify the conclusion and confirm the close if you agree? Reopen if I've got it wrong.
     ```
     Replace `<botOwnerGithubHandle>` with the configured handle. Only ever mention this exact configured handle — never guess or invent a username (see the no-hallucinated-mentions rule in CLAUDE.md).
   - **If `botOwnerGithubHandle` is empty/absent**, skip the @mention and note in progress.txt that no owner is configured to verify.

5. **Document in progress.txt:**
   - Note that you posted the GitHub comment
   - Include the reason for skipping
   - Note whether you pinged the bot owner to verify (and which handle)

**Example scenarios requiring notification:**
- Story skipped because of missing dependencies/blockers → Comment on issue explaining what's blocking it
- Story skipped because it's intentionally deferred → Comment on issue explaining the deferral reason

This ensures stakeholders who filed or are watching the issue aren't left wondering why the bot didn't work on it.

## Status: "invalid" (Invalid Story)

**Goal: None - story is invalid**

This story has been marked as invalid and will not be worked on. During task selection, invalid stories should not be picked (they're in the SKIP priority category). If you encounter an invalid story, simply move to the next story in priority order during task selection.

Stories can be manually set to "invalid" status when:
- The story is based on incorrect information or misunderstanding
- The story is a duplicate of another story
- The work is already completed elsewhere (e.g., fixed by another PR, fixed on master)
- The reported issue is not actually a bug (working as intended)
- The story requirements are contradictory or impossible
- The story is not applicable to the current codebase
- A PR for this story was closed without merging

### IMPORTANT: Notify GitHub Issue When Marking Invalid

When you change a story's status to "invalid" (from any status), you MUST check if there's a GitHub issue associated with it and notify stakeholders:

1. **Check if story has GitHub issue reference:**
   - Look for issue number in story's `description` or `issueUrl` field
   - If no issue is referenced, skip notification step

2. **Check for existing bot comment:**
   - Use `gh issue view <issue-number> --json comments` to check if we've already commented
   - Look for a comment from the bot account explaining the invalid reason
   - If our comment already exists, skip notification step

3. **Post notification comment if needed:**
   ```bash
   gh issue comment <issue-number> --body "$(cat <<'EOF'
   This issue has been marked as invalid by the bot because:
   [Brief explanation of why - e.g., "duplicate of #XXXX", "not reproducible - working as intended", "based on incorrect assumptions"]

   [Additional context if relevant - e.g., link to duplicate issue, explanation of expected behavior, etc.]
   EOF
   )"
   ```

4. **Ping the bot owner to verify (investigation-based invalids only):**

   When the invalid decision rests on the bot's own investigation/judgment rather than an objective fact — e.g. "not reproducible / working as intended", "root cause is infrastructure not code", "requirements are contradictory", "no code change can fix it" — the conclusion should be verified by a human before the issue stays closed. Duplicate-of-#XXXX or PR-closed-without-merging are objective and do NOT need this step.

   - Read `project.botOwnerGithubHandle` from the bot config provided in the prompt.
   - **If `botOwnerGithubHandle` is set** (non-empty), @-mention the owner in the same notification comment (or a follow-up comment) asking them to verify and confirm the close:
     ```
     @<botOwnerGithubHandle> I've marked this invalid based on the investigation above. Could you verify the conclusion and close it as invalid if you agree? Reopen if I've got it wrong.
     ```
     Replace `<botOwnerGithubHandle>` with the configured handle. Only ever mention this exact configured handle — never guess or invent a username (see the no-hallucinated-mentions rule in CLAUDE.md).
   - **If `botOwnerGithubHandle` is empty/absent**, skip the @mention and note in progress.txt that no owner is configured to verify.

5. **Document in progress.txt:**
   - Note that you posted the GitHub comment
   - Include the reason for marking invalid
   - Note whether you pinged the bot owner to verify (and which handle)

**Example scenarios requiring notification:**
- Story invalid because it's a duplicate → Comment on issue with link to original issue/PR
- Story invalid because work is already completed → Comment on issue explaining fix already exists (with PR link)
- Story invalid because behavior is working as intended → Comment explaining expected behavior
- Story invalid because requirements are contradictory → Comment explaining the contradiction
- Story invalid because PR was closed without merging → Comment on issue explaining PR was closed

This ensures stakeholders understand why the issue was marked invalid.
