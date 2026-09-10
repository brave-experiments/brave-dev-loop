# Concurrent runs

Several `run.sh` instances can share one bot directory. Each takes a numbered
**run slot** and works a different story.

Off by default. A deployment that never sets `bot.maxConcurrentRuns` behaves
exactly as it did before slots existed, down to the paths it writes.

```bash
./run.sh --status            # what is running here
./run.sh 3                   # takes the first free slot
./scripts/reset-run.sh --stale   # clear orphaned slots, leave live runs alone
```

## Turning it on

```json
{ "bot": { "maxConcurrentRuns": 2 } }
```

`run.sh` refuses anything above 1 unless the active project profile declares
`"worktrees": true` in its `profile.json`. Without per-story worktrees two
runs share one working tree and one branch, and they overwrite each other's
work — no amount of locking elsewhere fixes that. A profile that commits
directly in the target checkout cannot enable this until it moves stories into
worktrees of their own; the profile's `docs/repo.md` states which it does.

Nothing schedules extra runs. The cron jobs installed by `make schedules` run
one `run.sh`, which takes whichever slot is free — so a scheduled run and a
run you started by hand now coexist instead of one of them exiting.

## What a slot owns

| | slot 1 | slot N |
| --- | --- | --- |
| lock | `.run.lock` | `.run.slot-N.lock` |
| run state | `data/run-state.json` | `data/run-state.slot-N.json` |
| iteration log | `logs/iteration-<runId>-slot-1-loop-K.log` | `…-slot-N-…` |
| metadata | `data/runs/slot-1.json` | `data/runs/slot-N.json` |

Slot 1 deliberately keeps the paths it always had.

Operator settings — `skipPushedTasks`, `enableMergeBackoff`,
`mergeBackoffStoryIds` — are **not** per slot. They are read from
`data/run-state.json` and copied into each slot's file at run start, so
setting them in one place still configures every slot.

## Knowing what is running

A lock file is an inode to lock. It outlives every run, and its existence
means nothing. `./run.sh --status` asks the kernel instead, and reports one of
three states per slot:

- **IDLE** — the lock is free. Nothing is running.
- **RUNNING** — a live run holds it. Shows its pid, story, start time, and how
  long since it last wrote to its iteration log. A long silence there is the
  hang signal (an iteration is hard-capped at 2h by `timeout-tree.sh`).
- **ORPHANED** — the lock is held but the run that took it is gone: a child
  inherited the lock fd and outlived its parent. `./scripts/reset-run.sh
  --slot N` clears it.

`./run.sh --status --json` prints the same thing for scripting.

## How the pieces stay out of each other's way

**The slot lock** is held by the kernel — `flock(1)` where it exists,
otherwise `fcntl.flock` on an fd the shell holds (see `scripts/lib/lock.sh`).
`kill -9` releases it. There is no stale lock to clear and no timeout to tune.
A machine with neither `flock` nor `python3` falls back to a `mkdir` lock,
which does need the pid check it has always had.

**The agent is started through `scripts/exec-clean.py`**, which closes every
inherited file descriptor above stdio. A `flock` lives on the open file
description, so any child that inherits the lock fd keeps the slot held after
the run itself is gone — and `somecmd 200>&-` does not prevent that on the
bash 3.2 macOS ships, because applying that redirection makes bash duplicate
the fd to a free one near 10 first, and children inherit the duplicate. The
`tee` that writes the iteration log gets the same treatment: it sits waiting
on the pipe and would outlive a killed run.

**Story claims** stop two runs picking the same story. `select-task.py`
filters, selects and claims inside one PRD lock, writing to
`data/claims.json`. A claim counts only while the claiming run still holds its
slot lock, so a killed run's stories become available again immediately —
nothing expires, nothing is reclaimed by a timer.

```bash
./scripts/claims.py list                  # who is working what
./scripts/claims.py release --story US-4  # hand one back by hand
```

**`data/prd.json`** is written under an exclusive lock
(`data/.prd.lock`, see `scripts/lib/prd_store.py`) by everything that touches
it: `select-task.py`, `update-prd-status.py`, both syncs, `archive-prd.py`.
Read, change, write is one critical section, so no update is lost. Never
hand-edit the PRD while runs are live; use `scripts/update-prd-status.py`.

**`data/progress.txt`** is appended through `scripts/append-progress.sh`,
which takes a lock so two agents' entries cannot interleave mid-block.

**The shared `.git`** of a worktree checkout is guarded by
`scripts/git-repo-lock.sh` for operations against the repository itself
(`fetch`, `worktree add`) — git's own ref locks fail under concurrency. Work
inside a worktree needs no lock.

**The exit trap** does not touch the main checkout under a worktree profile.
It still stashes and returns to the default branch for a non-worktree profile,
which is what a single-run deployment expects.

## When something goes wrong

```bash
./run.sh --status                  # first, always
./scripts/reset-run.sh --stale     # clear orphans only; live runs untouched
./scripts/reset-run.sh --slot 2    # stop slot 2, free it, reset its state
./scripts/reset-run.sh             # stop everything (the old behaviour)
```

`reset-run.sh` kills the run *and* any child still holding the lock fd, then
confirms the lock actually came free. It skips the target-repo sync while any
other slot is still running.

Prefer it to `kill -9` on the run itself. Killing `run.sh` frees its slot
immediately — that is the point of the kernel lock — but the agent it started
is a separate process tree and keeps going, still writing to its worktree and
possibly to GitHub. `reset-run.sh --slot N` stops the whole tree. `Ctrl+C` is
also clean: the exit trap releases the claim and the slot.
