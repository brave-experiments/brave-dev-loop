# Brave Dev Loop

An autonomous coding agent for Brave projects. Built on Claude Code, it reads Product Requirements Documents (PRDs), implements user stories, runs tests, creates PRs, handles review feedback, and manages CI — all autonomously in iterative loops.

Each clone of this repo targets a single project (configured via `config.json`). To use with multiple repositories, clone once per project with its own config.

## Overview

The bot operates in an iterative loop with a state machine workflow:

1. Reads PRD from `data/prd.json`
2. Picks the next story using smart priority (reviewers first!):
   - **URGENT**: Pushed PRs where reviewer responded (respond immediately)
   - **HIGH**: Pushed PRs where bot responded (check for new reviews/merge)
   - **MEDIUM**: Committed PRs (create PR)
   - **NORMAL**: Start new development work
3. Executes based on current status:
   - **pending**: Implement, test, and commit → `committed`
   - **committed**: Push branch and create PR → `pushed` (lastActivityBy: "bot")
   - **pushed**: Check merge readiness FIRST, then:
     - If mergeable: Merge → `merged`
     - If reviewer commented: Implement feedback, push → (lastActivityBy: "bot")
     - If waiting: Skip to next story (will check again next iteration)
   - **merged**: Post-merge monitoring (CI breakage, flakes)
4. Updates progress in `data/progress.txt`
5. Repeats until all stories have terminal status (`merged`, `skipped`, or `invalid`)

**Story Lifecycle:**
```
     pending
        ↓
    (implement + test)
        ↓
    committed
        ↓
    (push + create PR)
        ↓
     pushed ◄─────────┐
    ╱   │   ╲         │
   ╱    │    ╲        │
merge  wait  review   │
  │     │     ↓       │
  │     │   (implement + test)
  │     │     ↓       │
  │     │   (commit + push)
  │     │     └───────┘
  ↓     │
merged  └─► (check again next iteration)
  ↓
(post-merge monitoring)
```

**Key Points:**
- Initial development: pending → (implement+test) → committed
- Review response: pushed → (implement+test) → (commit+push) → pushed
- **Same quality gates apply** — ALL tests must pass whether initial development or responding to reviews

**Anti-Stuck Guarantee:** Every `pushed` PR is checked for merge readiness on EVERY iteration, even when `lastActivityBy: "bot"`. Approved PRs never get stuck.

**Smart Waiting:** The bot tracks `lastActivityBy` to avoid spamming PRs while ensuring progress. See [docs/workflow-state-machine.md](docs/workflow-state-machine.md) for detailed flow.

## Prerequisites

