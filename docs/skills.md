# Skills

Bot-only skills are available as slash commands in Claude Code. bot-specific skills are included:

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
| `/learnable-pattern-search` | Analyze PR review comments to discover learnable patterns. Supports self-review mode to identify overly strict rules |
| `/update-best-practices` | Fetch and merge upstream Chromium documentation guidelines |

`/review-prs`, the scheduled review sweeps, the review-request poll and the ping
replies moved to https://github.com/brave-experiments/reviewer. This repo keeps
only the [review gate](review-gate.md): `./run.sh` waits for the reviewer
account's verdict on each pull request it pushes.

## Monitoring

| Skill | Description |
|-------|-------------|
| `/check-signal` | Check incoming Signal messages and execute commands |

## Developer Skills (target repo)

Developer-facing skills (commit, pr, review, preflight, etc.) are maintained in the target repository. Best practices docs location is configured via `bestPractices.docsDir`.
