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

`cargo test --all --locked` is what CI runs, and `make check` runs it, so the
run that decides whether a story passes is the one inside `make check-affected`
below. Do not run the whole workspace yourself first: that is the same suite on
the same tree, twice. While working, run the crate or test you changed; `--locked`
on that run makes a stale `Cargo.lock` fail here rather than in CI.

## What runs once

`make check` already does `cargo fmt --check`, clippy over every target and the whole
test suite, and compiles everything doing so, so a separate `cargo build` adds
nothing. Run `cargo fmt --all` once before the gates (it writes; the gates only check)
and do not repeat it after them, since an edit after the gates is a tree they did not
see. The duplicates that remain are deliberate: `check-linux` repeats fmt, clippy and
the suite on another platform, and `check-msrv` builds with another toolchain.

## Presubmit

There is no `presubmit` command. bravebot's CI decides which of its jobs a pull
request needs from the paths it touches, with `contrib/affected-checks.py`, and
the same script decides the local gates:

```sh
make check-affected                          # the host gates this branch needs, run
python3 contrib/affected-checks.py           # the same list and the reason for each, run nothing
python3 contrib/affected-checks.py --containers  # the Docker gates it needs
```

`check-affected` measures the branch against its merge base with
`upstream/main`: its commits, its uncommitted edits and its untracked files. It
always runs `check-scripts`, `check-spec`, `check-security`, `check-locales`,
`check-versions` and `check-reviewdog`, which take seconds and run on every CI
change too, then whichever of `check`, `check-ui`, `check-docs`, `check-npm` and
`check-deps` a touched path could fail. A change to bravebot's Makefile, its
workflows or the classifier needs every gate there is, as does a path no rule
names. It prints each area and the first path that needs it before running
anything, and passes `-k`, so one run reports every failing gate.

The Docker gates are `check-msrv`, `check-windows` and `check-linux` for any
change to Rust, and none for a branch that touches no Rust.

Three of the gates it can choose are worth knowing about before they run:

- **check-ui** builds the two bridge crates, typechecks and builds the desktop
  app, runs its Node tests and drives the Electron walkthrough, which on macOS
  opens a window in the logged-in session. It runs for a change under `ui/` and
  for one to any crate the desktop app builds, which is every crate but `cli`
  and `tui`.
- **check-deps** is `cargo deny` over the lockfile. Its first run builds
  cargo-deny into `~/.cache/bravebot-deny`, which takes a few minutes once.
- **check-windows** is clippy for `x86_64-pc-windows-gnu` over every target,
  which is CI's `Lint the Windows target` job.

For the inner loop, run a single target:

```sh
make check           # fmt --check, clippy -D warnings, tests, toolchain age
make check-spec      # the mechanical docs/specs check
make check-locales   # every catalog against the reference, and the gap file
make check-ui        # the desktop app, its Node tests and the walkthrough
make check-all       # every gate, whatever the branch touches
```

`check-locales` reads the catalogs against each other — every message the
reference has, the arguments each one takes, the recorded gaps — and nothing
else. A sentence written in the Rust source is invisible to it, because from the
catalog's side that message does not exist. `$BOT_DIR/scripts/check-untranslated.py`
is the half that looks at the source; both are needed and neither implies the
other.

### Waiting for them

Drive them through `./scripts/wait-gate.sh` rather than sleeping on a log — it
returns when the gate returns, and reads the verdict from the gate's own `EXIT=`
line. Start the local gates, self-review the diff while they run, then collect:

```sh
LOGS=$($BOT_DIR/scripts/wait-gate.sh start --dir "$PWD" \
  "BRAVEBOT_ALLOW_UNCONFIGURED_BUILD=1 make check-affected")
#   ... run the self-review here: it reads the same tree and writes nothing ...
$BOT_DIR/scripts/wait-gate.sh wait "$LOGS"
```

The log opens with the areas and the path behind each, so read that first when a
gate fails: it says which gate the failure is in, and why that gate ran.

Each gate is a shell command, so the build's environment goes in the string:
worktrees have no `.envrc`, and without `BRAVEBOT_ALLOW_UNCONFIGURED_BUILD=1` the
build refuses to start.

**Give the Docker gates an invocation to themselves.** Each one starts by copying
the whole worktree into its container, which takes long enough to matter and
fails outright if something writes into the tree while it reads. `make check`
writing `target/` is exactly that, so once the host gates are done, run the ones
`python3 contrib/affected-checks.py --containers` prints together, and never
alongside a local one. For a change to Rust that is all three:

