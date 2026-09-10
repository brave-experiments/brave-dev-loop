---
name: add-backlog-to-prd
description: "Fetch open issues assigned to the bot from the configured issue repository and add missing ones to the PRD backlog. Triggers on: add backlog to prd, update prd with issues, sync backlog, fetch issues for prd."
allowed-tools: Bash, Read, Grep, Glob
---

# PRD - Add Backlog Issues

Fetch open issues assigned to the bot from the configured issue repository and add any missing ones to the PRD.

---

## The Job

The issue sync itself is deterministic and needs no LLM: `scripts/add-backlog-to-prd.py`
does the whole thing, and `make backlog` is the same script. Run it, then do the
parts that do need judgment — the recap and the Signal notification.

---

## Step 1: Sync Assigned Issues Into the PRD

```bash
python3 ./scripts/add-backlog-to-prd.py
```

The script reads `config.json` itself (`project.issueRepository`, `bot.username`,
`project.targetRepoPath`, `bestPractices.*`), fetches every open issue assigned to
the bot, and appends a story for each one that isn't already tracked. It prints a
human summary on stderr and a JSON summary on stdout:

```json
{"added": [{"id": "US-016", "issueNumber": 52439, "title": "...", "status": "pending", "priority": 16,
            "triage": {"importance": 2, "urgency": 3, "size": 2}}],
 "retriaged": [{"id": "US-009", "issueNumber": 52001, "from": {"urgency": 4}, "to": {"urgency": 2}}],
 "checked": 15, "alreadyTracked": 8, "issueRepository": "brave/brave-browser", "dryRun": false}
```

Use that JSON for the recap — don't re-fetch the issue list.

Flags: `--dry-run` (report only), `--prd PATH`, `--issues-file -` (read issue JSON
from stdin instead of calling `gh`).

### What the script does:

- **Dedupe**: skips issues already referenced as `issue #N` by a story in
  `data/prd.json` or `data/prd.archived.json`, so archived (merged) work is not
  re-added
- **Detect issue type**: issues titled `Disabled test:` (or labeled
  `disabled-brave-test`) become re-enable stories; `Test failure:` (or
  `bot/type/test`) become test-fix stories; everything else gets a generic story
- **For test issues**:
  - Extract test names from issue titles (handles multiple prefixes)
  - Determine test location at generation time by running `git grep` against the
    configured target repo and its parent checkout
  - Generate test-specific acceptance criteria with correct test binary and filter
  - Include `testType`, `testLocation`, and `testFilter` fields
- **For generic issues**:
  - Use the issue title directly as the story title
  - Generate standard acceptance criteria (fetch issue, analyze, implement, build,
    format, review, run relevant tests, presubmit)
- Generate proper user story structure with sequential US-XXX IDs and priority
  ordering