- **Claude Code CLI** installed and configured
- **GitHub CLI** (`gh`) authenticated with your account
- **jq** for JSON parsing: `brew install jq` (macOS) or `sudo apt install jq` (Debian/Ubuntu)
- **flock** for run locking: `brew install flock` (macOS — included by default on Linux)
- **Git** configured with your credentials
- **Python 3** (for utility scripts)
- **signal-cli** (optional) — for Signal message notifications. See [Signal Notifications](#signal-notifications-optional) below
- Access to the target GitHub repositories

## Setup

### 1. Clone This Repository

Clone alongside (or near) your target project's repository:

```bash
git clone git@github.com:brave-experiments/brave-dev-loop.git my-project-bot
cd my-project-bot
```

### 2. Run Setup

```bash
make setup
```

The setup wizard handles everything interactively and is fully idempotent — safe to re-run at any time. It will:

1. **Create `config.json`** (if missing) — prompts for project name, GitHub org, PR/issue repositories, bot username/email, issue labels, and the SSH key the bot pushes with. If `config.json` already exists, current values are offered as defaults.
2. **Create data files** (`prd.json`, `run-state.json`, `progress.txt`) from templates if missing. Never overwrites existing files.
3. **Generate org members cache** (`.ignore/org-members.txt`) via GitHub API if missing.
4. **Configure git identity** in the target repo — repo-local `user.name`, `user.email`, and `core.sshCommand`.
5. **Install pre-commit hooks** for both the target repo and bot repo.
6. **Check the `gh` account** matching `bot.ghAccount` is authenticated.

**For existing brave-core deployments:** You can skip the wizard entirely by copying the reference config: `cp config.brave-core.json config.json`, then run `make setup` to complete the remaining steps.

### 3. Create Your PRD

**Option A: Use the `/prd` skill** (recommended)
```bash
claude
> /prd [describe your feature]
```
Follow the prompts, then use `/prd-json` to convert to `data/prd.json`.

**Option B: Manually edit `data/prd.json`**

```json
{
  "stories": [
    {
      "id": "STORY-1",
      "title": "Fix authentication test",
      "priority": 1,
      "status": "pending",
      "branchName": null,
      "prNumber": null,
      "prUrl": null,
      "lastActivityBy": null,
      "acceptanceCriteria": [
        "pnpm run test auth_tests"
      ]
    }
  ]
}
```

**Status values:** `"pending"` | `"committed"` | `"pushed"` | `"merged"` | `"skipped"` | `"invalid"`

**lastActivityBy values:** `null` (fresh) | `"bot"` (waiting for reviewer) | `"reviewer"` (bot should act)

## Usage

### Run the Bot

`run.sh` can be run from any directory — it resolves all paths from its own location:

```bash
./run.sh              # Default: 10 iterations
./run.sh 20           # Run 20 iterations
./run.sh 10 tui       # TUI mode (interactive terminal UI)
```

The bot runs on Claude Code by default. To use Codex or Cursor instead, pass
`--agent codex` / `--agent cursor`, set `BOT_AGENT=codex` / `BOT_AGENT=cursor`, or set
`bot.agent` to `codex` / `cursor` in `config.json` (precedence: flag > env var > config).
Codex must be installed and authenticated (`codex login`). Cursor must be installed
(`cursor-agent`) and authenticated (`cursor-agent login`, or set `CURSOR_API_KEY`).
Pass `--model <name>` or `--model=<name>` to override `bot.claudeModel`,
`bot.codexModel`, or `bot.cursorModel` for the selected agent.

```bash
./run.sh --agent codex
./run.sh --agent codex --model gpt-5
./run.sh --agent=codex --model=gpt-5
./run.sh --agent cursor
./run.sh --agent cursor --model sonnet-4-thinking
./run.sh --model opus
```

Skills (like `/review-prs`) are run interactively from the bot directory:

```bash
claude
> /review-prs 1d open
```

### Monitor Progress

```bash
tail -f data/progress.txt
```

### Stop the Bot

Press `Ctrl+C`. The bot will attempt to switch back to the master branch before exiting.

### Reset Between Runs

```bash
./scripts/reset-run-state.sh
```

Resets `data/run-state.json` for a fresh run while preserving configuration flags.

## Skills

Bot-only skills are available as slash commands in Claude Code. 8 bot-specific skills are included:

### PRD & Project Management

| Skill | Description |
|-------|-------------|
| `/prd` | Generate structured PRDs with clarifying questions |
| `/prd-json` | Convert PRD markdown to `prd.json` format |
| `/prd-clean` | Archive merged/invalid stories to `prd.archived.json` |
| `/add-backlog-to-prd` | Fetch open issues assigned to the bot and add to PRD, then recap and notify. The sync itself is `scripts/add-backlog-to-prd.py` — run it directly with `make backlog` if you don't need the recap |

### Code Review & Quality

| Skill | Description |
|-------|-------------|
| `/review-prs` | Batch review PRs for best practices violations. Supports auto mode for cron. Deduplicates against existing bot comments. Tracks prior comment context |
| `/learnable-pattern-search` | Analyze PR review comments to discover learnable patterns. Supports self-review mode to identify overly strict rules |
| `/update-best-practices` | Fetch and merge upstream Chromium documentation guidelines |

### Monitoring

| Skill | Description |
|-------|-------------|
| `/check-signal` | Check incoming Signal messages and execute commands |

### Developer Skills (target repo)

Developer-facing skills (commit, pr, review, preflight, etc.) are maintained in the target repository. Best practices docs location is configured via `bestPractices.docsDir`.

## Run State Configuration

`data/run-state.json` controls per-run behavior:

| Field | Description |
|-------|-------------|
| `runId` | Timestamp when this run started (auto-set) |
| `storiesCheckedThisRun` | Story IDs already processed in this run |
| `skipPushedTasks` | Skip all "pushed" PRs, only work on new development |
| `enableMergeBackoff` | Enable post-merge monitoring (default: true) |
| `mergeBackoffStoryIds` | Array of specific merged story IDs to check, or null for all |
| `lastIterationHadStateChange` | Tracks if work was done last iteration |

## Configuration Files

### config.json

Project-specific configuration (gitignored, created by `make setup`). Keys:

- `project.name`: Project name (e.g. `brave-core`)
- `project.org`: GitHub organization (e.g. `brave`)
- `project.prRepository`: PR repository as `owner/repo` (e.g. `brave/brave-core`)
- `project.issueRepository`: Issue repository as `owner/repo` (e.g. `brave/brave-browser`)
- `project.defaultBranch`: Default branch for the PR repo (e.g. `master`)
- `project.targetRepoPath`: Path to the target git repo (relative to the bot dir, to its parent, or absolute — all three resolve)
- `project.prdMode`: `curated` (default) — `data/prd.json` is authored and is the source of truth for what the bot works on. `auto` — the PRD is a cache with no manual curation: `run.sh` rebuilds it from assigned issues and open bot PRs before each run, using plain Python against the GitHub API (no agent, no tokens)
- `project.profile`: Which `projects/<name>/` profile supplies this project's rules — validation steps, test targets, and the project-specific docs the workflows point at. Omitted means `brave-core` (every deployment predating profiles is one); new setups get `default`
- `project.useFork`: `true` (default) — the bot pushes branches to its own fork (`origin` = fork, `upstream` = PR repo). `false` — the bot has write access to the PR repo and pushes there directly (`origin` = PR repo, no fork). Set `false` only when the bot account is a collaborator on the PR repo; `make setup` will then stop expecting a fork and stop offering to create one
- `bot.username`: Bot's GitHub username
- `bot.email`: Bot's email for git commits
- `bot.sshKeyPath`: SSH identity the bot pushes with (`null` = this machine's default key). See [Bot Identity Isolation](#bot-identity-isolation)
- `bot.ghAccount`: `gh` account whose token the bot uses (`null` = same as `bot.username`)
- `bot.ghConfigDir`: isolated `gh` config directory for the bot (`null` = use `~/.config/gh`). Set this to keep the bot's GitHub login out of your personal `gh` config entirely — no account is added, switched, or made active outside this repo. Create it with `GH_CONFIG_DIR=<dir> gh auth login`
- `bot.agent`: Which agent to run, `claude` (default), `codex`, or `cursor`
- `bot.claudeModel`: Claude model to use (`opus`, `sonnet`, etc.; overridden by `./run.sh --model` for Claude runs)
- `bot.claudeBin`: Path to the `claude` binary (`null` = found on PATH)
- `bot.codexModel`: Codex model to use (`null` = Codex default; overridden by `./run.sh --model` for Codex runs)
- `bot.codexBin`: Path to the `codex` binary (`null` = found on PATH)
- `bot.cursorModel`: Cursor model to use (`null` = account default; overridden by `./run.sh --model` for Cursor runs)
- `bot.cursorBin`: Path to the `cursor-agent` binary (`null` = found on PATH)
- `labels.prLabels`: Labels applied to bot-created PRs
- `labels.issueLabels`: Labels used for backlog issue fetching
- `bestPractices.docsDir`: Path to the docs directory containing best practices (relative to bot dir)

A `config.example.json` template and `config.brave-core.json` reference config are included.

### data/prd.json

Product Requirements Document defining user stories and acceptance criteria.

- `stories[].id`: Unique story identifier
- `stories[].priority`: Execution order (1 = highest)
- `stories[].status`: Story state
- `stories[].branchName`: Git branch name (set when work starts)
- `stories[].prNumber`: PR number (set when PR created)
- `stories[].prUrl`: PR URL (set when PR created)
- `stories[].lastActivityBy`: Who acted last — `"bot"` | `"reviewer"` | `null`
- `stories[].acceptanceCriteria`: Test commands that must pass

### data/progress.txt

Log of completed iterations:
- Codebase Patterns section (reusable patterns discovered during development)
- Per-iteration entries with status transitions, files changed, test results
- Learnings for future iterations

### .claude/CLAUDE.md

Agent instructions defining workflow, testing requirements, git operations, security guidelines, and quality standards.

## Best Practices

The bot enforces a comprehensive set of best practices organized by category:

- **Architecture** — Layering, dependency injection, factory patterns
- **Coding Standards** — Naming, CHECK vs DCHECK, IWYU, ownership, WeakPtr, base utilities
- **Testing** — Async patterns, JavaScript tests, navigation, test isolation
- **Frontend** — TypeScript/React, XSS prevention, component patterns
- **Build System** — BUILD.gn, buildflags, DEPS, GRD resources
- **Chromium src/ Overrides** — Override patterns, minimizing duplication
- **Documentation** — Inline comments, method docs

See `best_practices.md` in the target repo's docs directory (configured via `bestPractices.docsDir`) for the full index and quick checklist.

## Git Workflow

### Branch Management

Each user story gets its own branch, created automatically by the bot from `data/prd.json`.

### Bot Identity Isolation

The bot commits and pushes as a different GitHub account than the person who owns the machine. Left alone, git and `gh` both default to the machine owner: pushes to the bot's fork get rejected, and any that succeed are attributed to the wrong person.

Setup pins the bot's identity without touching anything global. Nothing here modifies `~/.gitconfig`, `~/.ssh/config`, or `gh`'s active account, so other repos and other terminals behave exactly as before.

| What | Where it is set | Scope |
| --- | --- | --- |
| `user.name`, `user.email` | `<target-repo>/.git/config` | That repo only |
| `core.sshCommand` | `<target-repo>/.git/config` | That repo only |
| `GIT_SSH_COMMAND` | exported by `run.sh` | The bot process and its children |
| `GH_TOKEN` | exported by `run.sh` | The bot process and its children |

The ssh command is `ssh -o IdentitiesOnly=yes -i <key>`. `IdentitiesOnly=yes` is required, not decoration: without it ssh-agent offers whatever keys it holds and GitHub authenticates as the owner of the first one accepted, ignoring `-i` entirely.

Setup verifies the chosen key with `ssh -T git@github.com` and warns if it authenticates as someone other than `bot.username`.

**Choosing a key.** Setup lists one entry per key, not one per file. Where a private key sits beside its `<key>.pub`, the `.pub` is offered: ssh matches it against ssh-agent and falls back to the private file beside it, so it works either way. Where the two halves are named differently (`netzenbot.ppk` + `netzenbot.pub`), the private file is offered instead — `-i` on that `.pub` has no `netzenbot` beside it to fall back to, so it only works while the agent holds the key. A pair setup cannot match by fingerprint, such as an encrypted PEM key, is listed under both names; there, prefer the `.pub`, since `-i` on the private file cannot reach the agent copy.

**Scheduled runs.** A passphrase-protected key only works while ssh-agent holds it. Runs started by `make schedules` have no agent, so use a dedicated passphrase-less key for the bot if you schedule it. Setup flags keys with `[needs ssh-agent]`.

**`gh` is separate.** The SSH key covers git transport only; `gh` carries its own stored token. `run.sh` reads the bot's token with `gh auth token --user <account>` and exports it as `GH_TOKEN` for that process. This reads the token without switching accounts — `gh auth switch` would change global state. To add the bot account:

```bash
gh auth login --hostname github.com   # makes the new account active
gh auth switch --user <your-own-login>  # switch back; both tokens stay stored
```

### Pre-commit Hooks

Two hooks are installed by `make setup`:

**Target repo hook** (`hooks/pre-commit`):
Blocks the configured bot account from modifying dependency files (package.json, DEPS, Cargo.toml, go.mod, etc.). Prevents bots from introducing external dependencies without review.

**Bot repo hook** (`hooks/pre-commit-bot-repo`):
Blocks committing `data/prd.json`, `data/progress.txt`, and `data/run-state.json` to ensure user-specific files don't get committed.

### Commit Format

```
feat: [Story ID] - [Story Title]
```

Commits must NOT include `Co-Authored-By` lines.

## Testing

### Acceptance Criteria

All acceptance criteria tests MUST run and pass before marking a story as complete. The bot will:
- Run ALL tests specified in acceptance criteria
- Use background execution for long-running tests
- Never skip tests regardless of duration
- Only mark a story complete when tests actually pass

### Test Suite

Run the automated test suite to verify setup:

```bash
./tests/test-suite.sh
```

Validates GitHub API integration, file structure, filtering scripts, pre-commit hooks, and configuration files.

## Security

### Prompt Injection Protection

The bot filters all GitHub data (issues, PR reviews) through security scripts that check authors against Brave org membership:

```bash
./scripts/filter-issue-json.sh 12345 markdown   # Filtered issue content
./scripts/filter-pr-reviews.sh 12345             # Filtered PR reviews
```

External user content is marked as `[filtered]` to prevent prompt injection attacks.

### Org Member Cache

Org membership is cached in `.ignore/org-members.txt` (survives reboots, unlike /tmp). The cache is required for `run.sh` to start — regenerate with `make setup` if missing.

### Trusted Reviewers Allowlist

When running with an external GitHub account that can't see private org membership:

```bash
cd scripts
cp trusted-reviewers.txt.example trusted-reviewers.txt
# Add one trusted username per line
```

### Other Security Measures

- **Dependency restrictions**: Pre-commit hook prevents bot from updating dependencies
- **Bot permissions**: Uses `--dangerously-skip-permissions` for autonomous operation
- **Review differentiation**: Review skill rejects fixes that aren't materially different from previous failed attempts

See [SECURITY.md](SECURITY.md) for complete guidelines.

## Signal Notifications (Optional)

The bot can send real-time notifications via Signal when key events happen (PR created, review comments posted, CI retriggered, etc.). This is entirely optional — everything works without it.

### Install signal-cli

```bash
brew install signal-cli
```

Or download manually from the [signal-cli releases page](https://github.com/AsamK/signal-cli/releases).

Requires Java 21+ (Homebrew handles this automatically).

### Register a Phone Number

You can register any phone number you own. Numbers must be in E.164 format (e.g., `+14155551234`).

#### Landline Registration (voice verification)

Signal requires an SMS attempt before allowing voice verification. For a landline, the SMS won't arrive — that's expected.

```bash
# Step 1: Solve the captcha
#   Go to https://signalcaptchas.org/registration/generate.html
#   Solve the captcha, then RIGHT-CLICK "Open Signal" and copy the link.
#   The link starts with signalcaptcha://

# Step 2: Request SMS verification with the captcha token (SMS won't arrive on landline — that's OK)
signal-cli -u +1YOURPHONENUMBER register --captcha "signalcaptcha://YOUR-CAPTCHA-TOKEN"

# Step 3: Wait at least 60 seconds for the SMS attempt to expire

# Step 4: Request voice verification (this will call your landline)
signal-cli -u +1YOURPHONENUMBER register --voice

# Step 5: Answer the call, note the 6-digit code, then verify
signal-cli -u +1YOURPHONENUMBER verify 123456
```

**Troubleshooting landline registration:**
- Steps 2 and 4 must be **separate commands**. Do NOT pass `--voice` and `--captcha` together — it will fail with `InvalidTransportModeException`.
- If step 4 fails with "Before requesting voice verification you need to request SMS verification and wait a minute", the SMS attempt (step 2) didn't register properly. Get a fresh captcha token and redo step 2 — tokens expire quickly.
- The voice call may have a **3-5 second silence** before the code is spoken. Use speakerphone and listen carefully. If no code is read, hang up and re-run step 4 to get a new call.
- If the voice call consistently has no audio, try a mobile number instead (see below).

#### Mobile Registration (SMS verification)

```bash
# Step 1: Solve captcha (same URL as above), then register
signal-cli -u +1YOURMOBILENUMBER register --captcha "signalcaptcha://YOUR-CAPTCHA-TOKEN"

# Step 2: Wait for SMS with code, then verify
signal-cli -u +1YOURMOBILENUMBER verify 123456
```

### Set Profile Name

After registration, set a display name so the recipient knows who's messaging:

```bash
signal-cli -u +1YOURPHONENUMBER updateProfile --given-name "Brave Core" --family-name "Bot"
```

### Configure Environment Variables

Add these to your `.envrc`:

```bash
export SIGNAL_SENDER="+14155551234"      # Your registered signal-cli number
export SIGNAL_RECIPIENT="+14155559876"   # Number to receive notifications
```

Then reload: `direnv allow`

### Test It

```bash
./scripts/signal-notify.sh "Hello from brave-dev-loop!"
```

You should receive the message on the recipient's Signal account.

### What Gets Notified

| Event | Example Message |
|-------|----------------|
| PR created | `PR created: #12345 - Fix auth test https://...` |
| PR merged | `PR merged: #12345 - Fix auth test https://...` |
| Review feedback addressed | `Review addressed: PR #12345 - removed unnecessary thread hop` |
| PR review comments posted | `Review complete: 5 PRs reviewed, 2 with violations, 4 comments posted` |
| CI jobs retriggered | `CI retriggered: PR #12345 - 3 jobs restarted (2 normal, 1 WIPE_WORKSPACE)` |
| Learnable patterns found | `Learnable patterns: analyzed 10 PRs, found 3 patterns, updated coding-standards.md` |
| Best practice PR created | `Best practice PR created: https://... - Adjust RunUntilIdle rule` |

### Graceful Degradation

The notification script (`scripts/signal-notify.sh`) silently does nothing if:
- `SIGNAL_SENDER` or `SIGNAL_RECIPIENT` env vars are not set
- `signal-cli` is not installed
- The message send fails for any reason

No skill or workflow will error due to missing Signal configuration.

## Archiving

When the branch changes between runs, previous state is automatically archived:

```
archive/
  └── 2026-01-30-old-branch/
      ├── data/prd.json
      └── data/progress.txt
```

## Project Structure

```
brave-dev-loop/                # (or your clone name)
├── README.md                  # This file
├── config.json                # Project config (gitignored, created by setup)
├── config.example.json        # Config template
├── SECURITY.md                # Security guidelines for GitHub data and prompt injection
├── LICENSE                    # MPL-2.0 license
├── Makefile                   # Dev commands: make test, lint, format, setup, schedules, backlog
├── run.sh                     # Main entry point (iterations, TUI mode)
├── data/
│   ├── prd.json               # Product requirements (gitignored)
│   ├── prd.example.json       # Example PRD template
│   ├── run-state.json         # Run state tracking (gitignored)
│   ├── run-state.example.json # Example run state template
│   ├── progress.txt           # Progress log (gitignored)
│   └── progress.example.txt   # Example progress template
├── .ignore/
│   └── org-members.txt        # Cached org members (security, gitignored)
├── .claude/
│   ├── CLAUDE.md              # Claude agent instructions
│   └── skills/                # Bot-only Claude Code skills (8 skills)
│       ├── prd/               # PRD generation
│       ├── prd-json/          # PRD to JSON converter
│       ├── prd-clean/         # Archive merged/invalid stories
│       ├── add-backlog-to-prd/  # Fetch issues from GitHub (wraps scripts/add-backlog-to-prd.py)
│       ├── review-prs/        # Batch PR review with caching
│       ├── check-signal/      # Check incoming Signal messages
│       ├── update-best-practices/ # Merge upstream Chromium guidelines
│       └── learnable-pattern-search/  # Analyze PR reviews for patterns
├── docs/                      # Detailed workflow and reference docs
│   ├── workflow-state-machine.md
│   ├── workflow-pending.md
│   ├── workflow-committed.md
│   ├── workflow-pushed.md
│   ├── workflow-merged.md
│   ├── workflow-skipped-invalid.md
│   ├── testing-requirements.md
│   ├── git-repository.md
│   ├── progress-reporting.md
│   ├── run-state-management.md
│   └── learnable-patterns.md
├── hooks/
│   ├── pre-commit             # Target repo hook (blocks dependency updates)
│   └── pre-commit-bot-repo    # Bot repo hook (blocks config file commits)
├── scripts/
│   ├── add-backlog-to-prd.py  # Sync assigned issues into prd.json (no LLM)
│   ├── sync-bot-prs-to-prd.py # Track untracked bot PRs as stories
│   ├── fetch-issue.sh         # Fetch filtered GitHub issues
│   ├── filter-issue-json.sh   # Filter issues to org members
│   ├── filter-pr-reviews.sh   # Filter PR reviews to org members
│   ├── signal-notify.sh       # Signal notification helper (optional)
│   └── trusted-reviewers.txt.example  # Allowlist template
├── logs/                      # Bot run logs
└── tests/
    ├── test-suite.sh          # Automated test suite
    └── README.md              # Test documentation
```

## Troubleshooting

### "Git user not configured"

Run `make setup` again or manually configure git in your target repo with the username/email from `config.json`.

### "Pre-commit hook blocks my commit"

If the bot account is trying to modify dependencies — this is intentional. Use existing libraries or a different git account.

### "Org members cache missing"

Run `make setup` to regenerate `.ignore/org-members.txt`, or manually:
```bash
gh api /orgs/<your-org>/members --paginate --jq '.[].login' > .ignore/org-members.txt
```

### "Tests are taking too long"

Expected. The bot uses `run_in_background: true` and high timeouts (1-2 hours) for long operations.

### "Build failures after sync"

The bot will automatically rebase and re-sync:
```bash
git fetch
git rebase origin/master
pnpm run sync --no-history
```

## Contributing

When adding new patterns or learnings, see [docs/learnable-patterns.md](docs/learnable-patterns.md) for guidance on where each type of pattern belongs.

## License

This project is licensed under the Mozilla Public License 2.0 (MPL-2.0). See the [LICENSE](LICENSE) file for details.

## Support

For issues or questions:
- Check `data/progress.txt` for detailed logs
- Review `.claude/CLAUDE.md` for agent behavior
- Verify configuration with `make setup`
- Run `./tests/test-suite.sh` to validate setup
