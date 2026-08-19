---
name: add-backlog-to-prd
description: "Fetch open issues assigned to the bot from the configured issue repository and add missing ones to the PRD backlog. Triggers on: add backlog to prd, update prd with issues, sync backlog, fetch issues for prd."
allowed-tools: Bash, Read, Grep, Glob
---

# PRD - Add Backlog Issues

Automatically fetch open issues assigned to the bot from the configured issue repository and add any missing ones to the PRD.

---

## The Job

**Step 0: Read project config.** Read `config.json` (in the bot repo root) to determine:
- `project.issueRepository` — the repo to fetch issues from (e.g. `brave/brave-browser`)
- `bot.username` — the GitHub username of the bot

1. Get the bot username from `config.json`
2. Fetch all open issues assigned to the bot username from the issue repository
3. Compare with existing issues in the PRD (`./data/prd.json`)
4. Add any missing issues as new user stories
5. Provide a recap of what was added

---

## Step 1: Bot Username

The bot username is already available from `config.json` (read in Step 0) via `bot.username`. Use this as the GitHub username for fetching assigned issues.

---

## Step 2: Fetch GitHub Issues

Read `config.json` to get the issue repository and bot username. Fetch issues assigned to the bot:

```bash
gh issue list --repo <issueRepository> --assignee "$BOT_USER" --state open --json number,title,url,labels --limit 100
```

---

## Step 3: Process Issues and Update PRD

A single helper script handles both new and existing PRDs:

```bash
ISSUE_REPO=$(jq -r '.project.issueRepository' config.json)
BOT_USER=$(jq -r '.bot.username' config.json)
# Note: both values above come from config.json (already read in Step 0)
gh issue list --repo "$ISSUE_REPO" --assignee "$BOT_USER" --state open --json number,title,url,labels --limit 100 | \
  .claude/skills/add-backlog-to-prd/update-prd-with-issues.py ./data/prd.json > /tmp/prd_updated.json && \
  mv /tmp/prd_updated.json ./data/prd.json
```

If `./data/prd.json` doesn't exist yet, the script creates a new PRD. If it already exists, it only appends missing issues and never modifies existing stories.

### What the script does:

- **Detect issue type**: Issues with "Test failure:" title prefix or `bot/type/test` label are treated as test issues; all others get generic stories
- **For test issues**:
  - Extract test names from issue titles (handles multiple prefixes)
  - Determine test location at generation time by running `git grep` to find if the test is in `src/brave` or `src` (Chromium)
  - Generate test-specific acceptance criteria with correct test binary and filter
  - Include `testType`, `testLocation`, and `testFilter` fields
- **For generic issues**:
  - Use the issue title directly as the story title
  - Generate standard acceptance criteria (fetch issue, analyze, implement, build, format, presubmit, gn_check, find and run relevant tests)
- Generate proper user story structure with sequential US-XXX IDs and priority ordering
- Skip issues already in the PRD
- Safety check verifies existing stories were not modified

---

## Step 3b: Sync Untracked Bot PRs

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

## Step 4: Provide Recap

Generate a comprehensive recap showing:

1. **New Issues Added**: List each new user story with:
   - US-XXX number
   - Test name
   - Issue number
   - Test type
   - Status

2. **Existing Issues Status Overview**: Summarize existing stories by status:
   - Merged
   - Pushed
   - Pending
   - Skipped
   - Invalid

3. **Untracked Bot PRs Added** (from Step 3b): US-XXX, PR number, and PR title
   for each one

4. **Total PRD Statistics**:
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

- Always preserve the exact structure of existing user stories
- Test issues include a best_practices.md read step in acceptance criteria; the path is derived from `bestPractices.docsDir` + `bestPractices.indexFile` in `config.json` (e.g. `../src/brave/docs/best_practices.md`)
- Test type determination is critical for generating correct test commands
- Priority numbers must be sequential and not conflict with existing ones
- All new issue-derived stories start in "pending" status; PR-derived stories from Step 3b start in "pushed" status because their PR already exists

---

## Step 5: Signal Notification

After the recap, send a Signal notification summarizing what was added. Each issue link goes on its own line.

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

- If `gh` CLI is not available, report error and exit
- If `./data/prd.json` doesn't exist, a new one is created automatically
- If GitHub API rate limit is hit, report error with retry time
- If `jq` is not available, report error and exit
