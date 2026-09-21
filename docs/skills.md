# Skills

Bot-only skills are available as slash commands in Claude Code. 8 bot-specific skills are included:

## PRD & Project Management

| Skill | Description |
|-------|-------------|
| `/prd` | Generate structured PRDs with clarifying questions |
| `/prd-json` | Convert PRD markdown to `prd.json` format |
| `/prd-clean` | Archive merged/invalid stories to `prd.archived.json` |
| `/add-backlog-to-prd` | Fetch open issues assigned to the bot and add to PRD, then recap and notify. The sync itself is `scripts/add-backlog-to-prd.py` — run it directly with `make backlog` if you don't need the recap |

## Code Review & Quality

| Skill | Description |
|-------|-------------|
| `/review-prs` | Batch review PRs for best practices violations. Supports auto mode for cron. Deduplicates against existing bot comments. Tracks prior comment context |
| `/learnable-pattern-search` | Analyze PR review comments to discover learnable patterns. Supports self-review mode to identify overly strict rules |
| `/update-best-practices` | Fetch and merge upstream Chromium documentation guidelines |

`/review-prs` is scheduled two ways, and a project can have either or both.
The **sweep** reviews whatever moved today, unsolicited, a few times a day. The
**review-request poll** runs every 15 minutes and reviews only PRs where someone
clicked "Request review" on the bot — `scripts/check-review-requests.sh` asks
GitHub, and `scripts/review-requested.sh` gives each one its own session. An
empty queue costs one API call: the gate stops before any agent starts. Both hold
the same `review-prs` lock, because either one builds a checkout per PR.

## Monitoring

| Skill | Description |
|-------|-------------|
| `/check-signal` | Check incoming Signal messages and execute commands |

## Developer Skills (target repo)

Developer-facing skills (commit, pr, review, preflight, etc.) are maintained in the target repository. Best practices docs location is configured via `bestPractices.docsDir`.
