# brave-dev-loop: Repository

Project-specific repository rules. Read alongside `docs/git-repository.md`.

The target repo is the bot directory: `project.targetRepoPath` is `.`, so the
loop develops the checkout it is running from. Every rule below follows from
that one fact — a story edits the files the session executing it is reading.

## Worktrees

**Every story works in its own git worktree. Nothing is ever edited, tested or
committed in the main checkout.**

Two things happen to an edit left in the main checkout, and neither reports
itself:

- The next iteration is a fresh process that re-reads `run.sh`, `scripts/` and
  `projects/` from there. A half-finished edit is not a draft; it is the loop
  the next iteration runs, and the story that wrote it is the one it breaks.
- Every cron job's prologue is `git fetch origin && git checkout master &&
  git reset --hard origin/master` on this repository. Uncommitted work in the
  main checkout is gone at the top of the next job, silently.

Worktrees are untouched by that reset: it moves one working tree and the branch
`master` points into it, and a worktree on its own branch is neither.

- **Path**: `../brave-dev-loop-<issue-number>` — a sibling of the main
  checkout, so issue #42 gets `../brave-dev-loop-42`. Resolve it the way the bot
  config's `project.targetRepoPath` resolves, and use absolute paths in
  commands: the agent's working directory is not the bot directory.
- **The issue number** is the story's `issueNumber` field, or the `#<n>` in its
  `description`. A story with neither uses its lowercased id instead:
  `../brave-dev-loop-us-004`.
- **Everywhere else means this worktree.** Wherever a shared workflow doc says
  `[targetRepoPath from bot config]`, read it as the worktree. Every `cd`, `git`,
  `make`, `python3` and file edit happens there. The only commands that run
  against the main checkout are the `git worktree` ones below, and the read-only
  ones this doc names.

### Entering the worktree — the first step of every iteration

A story spans several iterations and each iteration is a fresh agent, so start
by finding out whether the worktree already exists:

```sh
MAIN=<absolute targetRepoPath from the bot config>   # the bot directory itself
WORK="$MAIN-<issue>"                                 # …/brave-dev-loop-42
git -C "$MAIN" worktree list
```

Another run may be in the same `.git` at the same moment, so every command that
touches the *shared* repository — `fetch`, `worktree add`, `worktree remove`,
`worktree prune` — goes through the repo lock:

```sh
LOCK=<bot dir>/scripts/git-repo-lock.sh
"$LOCK" "$MAIN" -- git -C "$MAIN" fetch origin
```

Commands inside `$WORK` (test, lint, commit, rebase) need no lock: the worktree
is yours alone.

If `$WORK` is listed, `cd "$WORK"` and carry on — do not create a second one.
Otherwise create it, branch and all:

```sh
"$LOCK" "$MAIN" -- git -C "$MAIN" fetch origin
"$LOCK" "$MAIN" -- git -C "$MAIN" worktree add -b <branch-name> "$WORK" origin/master
cd "$WORK"
```

A story that already has a `branchName` — a later iteration, or one whose
worktree was collected — reuses that branch:

```sh
"$LOCK" "$MAIN" -- git -C "$MAIN" worktree add "$WORK" <branch-name>   # branch exists locally
"$LOCK" "$MAIN" -- git -C "$MAIN" worktree add --track -b <branch-name> "$WORK" origin/<branch-name>
```

Never `git checkout master` inside the worktree: the main checkout holds that
branch and the checkout fails. Stay on the story's branch for the story's life.

**The base is `origin/master`, and there is no `upstream`.** `project.useFork`
is false here: `origin` is `brave-experiments/brave-dev-loop` itself, not a fork
of it, and setup adds no second remote. So wherever a shared workflow doc says
`upstream/master` — the rule that keeps a fork's stale default branch out of a
story — read `origin/master`. It is also the ref the cron prologue resets the
main checkout to, so a branch based on it starts where the deployment stands.

### What a worktree shares, and what it does not

`.git` is shared, so the bot's git identity and the hooks in the common hooks
directory both apply, and a `git fetch` in either place updates both.

Untracked files are not shared, and in this repository that is most of the
configuration: `config.json`, `data/prd.json`, `data/progress.txt`,
`data/run-state*.json` and `.envrc` are all gitignored and none of them arrives
with `worktree add`. What follows from that:

- **The deployment's live state is not in your worktree, and cannot be reached
  by accident.** Tests that write into `data/` write into the worktree's copy of
  the tracked examples. Keep it that way: a test belongs in `tmp_path`, never in
  a path relative to the checkout.
- **A fresh worktree has no `.venv`.** The Makefile falls back to `python3` and
  `ruff` on `PATH`, which is how `make test` and `make lint` run there. Nothing
  to install.
- **`.envrc` arrives anyway.** The post-checkout hook `make setup` installs
  copies it and `.env` from the main checkout and runs `direnv allow`. It has
  already happened by the time `worktree add` returns.

### Removing

Nothing to do: `run.sh` collects worktrees itself, at the end of a session and
again at the start of the next one, and `scripts/clean-worktrees.py` decides. It
removes only a worktree holding nothing — no uncommitted changes, no commit
missing from every remote — and never one a live run claims.

So leave the directory where it is, and do not `--force` past a `worktree
remove` that refuses: the changes it refused over are the reason.

## A change is not live until it merges

The instructions the session is following, the `run.sh` driving it and the
scripts it calls all come from the main checkout, at the commit the prologue
reset to. The worktree's copies are inert for the length of the story.

- Verify a change with its tests, not by watching the loop behave differently.
  It will not.
- Do not "try it out" by editing the main checkout. See above for what that
  costs.
- It goes live when a later cron job resets the main checkout onto the merge.

## One remote, no fork

`origin` is `brave-experiments/brave-dev-loop`, the repository the pull requests
go to. Branches are pushed straight there and the pull request is opened against
`master` in the same repository — no fork to keep current, no cross-repository
push, and no `upstream` remote at all.

`scripts/sync-target-repo.sh` therefore has nothing to do here and says so: it
exists to move a fork's default branch onto upstream's. What keeps this checkout
current is the reset in every cron job's prologue.

## Runtime state is not yours to commit

`data/prd.json`, `data/progress.txt`, `data/run-state*.json` and
`data/claims.json` hold this machine's live queue. They are gitignored and the
`pre-commit` hook refuses them for every user, `git add -f` included.

A story that changes the shape of one of them changes the tracked example
(`data/*.example.json`) and whatever reads the old shape. The live file is the
deployment's, and the next hard reset is not a migration.

## Hooks apply to this checkout too

Because the target repo is this repo, the hooks `docs/bot-identity.md` describes
are installed here rather than somewhere else: `pre-commit` as above, `pre-push`
refusing a push whose commits are not the bot's or are unsigned, and
`post-checkout` for `.envrc`. Signing needs the key loaded in `ssh-agent`;
`--no-gpg-sign` is not the way past a commit that fails without it.

## Dependencies

The scripts are standard-library Python and bash. `ruff` and `pytest` are dev
tools, installed by `scripts/install-dev-deps.sh`, and there is no dependency
manifest to update. A story that wants a third-party import needs a maintainer's
agreement first: this repository is what runs unattended over other people's
checkouts.
