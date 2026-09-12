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

## Hooks

Three hooks are installed by `make setup`:

**Target repo pre-commit** (`hooks/pre-commit`):
Blocks the configured bot account from modifying dependency files (package.json, DEPS, Cargo.toml, go.mod, etc.). Prevents bots from introducing external dependencies without review.

**Target repo pre-push** (`hooks/pre-push`):
Refuses a push whose new commits are authored or committed by anybody other than the `user.name` and `user.email` set above. Commits already on a remote-tracking ref are not its subject, so a branch rebased onto upstream work is not blamed for who wrote that work.

It also checks which account would open the pull request, because `gh pr create` reads no git config at all: a branch every commit of which is correctly authored and signed can still be followed by a pull request opened by the machine owner, and a pull request's author cannot be reassigned afterwards, only closed and opened again. Where `GH_TOKEN` is already exported, as `run.sh` does, the hook trusts it and says nothing. Otherwise, if `gh`'s active account is not the bot and a stored token for the bot exists, it refuses the push and names the one-line fix:

```bash
export GH_TOKEN=$(gh auth token --user <bot-account>)
```

That pins `gh` for one shell, leaving its active account and every other terminal alone.

**Bot repo pre-commit** (`hooks/pre-commit-bot-repo`):
Blocks committing `data/prd.json`, `data/progress.txt`, and `data/run-state.json` to ensure user-specific files don't get committed.

Both target-repo hooks are inert in a checkout whose `user.name` is not the bot: each exits at its first check having done nothing, so a person's clone of the same repository behaves as if neither were there.

**Where a target repo's hook lands.** Not `<repo>/.git/hooks` unconditionally. `core.hooksPath` *replaces* that directory rather than adding to it, so in a target repo that sets it — as one with its own checked-in hooks does — a hook written to `.git/hooks` never runs and never says so. Setup resolves the configured path and installs into that.

Two things follow from the resolved directory usually being inside the working tree. Setup adds an entry to the target repo's `.git/info/exclude`, which is local to the clone, so the installed file does not show up as untracked and no tracked `.gitignore` is touched. And where the target repo already tracks a hook of that name, setup installs nothing and says so, rather than overwriting a file somebody committed.

## Commit Format

```
feat: [Story ID] - [Story Title]
```

Commits must NOT include `Co-Authored-By` lines.
