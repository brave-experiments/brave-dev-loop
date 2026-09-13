# Progress Reporting

APPEND to $BOT_DIR/data/progress.txt (never replace, always append).

Append with `$BOT_DIR/scripts/append-progress.sh`, not with `>>`:

```bash
$BOT_DIR/scripts/append-progress.sh <<'ENTRY'
## 2026-09-10 14:02 - US-004 - Status: pending → committed
...
ENTRY
```

It takes a lock, so an entry cannot interleave with another run's ([Concurrent runs](./concurrent-runs.md)). A bare `>>` from two agents at once produces a log where neither entry is readable.

**IMPORTANT:** Every progress entry MUST include a `Resume command` line with the claude resume command for the current session. Use the format: `claude -r <session-id>` where `<session-id>` is the current conversation's session ID. This allows easy resumption of the agent.

## For status: "pending" → "committed"

```
## [Date/Time] - [Story ID] - Status: pending → committed
- What was implemented
- Files changed
- Branch created: [branch-name]
- **Reproduction** (REQUIRED — this is what the PR body quotes):
  - The steps, numbered. For anything a user can see, what a *user* does in the
    running product: the screen or URL, the setting, the input. For a command,
    exactly as run and from which directory
  - What it did before the fix (the failing output or wrong state, trimmed)
  - What it does now
  - The test that covers it, if you added one — named, with both outcomes. It
    replaces the steps only when the change is test-only
  - If it cannot be reproduced here: why, plus the CI job / crash report / issue link
- [If filter file modification] **Project-specific test fields**: see the project profile's `docs/progress.md`
- **Test Results** (REQUIRED):
  - [List all acceptance criteria tests and their results]
  - All tests MUST pass before transitioning to "committed"
- **Learnings for future iterations:**
  - Patterns discovered
  - Gotchas encountered
  - Useful context
- **Resume command:** `claude -r <session-id>`
---
```

## For an iteration that ends without a status transition

The story stays where it is, so nothing else records that this iteration happened.
Write this before you stop — see
[workflow-pending.md](./workflow-pending.md#never-stop-without-leaving-a-record).

```
## [Date/Time] - [Story ID] - Status: [status] (iteration ended, no transition)
- Why it stopped: [gate not finished / blocked on X / decided against Y]
- Branch and commit: [branch-name] at [sha], based on [upstream sha]
- What is already done and must NOT be redone:
  - [gate or step]: [result, with its exit code]
  - [gate or step]: [result, with its exit code]
- What is outstanding: [the one gate or step left]
- Next command: [the exact command the next iteration should run first]
- Artefacts: [log paths, /tmp files, a drafted PR body — anything the next
  iteration would otherwise rewrite]
- **Resume command:** `claude -r <session-id>`
---
```

Read the newest such entry for the story **before** re-running anything: it is the
difference between finishing a story and starting it again.

## For status: "committed" → "pushed"

```
## [Date/Time] - [Story ID] - Status: committed → pushed
- Pushed branch: [branch-name]
- Created PR: #[pr-number]
- PR URL: [url]
- **Resume command:** `claude -r <session-id>`
---
```

## For status: "pushed" (handling reviews)

```
## [Date/Time] - [Story ID] - Status: pushed (review iteration)
- Review comments addressed:
  - [Summary of feedback from org members]
  - [Changes made]
- **Test Results** (REQUIRED):
  - [Re-ran all acceptance criteria tests]
  - All tests MUST pass before pushing review changes
- Commit strategy: [Amended last commit / Created new commit]
- Posted reply to PR #[pr-number] explaining fixes
- Pushed changes to PR #[pr-number]
- **Resume command:** `claude -r <session-id>`
---
```

## For status: "pushed" (status check - waiting for reviewer)

```
## [Date/Time] - [Story ID] - Status: pushed (checked - waiting for reviewer)
- Checked PR #[pr-number]
- Review Decision: [APPROVED/REVIEW_REQUIRED/etc]
- CI Status: [summary of checks]
- Latest activity: Bot went last (no new comments from reviewers)
- Time waiting: [X hours/days since last push]
- Reviewer reminder: [Sent ping to @reviewer1, @reviewer2 / Not needed yet (< 24hrs) / Already pinged recently / No reviewers assigned]
- Action: Waiting for reviewer feedback - ending iteration
- **Resume command:** `claude -r <session-id>`
---
```

## For status: "pushed" → "merged"

```
## [Date/Time] - [Story ID] - Status: pushed → merged
- PR #[pr-number] merged successfully
- Final approvals: [list of approvers]
- Post-merge monitoring initialized: First check in 1 day
- **Resume command:** `claude -r <session-id>`
---
```

## For status: "merged" (post-merge check)

```
## [Date/Time] - [Story ID] - Status: merged (post-merge check #[N])
- Checked PR #[pr-number] for post-merge follow-up comments
- Comments found since merge: [count]
- New comments from org members: [list usernames or "none"]
- Follow-up work needed: [Yes/No]
- [If yes: Created follow-up work:
  - Story US-XXX: "[title]" (GitHub issue #YYYY - [issue URL])
    - Replied to @[username] on PR with issue link
  - Story US-ZZZ: "[title]" (GitHub issue #WWWW - [issue URL])
    - Replied to @[username] on PR with issue link
]
- [If no: No follow-up action required]
- Next check scheduled: [timestamp] ([interval] from now)
- [Or if final: "Post-merge monitoring complete - reached final state"]
- **Resume command:** `claude -r <session-id>`
---
```

## For status: [any] → "skipped"

```
## [Date/Time] - [Story ID] - Status: [previous-status] → skipped
- **Reason for skipping:** [Brief explanation - e.g., "blocked by missing dependency", "intentionally deferred"]
- **Root Cause Analysis:** [What you discovered about why this story is being skipped]
- **Resolution:** [What's blocking it, or why it's deferred]
- **GitHub Notification:** [Posted comment on issue #XXXX / No issue referenced / Comment already exists]
- **Note:** [Any additional context for future reference]
- **Resume command:** `claude -r <session-id>`
---
```

## For status: [any] → "invalid"

```
## [Date/Time] - [Story ID] - Status: [previous-status] → invalid
- **Reason for invalid:** [Brief explanation - e.g., "duplicate of #XXXX", "already fixed by PR #YYYY", "not a bug - working as intended", "PR was closed without merging"]
- **Analysis:** [What you discovered about why this story is invalid]
- **GitHub Notification:** [Posted comment on issue #XXXX / No issue referenced / Comment already exists]
- **Note:** [Any additional context for future reference]
- **Resume command:** `claude -r <session-id>`
---
```
