# bravebot: Testing

Project-specific test and presubmit commands. Read alongside
`docs/testing-requirements.md`.

Every command here runs in the story's worktree (`../bravebot-<issue-number>`),
never in the main checkout — see [repo.md](./repo.md#worktrees). A fresh
worktree has an empty `target/`, so its first build is a cold one.

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

## Upstream tests

There is no upstream to inherit tests from. Every test in the repo is ours, so
the upstream-flake and filter-file procedures in `docs/testing-requirements.md`
do not apply here.

## Disabled tests

Rust marks these `#[ignore]`, not `DISABLED_`. Search for `#[ignore]` and read
the attribute's reason string and the commit that added it.
