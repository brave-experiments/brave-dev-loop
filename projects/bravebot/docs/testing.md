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
refused`, `Aichat(NoContent)`, an off-by-one round count, or an assertion on a
request body that was still in flight when the test drained the channel with
`try_iter`. One story saw five distinct `turn.rs` tests fail across three runs with
no overlap between them.

Do not re-run the target hoping for green. Apply the rule in step 11 of
[workflow-pending.md](../../../docs/workflow-pending.md): if the diff touches no
turn-mode code, say so, name the failing tests in the PR body, and carry on. bravebot's own `docs/development/checks.md` says the same thing first, and it
wins: "a test that fails on the parent commit is not yours to fix".

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
