# brave-dev-loop: Testing

Project-specific test and gate commands. Read alongside
`docs/testing-requirements.md`.

Every command here runs in the story's worktree
(`../brave-dev-loop-<issue-number>`), never in the main checkout — see
[repo.md](./repo.md#worktrees). A fresh worktree needs no setup: the Makefile
falls back to `python3` and `ruff` on `PATH` when there is no `.venv`.

## Running tests

```sh
make test                                   # the whole suite, about 80 seconds
python3 -m pytest tests/test_scripts.py -v  # one file
python3 -m pytest tests/test_scripts.py -k profile_mismatch -v   # one test
```

The suite is fast enough that there is no reason to work from a subset for long.
Where a `.venv` exists, `make test` uses it and a bare `python3 -m pytest` may
not have pytest — use `.venv/bin/python -m pytest` there.

**A test writes to `tmp_path`, never to a path relative to the checkout.** The
deployment's live queue and progress sit in the main checkout's `data/`, and a
test that resolves paths from the repo root is one hard reset away from being a
test that eats them.

## The gates

```sh
make lint             # ruff check, ruff format --check
make test             # pytest
make check-reviewdog  # the brave/security-action scan, this branch vs origin/master
make check            # all three: the before-you-push pass
```

`make check` is what a story runs. `docs/development.md` covers what the scan is
and how to read a finding.

- **check-reviewdog reads commits, not the working tree.** It diffs `HEAD`
  against the merge base, so an uncommitted fix is not scanned and the gate still
  exits 0. Commit first, then run it.
- **Never pipe a gate to `tail` or `head`.** The agent shell is zsh, which has no
  `pipefail`: the pipeline reports `tail`'s status and a failing gate looks
  green. Redirect to a file and read the file, or use `./scripts/wait-gate.sh`,
  which reports the gate's own exit line.

Start the gates before the self-review rather than after — they read the same
tree and the review writes nothing, so the review is free:

```sh
LOGS=$($BOT_DIR/scripts/wait-gate.sh start --dir "$PWD" "make check")
#   ... self-review the diff here ...
$BOT_DIR/scripts/wait-gate.sh wait "$LOGS"
```

## CI runs the tests on Python 3.9

`.github/workflows/lint-and-test.yml` runs `make lint` and `make test` on 3.9 and
3.13, and the organization-level security scan runs on the pull request. 3.9 is
the floor `pyproject.toml` declares, and it is the one that catches a story out:
`match` statements, `X | Y` in an annotation that is evaluated, `tomllib` and
`itertools.batched` all pass on the machine the story is written on and fail on
the floor. `ruff` is pinned to `target-version = "py39"`, so `make lint` catches
most of it — but only what a linter can see.

`tests/test_ci.py` holds the workflow to `make check` minus `check-reviewdog`, so
a target added to `check` fails a test rather than quietly going unrun. A story
that adds a gate updates that test.

## The installation check

`./tests/test-suite.sh` validates an installation end to end — hooks are
executable, scripts parse, the config template loads. It is not part of `make
check` and not part of a story's gates. Run it when the change touches
`scripts/setup.sh`, the hooks, or the shape of a config or data file.

## Screenshotting the interface

The profile lists `scripts/run-status.sh` and `scripts/view-schedules.sh` under
`uiPaths`: they exist to print a screen an operator reads, so a change to either
has to show that screen, or `check-pr-body.py --diff-base origin/master` fails.
See
[pr-descriptions.md](../../../docs/pr-descriptions.md#showing-a-terminal-screen)
for what the body needs.

No pty replay is involved here — the screen is the command's own output:

```sh
./run.sh --status
make view-schedules
```

Run them in the worktree so the screen shows the change. A worktree has no
`config.json`, and without one both fall back to `config.example.json` and print
a screen belonging to `my-project` — copy the deployment's config in first, which
is gitignored and goes no further:

```sh
cp <bot dir>/config.json .
./run.sh --status
```

`scripts/setup.sh` is deliberately not in `uiPaths`, though it prints more than
either: its screen costs an interactive run that rewrites `config.json` and
reconfigures a repository's remotes and hooks. Show what a reviewer needs from it
by hand, and do not fabricate the parts you did not run.

## Upstream tests

There is no upstream to inherit tests from. Every test in `tests/` is ours, so
the upstream-flake and filter-file procedures in
`docs/testing-requirements.md` do not apply here.

## Disabled tests

Python marks these with a `pytest.mark.skip`/`skipif` decorator, not `DISABLED_`.
The four that skip today are all one `skipif` in
`tests/test_chunk_best_practices.py`, on a best-practices tree this repository
does not have. Read the reason string and the commit that added it before
re-enabling one.
