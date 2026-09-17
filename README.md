# brave-dev-loop

An autonomous coding loop. It picks up work from a backlog, implements it, runs
the tests, opens a PR, responds to review feedback, and follows the PR through
to merge — repeating until there is nothing left to do.

The loop is the product; the agent is swappable. `bot.agent` selects Claude
Code (default), Codex, or Cursor.

One clone targets one project, configured by `config.json`. To drive several
repositories, clone once per project.

## How it works

Each iteration picks a single story and acts on its status:

| Status | Action | Next |
| --- | --- | --- |
| `pending` | implement, test, commit | `committed` |
| `committed` | push branch, open PR | `pushed` |
| `pushed` | merge if ready, otherwise address review feedback | `merged` / `pushed` |
| `merged` | nothing — terminal | terminal |

Stories are chosen reviewer-first, so a PR with a waiting reviewer is always
handled before new development starts. Every `pushed` PR is re-checked for
merge readiness on every iteration, so an approved PR can never get stuck.

See [docs/workflow-state-machine.md](docs/workflow-state-machine.md) for the
full state machine and selection tiers.

## Prerequisites

- **Claude Code CLI** (or `codex` / `cursor-agent` / `bravebot`, per `bot.agent`)
- **GitHub CLI** (`gh`), authenticated
- **Git**, **Python 3**
- **jq** — `brew install jq` / `apt install jq`
- **flock** (optional) — used for run locking when present; without it a directory-based lock is used instead
- Access to the target GitHub repositories

## Setup

```bash
git clone git@github.com:brave-experiments/brave-dev-loop.git my-project-loop
cd my-project-loop
make setup
```

`make setup` is interactive and idempotent — safe to re-run at any time. It
creates `config.json` and the data files, configures the bot's git identity and
commit signing, installs the pre-commit hooks, caches org membership, and
repairs configuration left over from older versions.

To skip the wizard, copy the reference config for the deployment you want over
`config.json` first, then run `make setup`:

```bash
cp config.brave-core.json config.json     # the brave-core deployment
cp config.brave-dev-loop.json config.json # the loop developing itself
```

The second one is self-hosted: the target repo is the clone you are standing in,
and each story works in a `../brave-dev-loop-<issue>` worktree of it. See
[projects/brave-dev-loop/docs/repo.md](projects/brave-dev-loop/docs/repo.md).

## Usage

`run.sh` works from any directory; it resolves paths from its own location.

```bash
./run.sh              # 10 iterations (default)
./run.sh 20           # 20 iterations
./run.sh 10 tui       # interactive terminal UI

./run.sh --agent codex --model gpt-5
./run.sh --agent cursor
./run.sh --agent bravebot
./run.sh --agent bravebot --agent-bin ~/bravebot/target/release/bravebot
./run.sh --model opus

./run.sh 1 --agent bravebot --comparison-run   # do the story twice and critique the first

./run.sh --status     # what is running in this directory
```

Agent precedence is `--agent` flag > `BOT_AGENT` env var > `bot.agent` in
config. `--model` overrides the model for whichever agent is selected, and
`--agent-bin` overrides the binary it runs from — which is how bravebot is
pointed at a local build, since it has no config keys of its own.

```bash
tail -f data/progress.txt        # watch progress
make schedules                   # install/update this project's cron jobs
make view-schedules              # show the current schedule
make unmount-worktrees           # remove idle story worktrees (ALL=1 for all)
./scripts/reset-run-state.sh     # reset run state between runs
./scripts/reset-run.sh --stale   # clear slots left behind by a killed run
```

Story worktrees are collected by every run, so `make unmount-worktrees` is for
reclaiming the disk now — the `../<repo>-<issue>` checkouts and their build
directories, a few gigabytes each. It removes the ones idle for over 24 hours;
`ALL=1` removes every one whatever it holds, and `DRY_RUN=1` only reports. See
[Worktrees between runs](docs/configuration.md#worktrees-between-runs) for what
that can lose (uncommitted changes, and nothing else) and what is kept regardless.

To look at what a pull request actually did, open a shell in the worktree that
holds its branch:

```bash
make worktree PR=https://github.com/brave/bravebot/pull/351
make worktree PR=351             # the repo comes from config.json
make worktree                    # asks which pull request
```

An existing worktree is found by branch rather than by directory name, since a
worktree is named after its story's issue and not its branch. When none has the
branch, one is created from the pull request's head — in `<target repo>-<issue>`,
the directory that story's own worktree would use. Either way the main
checkout's `.envrc` is copied in and allowed, because untracked files are not
shared between worktrees and a fresh one would otherwise have no environment.
Exit the shell to come back. No model is involved, and nothing is committed or
pushed.

Scheduled jobs are per-project: `make schedules` installs one crontab block for
the project in `config.json` and leaves any other deployment's block alone, and
what goes in it comes from `projects/<profile>/schedules.sh` — see
[Project profiles](projects/README.md#schedules).

Several runs can share one bot directory — set `bot.maxConcurrentRuns` and see
[Concurrent runs](docs/concurrent-runs.md). It is off by default.

`--comparison-run` redoes each story with a second tool in a throwaway worktree
and files issues for what the first tool did worse. Also off by default, and it
triples the cost of an iteration: see [Comparison runs](docs/comparison-runs.md).

A run titles its terminal tab `#<issue> PR #<pr> <story title>`, so tabs are
told apart by the numbers you search GitHub by; the PR number joins the title
as soon as the story has one, mid-iteration. Stories with neither number show
their story id instead.

Stop with `Ctrl+C`; the bot returns the target repo to its default branch on
the way out.

Skills are run interactively from the bot directory:

```bash
claude
> /review-prs 1d open
```

## Documentation

| | |
| --- | --- |
| [Configuration](docs/configuration.md) | `config.json` keys, PRD format, run state |
| [Concurrent runs](docs/concurrent-runs.md) | Run slots, claims, `--status`, clearing a killed run |
| [Project profiles](projects/README.md) | Per-project validations, test targets, docs |
| [Comparison runs](docs/comparison-runs.md) | `--comparison-run`: redo a story with another tool and critique the first |
| [Skills](docs/skills.md) | The slash commands this repo provides |
| [Development](docs/development.md) | Working on the loop itself: tests, lint, the security scan |
| [Bot identity](docs/bot-identity.md) | Signing, SSH keys, `gh` isolation, hooks |
| [State machine](docs/workflow-state-machine.md) | Task selection and status transitions |
| [Testing requirements](docs/testing-requirements.md) | What the bot must run before claiming success |
| [PR descriptions](docs/pr-descriptions.md) | The body shape reviewers get, and the checker that enforces it |
| [Signal notifications](docs/signal-notifications.md) | Optional real-time alerts |
| [Troubleshooting](docs/troubleshooting.md) | Common failures |
| [Security](SECURITY.md) | Prompt-injection handling for GitHub data |

Per-status workflow docs live in `docs/workflow-*.md`, and the agent's own
instructions in [.claude/CLAUDE.md](.claude/CLAUDE.md).

## Development

```bash
make test              # pytest
make lint              # ruff check + format --check
make format            # ruff check --fix + format
make check-reviewdog   # the brave/security-action scan, on this branch's changes
make check             # all three: the before-you-push pass
```

`./tests/test-suite.sh` validates an installation end to end.

A pull request here gets lint and the tests from
[`.github/workflows/lint-and-test.yml`](.github/workflows/lint-and-test.yml), and
the security scan from an organization-level workflow — `make check-reviewdog` is
that scan locally. See [Development](docs/development.md).

## License

[MPL-2.0](LICENSE).
