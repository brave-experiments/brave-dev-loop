# Configuration

Every file here is per-deployment and gitignored; `make setup` creates them.

## config.json

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
- `bot.sshKeyPath`: SSH identity the bot pushes with (`null` = this machine's default key). See [Bot identity](./bot-identity.md)
- `bot.signingKeyPath`: public key the bot signs commits and tags with (`null` = the same key it pushes with, `bot.sshKeyPath`). Signing with any other key — including the machine owner's — makes GitHub mark every bot commit Unverified. The key must also be registered on the bot's GitHub account as a **signing** key; authentication keys are a separate list
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

## data/prd.json

Product Requirements Document defining user stories and acceptance criteria.

- `stories[].id`: Unique story identifier
- `stories[].priority`: Execution order (1 = highest)
- `stories[].status`: Story state
- `stories[].branchName`: Git branch name (set when work starts)
- `stories[].prNumber`: PR number (set when PR created)
- `stories[].prUrl`: PR URL (set when PR created)
- `stories[].lastActivityBy`: Who acted last — `"bot"` | `"reviewer"` | `null`
- `stories[].acceptanceCriteria`: Test commands that must pass

## data/progress.txt

Log of completed iterations:
- Codebase Patterns section (reusable patterns discovered during development)
- Per-iteration entries with status transitions, files changed, test results
- Learnings for future iterations

## .claude/CLAUDE.md

Agent instructions defining workflow, testing requirements, git operations, security guidelines, and quality standards.

# Run State Configuration

`data/run-state.json` controls per-run behavior:

| Field | Description |
|-------|-------------|
| `runId` | Timestamp when this run started (auto-set) |
| `storiesCheckedThisRun` | Story IDs already processed in this run |
| `skipPushedTasks` | Skip all "pushed" PRs, only work on new development |
| `enableMergeBackoff` | Enable post-merge monitoring (default: true) |
| `mergeBackoffStoryIds` | Array of specific merged story IDs to check, or null for all |
| `lastIterationHadStateChange` | Tracks if work was done last iteration |

## Archiving

When the branch changes between runs, previous state is automatically archived:

```
archive/
  └── 2026-01-30-old-branch/
      ├── data/prd.json
      └── data/progress.txt
```
