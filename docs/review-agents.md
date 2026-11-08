# Review agents

`reviewer.agent` in `config.json` (set by `make setup`) picks the agent that runs the
scheduled `/review-prs` sweeps, the review-request poll and the ping replies. It is
independent of `bot.agent`, which is what `./run.sh` drives.

| Value | Status |
|---|---|
| `claude` (default) | Confined by an allowlist. |
| `bravebot` | Runs, but is confined differently, and less tightly. See below. |

`BOT_REVIEW_AGENT` in the environment overrides the key, as `BOT_AGENT` does `bot.agent`.
Only `claude` and `bravebot` are accepted; `codex` and `cursor` have no confined headless
mode wired in here.

The command line is built in one place, `scripts/lib/review-agent.sh`, and used by
`scripts/review-session.sh` and `scripts/reply-to-pings.sh`.

## What a review session must not do

The session reads untrusted text: a PR's diff, its description and its source tree. It
runs without a GitHub token. `prepare-review.py` and `collect-results.py` run outside it,
and only the validators' results file leaves it. So the session may:

- read its work directory and the bot directory (credential paths excepted);
- write only inside its work directory;
- run one script, `select-candidates.py`, and launch subagents;
- not reach the network.

## claude

`review-session-settings.py` writes an allowlist and the session runs under
`--permission-mode dontAsk`, so anything not listed is refused.

## bravebot

A one-shot bravebot run has nobody to ask. A write, a program or a fetch that would need
approval ends the run on status 4, and an `allow` rule in a settings file decides nothing
there. A review session that cannot write its results file is useless, so the run uses
`--dangerously-skip-permissions`. That mode answers every question yes and also vouches
for every file the session reads, so what it reads is not quarantined.

What replaces the allowlist:

| Control | How |
|---|---|
| File reads of credentials | `Read` deny rules from `review-session-settings.py`, which hold in every mode. |
| File writes outside the work directory | `Edit` deny rules for the bot directory and each read-only directory, added by `review-agent.sh`. Writes elsewhere (for example `/tmp`) are not denied. |
| Programs `run` starts | `--sandbox workspace-write`, writable only in the work directory (`--sandbox-allow-write`), `--sandbox-deny-write` on the bot directory, and `--sandbox-deny-read` on ssh keys, gh config directories and `.envrc`. |
| Network for programs | `--run-network closed`. |
| Tool surface | `--tools` is a fixed list with no `fetch_url` or `download_url`. A reply session also gets no `run` and no `spawn_agent`. |
| GitHub | No token and an empty `GH_CONFIG_DIR`, as for claude. |

### What is weaker than claude

1. **`run` is not limited to one script.** claude may run only `select-candidates.py`.
   bravebot may run any program the sandbox allows. There is no allowlist for `run` that
   applies in this mode, because `allow` rules are ignored where nobody can be asked.
2. **Reads are not quarantined.** Content from the PR is shown to the model as trusted,
   so a prompt injection in a diff reaches it as instructions. The controls above bound
   what it can then do. They do not stop it being misled into writing a bad results file
   inside the work directory.
3. **Write confinement is deny-list based.** Paths nobody listed (for example the system
   temporary directory) stay writable by the file tools. Programs are held by the sandbox.
4. **`--trust-workspace`** trusts the whole bot directory so the `/review-prs` skill under
   `.claude/skills` loads. It trusts the PR checkout nothing, but it does vouch for the
   bot directory's own files.
5. **Not verified here.** This has not been run against a real PR. The flags come from
   bravebot's help and docs, and a flag combination bravebot refuses will fail the
   session (the collector still runs and posts nothing).

What would close the gap: a headless mode in which `allow` rules apply and the rest is
refused, without status 4 ending the run (the behaviour claude's `dontAsk` has), plus a
way to keep reads quarantined while still reading a named directory.
