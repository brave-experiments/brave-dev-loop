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
| `merged` | watch for CI breakage and flakes | terminal |

Stories are chosen reviewer-first, so a PR with a waiting reviewer is always
handled before new development starts. Every `pushed` PR is re-checked for
merge readiness on every iteration, so an approved PR can never get stuck.

See [docs/workflow-state-machine.md](docs/workflow-state-machine.md) for the
full state machine and selection tiers.

## Prerequisites

- **Claude Code CLI** (or `codex` / `cursor-agent`, per `bot.agent`)
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

For an existing brave-core deployment, `cp config.brave-core.json config.json`
first to skip the wizard, then run `make setup`.

## Usage

`run.sh` works from any directory; it resolves paths from its own location.

```bash
./run.sh              # 10 iterations (default)
./run.sh 20           # 20 iterations
./run.sh 10 tui       # interactive terminal UI

./run.sh --agent codex --model gpt-5
./run.sh --agent cursor
./run.sh --model opus
```

Agent precedence is `--agent` flag > `BOT_AGENT` env var > `bot.agent` in
config. `--model` overrides the model for whichever agent is selected.

```bash
tail -f data/progress.txt        # watch progress
make schedules                   # install/update cron jobs
make view-schedules              # show the current schedule
./scripts/reset-run-state.sh     # reset run state between runs
```

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
| [Project profiles](projects/README.md) | Per-project validations, test targets, docs |
| [Skills](docs/skills.md) | The slash commands this repo provides |
| [Bot identity](docs/bot-identity.md) | Signing, SSH keys, `gh` isolation, hooks |
| [State machine](docs/workflow-state-machine.md) | Task selection and status transitions |
| [Testing requirements](docs/testing-requirements.md) | What the bot must run before claiming success |
| [Signal notifications](docs/signal-notifications.md) | Optional real-time alerts |
| [Troubleshooting](docs/troubleshooting.md) | Common failures |
| [Security](SECURITY.md) | Prompt-injection handling for GitHub data |

Per-status workflow docs live in `docs/workflow-*.md`, and the agent's own
instructions in [.claude/CLAUDE.md](.claude/CLAUDE.md).

## Development

```bash
make test     # pytest
make lint     # ruff check + format --check
make format   # ruff check --fix + format
```

`./tests/test-suite.sh` validates an installation end to end.

## License

[MPL-2.0](LICENSE).
