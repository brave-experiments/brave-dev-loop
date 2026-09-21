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
**review-request poll** runs every 5 minutes and reviews only PRs where someone
clicked "Request review" on the bot — `scripts/check-review-requests.sh` asks
GitHub, and `scripts/review-requested.sh` gives each one its own session, up to
`REVIEW_REQUESTED_MAX_PRS` (5) a run. An empty queue costs one API call: the
gate stops before any agent starts.

Poll runs overlap. A review takes far longer than five minutes, so a poll that
waited for the previous one would drain the queue at one PR per session however
often it ran — which is what made a request behind seven others wait over two
hours. What bounds the overlap instead:

- **`with-lock.sh review-prs --slots 3`**, a counting semaphore rather than a
  single lock. The sweep draws on the same three slots, and asks for the same
  count: a job asking for one slot takes slot 1 only, so it would exit doing
  nothing every time a poll happened to be holding it.
- **A lock per PR**, held for the life of its session by `review-requested.sh`.
  GitHub only drops a PR from the queue once the review is submitted, so an
  overlapping run finds the PR being reviewed right now at the front of its own
  queue; the lock is what makes it walk past instead of posting a second review
  of the same PR. A PR skipped this way does not use up the run's cap.

Anything else these runs share had to stop assuming it ran alone:
`sync-target-repo.sh` and the skill's PR-head fetches now take the repo lock
(`git-repo-lock.sh`, or `lib/repo_lock.py` from Python — the same file, or they
would not contend), and the review cache is written as a locked, atomic merge
rather than a read-modify-write of a snapshot. See
[concurrent-runs.md](concurrent-runs.md).

## Monitoring

| Skill | Description |
|-------|-------------|
| `/check-signal` | Check incoming Signal messages and execute commands |

## Developer Skills (target repo)

Developer-facing skills (commit, pr, review, preflight, etc.) are maintained in the target repository. Best practices docs location is configured via `bestPractices.docsDir`.
