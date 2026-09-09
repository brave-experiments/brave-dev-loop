# Bot identity

How the bot commits, signs and pushes as its own GitHub account without touching the machine owner's global git or `gh` setup.

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

## Pre-commit Hooks

Two hooks are installed by `make setup`:

**Target repo hook** (`hooks/pre-commit`):
Blocks the configured bot account from modifying dependency files (package.json, DEPS, Cargo.toml, go.mod, etc.). Prevents bots from introducing external dependencies without review.

**Bot repo hook** (`hooks/pre-commit-bot-repo`):
Blocks committing `data/prd.json`, `data/progress.txt`, and `data/run-state.json` to ensure user-specific files don't get committed.

## Commit Format

```
feat: [Story ID] - [Story Title]
```

Commits must NOT include `Co-Authored-By` lines.