```sh
$BOT_DIR/scripts/wait-gate.sh run --dir "$PWD" \
  "BRAVEBOT_ALLOW_UNCONFIGURED_BUILD=1 make check-msrv" \
  "BRAVEBOT_ALLOW_UNCONFIGURED_BUILD=1 make check-windows" \
  "BRAVEBOT_ALLOW_UNCONFIGURED_BUILD=1 make check-linux"
```

When it prints nothing, there is no Docker gate to run. When it exits non-zero
it could not diff the branch, and that is a failed gate: fetch `upstream` and ask
again. `make check-affected-containers` runs the same list one gate after another,
which is slower.

Exit 2 means the timeout expired with a gate still running; the container is still
going, so `wait` on the log directory again rather than starting it over.

- **check-reviewdog** drives the same opengrep and npm-audit runners the
  organization workflow drives, so a finding here is a comment the bot would
  post on the PR. `check-reviewdog-full` scans the whole tree and reports plenty
  that predates the branch — the branch-scoped one is the gate. It measures from
  `upstream/main` wherever that ref exists, as `check-affected` does. `origin/main`
  is the fork's stale ref here (see
  [repo.md](./repo.md#entering-the-worktree--the-first-step-of-every-iteration)),
  and measured from that, the scan would cover every commit the fork is behind
  and report whatever it found in them against the branch.
- **check-linux** is not a CI job; it is the coverage a macOS host lacks, since
  a macOS host never compiles the Linux backend and clippy gains lints between
  releases.
- **check-msrv**, **check-windows** and **check-linux** all need Docker running.

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

## Which front end a change is in

bravebot ships two: the terminal client in `crates/tui`, and the desktop
application under `ui/`, whose Rust side is `crates/ui-bridge` and
`crates/ui-files`. Both run turns, and both read the files under `~/.bravebot`, so
a reproduction has to say which of them its steps are in and how the screen is
reached inside it. In the desktop application a turn starts from the message box
at the bottom of the transcript, with Enter or the Send button, and Agent settings
opens from the button in the sidebar; in the terminal client it starts from the
session prompt. Where the steps write a file the other front end reads, say so:
otherwise a shared file reads as belonging to the one being changed.

## Screenshotting the interface

`uiPaths` lists `crates/tui` alone: a change there has to show the screen it
produces, or `check-pr-body.py --diff-base upstream/main` fails. A change under
`ui/` is asked for no screen — paste one anyway. See
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

## Reproducing something the model decides

The planner's tool calls (`list_files`, `read_file`, `vet_content` and the rest) and the
references they hand back (`ref:1`) are not steps. A person types a message into the session
prompt or the message box; which tools the planner then calls is the model's choice. A test
scripts those calls, so a body that copies a test's `tool_request(...)` lines as its steps
describes the test and not the product, and leaves out everything the reviewer needs to get
there.

Write the person's steps, and make them complete from a cold start:

- **The launch.** The front end ([above](#which-front-end-a-change-is-in)), the command that
  starts it, and the redirected `HOME` from the screenshot recipe so the run is not yours.
- **The directory.** What the work directory contains and how to make it (`printf`, `cp`, or a
  generated file), and the trust answer, because a fresh directory opens a trust prompt that
  swallows the first keys. Say whether the files are trusted: the trust map decides whether
  the planner is handed names or opaque references, so a bug in one of them is not in the other.
- **The message.** The exact text the person types.
- **What appears.** The screen, from a capture, before and after.

When no message makes a stock model take the path, the model has to be a stand-in. A
`provider` block with `options.baseURL` pointing at a local server and no key makes bravebot
treat it as a gateway and send `POST /v1/chat/completions` with no credentials; a session
takes its model from `<HOME>/.bravebot/model`, as `<provider>/<model>`. The server answers
request *N* with the *N*th tool call from a list. Say in the steps that this server stands in
for the model, give the command that starts it, and show the calls it makes in a fenced block
under the steps, apart from what the person does. `contrib/README.md` and `contrib/drive_tui.py`
have the rest. If the path cannot be reached even that way, write `Not reproducible locally:`
with the reason.

Say how the planner knows what the call needs. If the scripted call names `ref:1` and the
planner was never told what `ref:1` is, the reproduction shows a sequence a real model would
have to guess at, and the body has to say so.

## Upstream tests

There is no upstream to inherit tests from. Every test in the repo is ours, so
the upstream-flake and filter-file procedures in `docs/testing-requirements.md`
do not apply here.

## Disabled tests

Rust marks these `#[ignore]`, not `DISABLED_`. Search for `#[ignore]` and read
the attribute's reason string and the commit that added it.
