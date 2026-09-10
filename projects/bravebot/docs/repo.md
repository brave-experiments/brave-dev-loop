# bravebot: Repository

Project-specific repository rules. Read alongside `docs/git-repository.md`.

The target repo is a standalone checkout — a Rust workspace at its root, with
no surrounding source tree. Nothing above it is ever touched, and stories do not
work in the checkout itself either: each one gets a worktree of its own.

Because of that, this profile declares `"worktrees": true` and may run several
`run.sh` instances at once — see [Concurrent runs](../../../docs/concurrent-runs.md).
Everything below assumes another run could be working a different story in a
sibling worktree while you work.

## Worktrees

**Every story works in its own git worktree. Nothing is ever built, edited,
branched or committed in the main checkout.**

- **Path**: `../bravebot-<issue-number>` — a sibling of the main checkout, so
  issue #133 gets `../bravebot-133`. Resolve it the way the bot config's
  `project.targetRepoPath` resolves, and use absolute paths in commands: the
  agent's working directory is not the bot directory.
- **The issue number** is the story's `issueNumber` field, or the `#<n>` in its
  `description` (`"Resolve issue #133: …"`). A story with neither uses its
  lowercased id instead: `../bravebot-us-004`.
- **Everywhere else means this worktree.** Wherever a shared workflow doc says
  `[targetRepoPath from bot config]`, or a story's acceptance criteria name a
  path inside `../bravebot`, read it as the same path inside the worktree. Every
  `cd`, `git`, `cargo`, `make`, and file edit happens there. The only commands
  that run against the main checkout are the `git worktree` ones below.
- **The main checkout stays on `main`.** Nothing is ever built or committed
  there. `run.sh` leaves it alone under this profile — several runs may share
  it — so anything you leave in it stays until someone cleans it up.

### Entering the worktree — the first step of every iteration

A story spans several iterations and each iteration is a fresh agent, so start
by finding out whether the worktree already exists:

```sh
MAIN=<absolute targetRepoPath from the bot config>   # …/bravebot
WORK="$MAIN-<issue>"                                 # …/bravebot-133
git -C "$MAIN" worktree list
```

Another run may be doing the same thing in the same `.git` at the same moment,
so every command that touches the *shared* repository — `fetch`, `worktree
add`, `worktree remove`, `worktree prune` — goes through the repo lock:

```sh
LOCK=<bot dir>/scripts/git-repo-lock.sh
"$LOCK" "$MAIN" -- git -C "$MAIN" fetch origin
```

Commands inside `$WORK` (build, test, commit, rebase) need no lock: the
worktree is yours alone.

If `$WORK` is listed, `cd "$WORK"` and carry on — do not create a second one.

Otherwise create it. A new story gets its branch from the worktree command, so
there is no separate `git checkout -b`:

```sh
"$LOCK" "$MAIN" -- git -C "$MAIN" fetch origin
"$LOCK" "$MAIN" -- git -C "$MAIN" worktree add -b <branch-name> "$WORK" origin/main
cd "$WORK"
```

A story that already has a `branchName` — a later iteration, or one whose
worktree was removed — reuses that branch:

```sh
"$LOCK" "$MAIN" -- git -C "$MAIN" fetch origin
"$LOCK" "$MAIN" -- git -C "$MAIN" worktree add "$WORK" <branch-name>   # branch exists locally
"$LOCK" "$MAIN" -- git -C "$MAIN" worktree add --track -b <branch-name> "$WORK" origin/<branch-name>
```

Never `git checkout main` inside the worktree: the main checkout holds that
branch and the checkout fails. Stay on the story's branch for the story's life.

### What a worktree shares, and what it does not

`.git` is shared, so the bot's git identity and the pre-commit dependency guard
`make setup` installs both apply, and a `git fetch` in either place updates both.
Untracked files are not shared: `target/` starts empty and the first
`cargo build` in a new worktree is a cold one. Budget for it — that build, not
the worktree, is what makes the first iteration slow.

### Removing

Remove the worktree only after the story's post-merge bookkeeping is done:

```sh
"$LOCK" "$MAIN" -- git -C "$MAIN" worktree remove "$WORK"
"$LOCK" "$MAIN" -- git -C "$MAIN" worktree prune
```

`worktree remove` refuses when the worktree has uncommitted changes. That is the
correct outcome: commit or discard them deliberately, and never `--force` past
it. Leave the worktree in place for a story that is still pending, committed, or
pushed — a stale directory is cheap, a lost branch is not.

## Package managers

`cargo` owns the code. `npm` exists only for the published wrapper package, and
`package-lock.json` is lint-checked in CI, so never hand-edit it and never run
`npm install` when `npm ci` will do.

- Dependency changes need a maintainer's agreement first. A new crate widens the
  supply-chain surface of a binary people install.
- `Cargo.lock` is committed and every CI build passes `--locked`. A build that
  wants to rewrite it means the manifest and the lockfile disagree — fix the
  manifest, do not drop the flag.

## Version bumps

`make bump-version` is a release step, not something a story does. Leave the
version alone unless the story is the release itself.