- **Read the triage axes**: the importance/urgency/size labels the issue carries
  become the story's `triage` block, which is what orders pending work — see
  [Backlog order](../../../docs/workflow-state-machine.md#backlog-order-the-three-triage-axes).
  Which labels spell an axis comes from the profile's `labels.axes`, so a project
  that does not label its issues that way gets no block and loses nothing
- **Re-triage what is still pending**: a pending story's block is brought back
  into line with its issue's labels on every run, so somebody raising an urgency
  reaches the next iteration. It is the only field of an existing story this
  script ever rewrites
- Create `data/prd.json` from scratch if it doesn't exist
- Write atomically, and abort with a safety-check error if any existing story
  would have been modified in any way but its `triage` block

---

## Step 2: Sync Untracked Bot PRs

Some cron skills (`learnable-pattern-search`, `update-best-practices`) open PRs
directly against the PR repository without creating a story. Those PRs are
invisible to `run.sh`/`select-task.py`, so nothing ever responds to reviews,
rebases them, or nudges them toward a merge. Catch any that slipped through:

```bash
python3 ./scripts/sync-bot-prs-to-prd.py
```

The script fetches every open PR authored by `bot.username` in
`project.prRepository`, skips ones already referenced by a story (in
`data/prd.json` *or* `data/prd.archived.json`), skips drafts, and appends the
rest as `pushed` stories. It never modifies existing stories. Use `--dry-run`
first if you want to see what it would add.

Include anything it added in the recap below.

---

## Step 3: Provide Recap

Generate a comprehensive recap showing:

1. **New Issues Added**: List each new user story with:
   - US-XXX number
   - Test name
   - Issue number
   - Test type
   - Status
   - Triage axes, where the issue carried any

2. **Existing Issues Status Overview**: Summarize existing stories by status:
   - Merged
   - Pushed
   - Pending
   - Skipped
   - Invalid

3. **Untracked Bot PRs Added** (from Step 2): US-XXX, PR number, and PR title
   for each one

4. **Re-triaged Stories** (`retriaged` in the Step 1 JSON): US-XXX, issue
   number, and which way the triple moved. An axis that moved is somebody
   re-prioritising work the loop already has, so it is worth a line even though
   no story was added

5. **Total PRD Statistics**:
   - Total count before and after
   - Count by status

---

## Example Output Format

```markdown
# PRD Update Recap

## Summary
Successfully fetched 15 open issues assigned to the bot and added 7 missing issues to the PRD.

## New Issues Added (US-016 to US-022)

1. **US-016** - Fix test: BraveSearchTestEnabled.DefaultAPIVisibleKnownHost (issue #52439)
   - Type: browser_test
   - Status: pending
   - Priority: 16

[... more entries ...]

## Existing Issues Status Overview

### Merged (6 issues)
- US-001: SolanaProviderTest.AccountChangedEventAndReload (#50022)
[... more entries ...]

### Pushed (1 issue)
[... entries ...]

### Skipped (7 issues)
[... entries ...]

### Invalid (1 issue)
[... entries ...]

### Pending (7 new issues)
[... entries ...]

## Total PRD Statistics
- Total user stories: 22 (was 15, added 7)
- Merged: 6
- Pushed: 1
- Pending: 7
- Skipped: 7
- Invalid: 1
```

---

## Important Notes

- Always preserve the exact structure of existing user stories — the `triage` block is the single exception, and the script owns it
- The script owns story structure — never hand-write stories into `data/prd.json`; if a story comes out wrong, fix `scripts/add-backlog-to-prd.py`
- Every story opens with the project profile's `research` steps and closes with its `validations` (`projects/<profile>/profile.json`) — which docs to read and which checks to run are the project's answer, not this skill's. See [Project profiles](../../../projects/README.md)
- Test type determination is critical for generating correct test commands
- Priority numbers must be sequential and not conflict with existing ones
- All new issue-derived stories start in "pending" status; PR-derived stories from Step 2 start in "pushed" status because their PR already exists

---

## Step 4: Signal Notification

After the recap, send a Signal notification summarizing what was added. Each issue link goes on its own line. The issue numbers and repository come from the Step 1 JSON summary (`added[].issueNumber`, `issueRepository`).

**If new issues were added:**

```bash
$BOT_DIR/scripts/signal-notify.sh "PRD backlog updated: added <N> new issues.
https://github.com/$ISSUE_REPO/issues/<number1>
https://github.com/$ISSUE_REPO/issues/<number2>
https://github.com/$ISSUE_REPO/issues/<number3>"
```

**If no new issues were added:**

```bash
$BOT_DIR/scripts/signal-notify.sh "PRD backlog sync: no new issues to add. <N> issues already tracked."
```

Do NOT send a notification without issue links when issues were added.

This is a no-op if Signal is not configured.

---

## Error Handling

The script exits 2 and prints the cause on stderr when `gh` is missing, `gh`
fails (auth, rate limit, unknown repo), or a PRD file is unreadable. Report the
error and stop — do not hand-edit `data/prd.json` to work around it. A missing
`data/prd.json` is not an error; the script creates one.
