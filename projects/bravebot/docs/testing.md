# bravebot: Testing

Project-specific test and presubmit commands. Read alongside
`docs/testing-requirements.md`.

Every command here runs in the story's worktree (`../bravebot-<issue-number>`),
never in the main checkout — see [repo.md](./repo.md#worktrees). A fresh
worktree has an empty `target/`, so its first build is a cold one.

## Choosing the tests

`agents/skills/testing-preflight/SKILL.md` in the target repo is what the tests
are designed against, and `docs/development/commits.md` makes its output part of
the pull request. Read it before writing a test, not after: it asks, for each
behaviour the change touches, which wrong implementation would still pass the
test you were about to write, and that question changes the fixture rather than
the wording.

It is also the standard the tests are reviewed against, so the body has to say
per behaviour whether the test was **demonstrated** to fail on the fault —
restore the fault, watch that test fail for that reason, restore the tree, re-run
it — or is **reasoned only**. Restore by saving the file's exact bytes first;
`git checkout --` takes the review fixes with it.

## Running tests

```sh
cargo test --all              # the whole workspace
cargo test --all <filter>     # one test, by substring of its path
cargo test -p <crate>         # one crate
```

`cargo test --all --locked` is what CI runs. Use `--locked` for the run that
decides whether a story passes, so a stale `Cargo.lock` fails here rather than
in CI.

## Presubmit

There is no `presubmit` command. `make check-all` is the equivalent: it runs
every check any CI enforces, which is the four jobs in `.github/workflows/ci.yml`
plus the organization-level security scan.

```sh
make check           # fmt --check, clippy -D warnings, tests, toolchain age
make check-spec      # the mechanical docs/specs check
make check-npm       # npm ci --ignore-scripts and the lockfile lint
make check-msrv      # build against the declared minimum Rust (Docker)
make check-reviewdog # the brave/security-action scan (this branch's changes)
make check-all       # all five, in that order
make check-linux     # the same fmt/clippy/tests on Linux stable (Docker)
```

`make check` is the inner loop. Run the rest before pushing.

### Waiting for them

Drive them through `./scripts/wait-gate.sh` rather than sleeping on a log — it
returns when the gate returns, and reads the verdict from the gate's own `EXIT=`
line. Start the local gates, self-review the diff while they run, then collect:

```sh
LOGS=$($BOT_DIR/scripts/wait-gate.sh start --dir "$PWD" \
  "BRAVEBOT_ALLOW_UNCONFIGURED_BUILD=1 make check" "make check-spec" "make check-npm")
#   ... run the self-review here: it reads the same tree and writes nothing ...
$BOT_DIR/scripts/wait-gate.sh wait "$LOGS"
```

Each gate is a shell command, so the build's environment goes in the string:
worktrees have no `.envrc`, and without `BRAVEBOT_ALLOW_UNCONFIGURED_BUILD=1` the
build refuses to start.

**Give the Docker gates an invocation to themselves.** `check-msrv` and
`check-linux` start by copying the whole worktree into the container, which takes
long enough to matter and fails outright if something writes into the tree while
it reads. `make check` writing `target/` is exactly that, so run the two Docker
gates together and never alongside a local one:

```sh
$BOT_DIR/scripts/wait-gate.sh run --dir "$PWD" \
  "BRAVEBOT_ALLOW_UNCONFIGURED_BUILD=1 make check-msrv" \
  "BRAVEBOT_ALLOW_UNCONFIGURED_BUILD=1 make check-linux"
```

Exit 2 means the timeout expired with a gate still running; the container is still
going, so `wait` on the log directory again rather than starting it over.

- **check-reviewdog** drives the same opengrep and npm-audit runners the
  organization workflow drives, so a finding here is a comment the bot would
  post on the PR. `check-reviewdog-full` scans the whole tree and reports plenty
  that predates the branch — the branch-scoped one is the gate. Run it as
  `contrib/check-reviewdog.sh --base upstream/main`: its own default base is
  `origin/main`, which is the fork's stale ref here (see
  [repo.md](./repo.md#entering-the-worktree--the-first-step-of-every-iteration)),
  so the scan takes its baseline from a commit the branch is not based on, covers
  every commit the fork is behind, and reports whatever it finds in them against
  the branch.
- **check-linux** is not a CI job; it is the coverage a macOS host lacks. Run it
  whenever the change touches platform-specific code, and for anything clippy
  might lint differently on a newer stable.
- **check-msrv** and **check-linux** both need Docker running.

### check-linux flakes on `crates/agent/tests/turn.rs`

Many tests there stand up a real mock HTTP server on an ephemeral port or wait on a
clock, and the container runs at `--test-threads=4` while other run slots build on
the same machine. So a *different* handful fails on each attempt: `Connection
refused`, `Aichat(NoContent)`, `Transport { detail: "io: Peer disconnected" }`, an
off-by-one round count, or an assertion on a request body that was still in flight
when the test drained the channel with `try_iter`. One story saw five distinct
`turn.rs` tests fail across three runs with no overlap between them.

Re-run the test the run named, by itself, per step 11 of
[workflow-pending.md](../../../docs/workflow-pending.md) — not the target. The
container is what makes that cheap, so **start it yourself rather than through
`make`**: the Makefile's `check-linux` passes `--rm`, which throws the compiled tree
away at the moment you learn you need it.

```sh
docker run --name bb-linux --platform linux/amd64 -e BRAVEBOT_ALLOW_UNCONFIGURED_BUILD=1 \
  -e USER=root -e BRAVEBOT_ALLOW_MISSING_LANDLOCK=1 -v "$PWD:/src:ro" -w /work rust:slim \
  sh -c 'cp -r /src/. /work && cargo test --all --no-fail-fast -- --test-threads=4'

docker commit bb-linux bb-linux-snap    # keeps /work, so the re-run mostly skips the build
docker run --rm --platform linux/amd64 -e BRAVEBOT_ALLOW_UNCONFIGURED_BUILD=1 \
  -e USER=root -e BRAVEBOT_ALLOW_MISSING_LANDLOCK=1 -w /work bb-linux-snap \
  sh -c 'cargo test -p bravebot-agent --test turn -- --test-threads=1 <test-name>'
```

The snapshot loses the fingerprints of the crates with build scripts, so the isolated
run is not free: about a dozen crates rebuild in some 20 seconds, against 135 cold.
A test that passes alone was load, and the gate counts as passed with the flake named
in the PR body. bravebot's own `docs/development/checks.md` covers the case where it
fails alone too: "a test that fails on the parent commit is not yours to fix".

Two Docker specifics worth knowing before you spend twelve minutes:

- `cargo test --all` aborts at the first failing test binary, so one flake truncates
  the rest of the suite. Use `--no-fail-fast` when the question is whether anything
  else fails too.
- The container **outlives a killed `make`**. If the iteration died mid-run,
  `docker ps` will still show it: confirm with `docker inspect <name>` that the
  mount source is your own worktree and not another slot's, then recover the result
  with `docker logs -f <name>` and `docker wait <name>` instead of starting over.

## Screenshotting the interface

The interface is `crates/tui`, which the profile lists under `uiPaths`: a change
there has to show the screen it produces, or `check-pr-body.py --diff-base
upstream/main` fails. See
[pr-descriptions.md](../../../docs/pr-descriptions.md#showing-a-terminal-screen)
for what the body needs.

`contrib/drive_tui.py` in the target repo runs a scripted session against a real
pty. Its `--raw` capture is the untouched bytes, which is what to replay — its
own default output has the escape sequences stripped, and that is not a screen.

A script is one step per line: a timeout in seconds, a space, then the keys.

```sh
WORK=$(pwd)          # the story worktree: ../bravebot-<issue-number>
cargo build

mkdir -p /tmp/shot/work /tmp/shot/home
printf '%s\n' '8 y' '10 !ls -1\r' > /tmp/shot/session.txt

cd /tmp/shot/work    # or wherever the change is visible
HOME=/tmp/shot/home python3 "$WORK/contrib/drive_tui.py" \
  /tmp/shot/session.txt --raw /tmp/shot/raw.txt --cols 100 --rows 30 \
  -- "$WORK/target/debug/bravebot" > /dev/null

python3 "$BOT_DIR/scripts/terminal-screenshot.py" /tmp/shot/raw.txt \
  --cols 100 --rows 30 --strict
```

The `--cols`/`--rows` given to the two commands have to match, since the
interface measures the terminal once at startup and lays every frame out against
that size. 100x30 fits a PR body without wrapping.

`HOME` is redirected because a scripted session is a real one: without it the run
writes to `~/.bravebot/sessions` and picks up whatever is configured there, so
the screen would be yours rather than a reader's. Nothing else is needed for a
screen the model is not part of — a trust prompt, a `!` shell command, a slash
command, an error. A screen that needs a reply needs a backend:
`BRAVE_AI_CHAT_ENDPOINT` pointed at a local
[aichat](https://github.com/brave/aichat). `contrib/README.md` has the rest.

`--strict` exits non-zero rather than print a screen holding a sequence the
replay does not model. Keep it: the interface emits nothing it cannot account
for, so a failure means the capture is unusual and the screen should not be
trusted.

## Upstream tests

There is no upstream to inherit tests from. Every test in the repo is ours, so
the upstream-flake and filter-file procedures in `docs/testing-requirements.md`
do not apply here.

## Disabled tests

Rust marks these `#[ignore]`, not `DISABLED_`. Search for `#[ignore]` and read
the attribute's reason string and the commit that added it.
