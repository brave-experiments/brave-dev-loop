#!/usr/bin/env bash
#
# The security scan that comments on our pull requests, run here instead.
#
# Nothing in this repository configures that scan: it arrives as an
# organization-level workflow that calls brave/security-action, so the only
# copy of the runner definitions is in that repository. This clones it, pins
# the same tool versions its action.yml pins, and drives the same
# reviewdog.yml runners against this checkout. The findings are the ones the
# bot would post, minus the posting.
#
#   check-reviewdog.sh            what this branch adds, against its merge base
#   check-reviewdog.sh --full     the whole tree, however old the finding is
#
# The two modes are the action's own two modes. On a pull request it runs
# baseline_scan_only, which passes opengrep --baseline-commit and filters
# every runner through the diff; on workflow_dispatch it scans everything
# with -filter-mode=nofilter. Same flags here.
#
# No model is involved, so this is deterministic and cheap to re-run.

set -uo pipefail

# Pinned to match brave/security-action: OPENGREP_VERSION in
# src/installOpengrep.js, reviewdog_version in actions/main/action.yml. They
# move rarely; when they do, this drifts silently rather than breaking, so
# check them if a finding here disagrees with one on a PR.
OPENGREP_VERSION="v1.11.5"
REVIEWDOG_VERSION="0.17.5"
SECURITY_ACTION_REF="${SECURITY_ACTION_REF:-main}"

# The runners reviewdog.yml defines and reviewdog.sh actually enables. tfsec
# and brakeman are defined there too but commented out of the RUNNERS line;
# leaving them out keeps this honest about what CI reports.
ALL_RUNNERS="safesvg opengrep sveltegrep npm-audit pip-audit"

CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/brave-dev-loop-reviewdog"
REPO_ROOT="$(git rev-parse --show-toplevel)"
BASE_REF="origin/master"
MODE="diff"
RAW=0
RUNNERS=""

# A ref of our own under refs/remotes/origin/, because the opengrep runner
# hardcodes `--baseline-commit origin/$GITHUB_BASE_REF`. Pointing a throwaway
# origin/ ref at whatever commit we resolved is the only way to feed it a base
# it will not mangle, and it means --base accepts a sha or a tag, not just a
# branch that happens to exist on origin.
BASELINE_REF="refs/remotes/origin/__check_reviewdog_base"

usage() {
    cat <<'EOF'
usage: check-reviewdog.sh [--full] [--base REF] [--runners LIST] [--raw]

  --full           Scan the whole tree instead of this branch's changes
  --base REF       Compare against REF (default: origin/master)
  --runners LIST   Space or comma separated subset of:
                     safesvg opengrep sveltegrep npm-audit pip-audit
  --raw            Leave the <br> markup in, as the PR comments carry it
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --full) MODE="full"; shift ;;
        --base) BASE_REF="${2:?--base needs a ref}"; shift 2 ;;
        --runners) RUNNERS="${2:?--runners needs a list}"; shift 2 ;;
        --raw) RAW=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "check-reviewdog: unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

say() { printf '\033[0;34m%s\033[0m\n' "$*" >&2; }
die() { printf '\033[0;31mcheck-reviewdog: %s\033[0m\n' "$*" >&2; exit 1; }

for tool in git curl jq ruby python3; do
    command -v "$tool" >/dev/null || die "$tool is required and is not on PATH"
done

# ── the pinned tools, downloaded once ────────────────────────────────────────

case "$(uname -s)" in
    Darwin) os="osx"; rd_os="Darwin" ;;
    Linux)  os="linux"; rd_os="Linux" ;;
    *) die "unsupported platform: $(uname -s)" ;;
esac
case "$(uname -m)" in
    arm64|aarch64) og_arch="arm64"; rd_arch="arm64" ;;
    x86_64|amd64)  og_arch="x86"; rd_arch="x86_64" ;;
    *) die "unsupported architecture: $(uname -m)" ;;
esac
# The release names the mac build osx_arm64 but the linux ones manylinux_*.
[ "$os" = "linux" ] && og_asset="opengrep_manylinux_${og_arch/arm64/aarch64}" \
                    || og_asset="opengrep_osx_${og_arch}"

mkdir -p "$CACHE/bin" || die "cannot create $CACHE"

if [ ! -x "$CACHE/bin/opengrep" ] || \
   [ "$("$CACHE/bin/opengrep" --version 2>/dev/null)" != "${OPENGREP_VERSION#v}" ]; then
    say "downloading opengrep $OPENGREP_VERSION"
    curl -sfL -o "$CACHE/bin/opengrep.tmp" \
        "https://github.com/opengrep/opengrep/releases/download/$OPENGREP_VERSION/$og_asset" \
        || die "could not download opengrep $OPENGREP_VERSION ($og_asset)"
    chmod +x "$CACHE/bin/opengrep.tmp"
    mv "$CACHE/bin/opengrep.tmp" "$CACHE/bin/opengrep"
fi

if [ ! -x "$CACHE/bin/reviewdog" ] || \
   ! "$CACHE/bin/reviewdog" -version 2>/dev/null | grep -qx "$REVIEWDOG_VERSION"; then
    say "downloading reviewdog $REVIEWDOG_VERSION"
    curl -sfL "https://github.com/reviewdog/reviewdog/releases/download/v$REVIEWDOG_VERSION/reviewdog_${REVIEWDOG_VERSION}_${rd_os}_${rd_arch}.tar.gz" \
        | tar xz -C "$CACHE/bin" reviewdog \
        || die "could not download reviewdog $REVIEWDOG_VERSION"
fi

# The runner definitions and the rule set. A shallow clone, refreshed on every
# run, because the rules are the point: a stale copy reports findings CI does
# not and misses ones it does. Offline, the existing clone is used as-is.
ACTION="$CACHE/security-action"
if [ ! -d "$ACTION/.git" ]; then
    say "cloning brave/security-action ($SECURITY_ACTION_REF)"
    git clone --depth 1 --branch "$SECURITY_ACTION_REF" \
        https://github.com/brave/security-action.git "$ACTION" \
        || die "could not clone brave/security-action"
else
    git -C "$ACTION" fetch --depth 1 origin "$SECURITY_ACTION_REF" --quiet 2>/dev/null \
        && git -C "$ACTION" checkout --quiet FETCH_HEAD \
        || say "could not refresh brave/security-action; using the cached copy"
fi

SCRIPTPATH="$ACTION/assets"
export SCRIPTPATH
export PATH="$CACHE/bin:$PATH"

# safesvg reads its file list with `xargs -0 -n1 -a FILE`, and BSD xargs has no
# -a. Rather than rewrite the runner, hand it an xargs that understands the one
# flag it is missing and delegates the rest.
if ! xargs --version >/dev/null 2>&1; then
    cat > "$CACHE/bin/xargs" <<'SHIM'
#!/bin/sh
# BSD xargs has no -a FILE; read the file on stdin instead.
file=""; args=""
while [ $# -gt 0 ]; do
    case "$1" in
        -a) file="$2"; shift 2 ;;
        -a*) file="${1#-a}"; shift ;;
        *) args="$args $(printf '%s' "$1" | sed "s/'/'\\\\''/g; s/^/'/; s/\$/'/")"; shift ;;
    esac
done
if [ -n "$file" ]; then
    eval "exec /usr/bin/xargs $args" < "$file"
fi
eval "exec /usr/bin/xargs $args"
SHIM
    chmod +x "$CACHE/bin/xargs"
fi

# ── which runners have anything to look at ───────────────────────────────────

# Every runner but opengrep is a no-op without its file type, and pip-audit is
# worse than a no-op: it imports pip_audit at module scope, so with no python
# manifest to audit it still fails to start and reviewdog reports the crash.
# Selecting on content keeps a clean tree quiet.
# Any of the pathspecs matching a tracked file is enough.
has() { [ -n "$(git -C "$REPO_ROOT" ls-files -- "$@" | head -n 1)" ]; }
if [ -z "$RUNNERS" ]; then
    RUNNERS="opengrep"
    has '*.svg' && RUNNERS="$RUNNERS safesvg"
    has '*.svelte' '*.html' && RUNNERS="$RUNNERS sveltegrep"
    has 'package-lock.json' '*/package-lock.json' && RUNNERS="$RUNNERS npm-audit"
    has 'pyproject.toml' '*/pyproject.toml' 'requirements*.txt' '*/requirements*.txt' \
        && RUNNERS="$RUNNERS pip-audit"
fi
RUNNERS="$(printf '%s' "$RUNNERS" | tr ',' ' ' | tr -s ' ')"

for runner in $RUNNERS; do
    case " $ALL_RUNNERS " in
        *" $runner "*) ;;
        *) die "unknown runner: $runner (have: $ALL_RUNNERS)" ;;
    esac
done

# pip-audit is a library import, not a subprocess, so it needs its own
# interpreter rather than a tool install. Same shape as the action's `uv sync`
# into a venv whose bin goes on PATH ahead of everything else.
case " $RUNNERS " in
    *" pip-audit "*)
        if ! python3 -c 'import pip_audit' >/dev/null 2>&1; then
            if [ ! -x "$CACHE/venv/bin/python3" ]; then
                say "creating a venv for pip-audit"
                python3 -m venv "$CACHE/venv" >/dev/null 2>&1 \
                    && "$CACHE/venv/bin/pip" install --quiet 'pip-audit~=2.9.0' \
                    || say "could not install pip-audit; that runner will report a failure"
            fi
            [ -x "$CACHE/venv/bin/python3" ] && export PATH="$CACHE/venv/bin:$PATH"
        fi
        ;;
esac

# ── the file list every runner scopes itself with ────────────────────────────

cd "$REPO_ROOT" || die "cannot enter $REPO_ROOT"

# reviewdog writes a stderr log per runner into the working directory, and the
# working directory has to be the repo root for the scan to find anything.
# Clear them going in and out so a run leaves nothing behind.
cleanup() {
    rm -f "$REPO_ROOT"/reviewdog.*.stderr.log
    git update-ref -d "$BASELINE_REF" 2>/dev/null
}
trap cleanup EXIT
rm -f "$REPO_ROOT"/reviewdog.*.stderr.log

FINDINGS="$(mktemp)"
FAILURES="$(mktemp)"
trap 'cleanup; rm -f "$FINDINGS" "$FAILURES"' EXIT

if [ "$MODE" = "full" ]; then
    # reviewdog.sh's own full-scan line.
    git ls-files | tr '\n' '\0' > "$SCRIPTPATH/all_changed_files.txt"
    say "scanning the whole tree: $RUNNERS"

    reviewdog \
        -runners="$(printf '%s' "$RUNNERS" | tr ' ' ',')" \
        -conf="$SCRIPTPATH/reviewdog/reviewdog.yml" \
        -filter-mode=nofilter \
        -reporter=local > "$FINDINGS" 2>>"$FAILURES"
else
    BASE_SHA="$(git rev-parse --verify --quiet "${BASE_REF}^{commit}")" \
        || die "no such ref: $BASE_REF (fetch it, or pass --base)"
    # The merge base, not the ref itself, so a branch that has not been rebased
    # is judged on what it changed rather than on everything master moved on to.
    MERGE_BASE="$(git merge-base HEAD "$BASE_SHA")" || die "no merge base with $BASE_REF"

    if [ "$MERGE_BASE" = "$(git rev-parse HEAD)" ]; then
        say "HEAD is at the merge base with $BASE_REF; nothing on this branch to scan"
        say "(scan the whole tree with --full)"
        exit 0
    fi

    git update-ref "$BASELINE_REF" "$MERGE_BASE"
    export GITHUB_BASE_REF="__check_reviewdog_base"

    say "scanning this branch against $BASE_REF ($(git rev-parse --short "$MERGE_BASE")): $RUNNERS"
    git diff --name-only -z --diff-filter=d "$MERGE_BASE" \
        > "$SCRIPTPATH/all_changed_files.txt"

    # One runner at a time, filtered through the diff, as reviewdog.sh does it.
    for runner in $RUNNERS; do
        reviewdog \
            -reporter=local \
            -runners="$runner" \
            -conf="$SCRIPTPATH/reviewdog/reviewdog.yml" \
            -diff="git diff -U0 $MERGE_BASE" \
            >> "$FINDINGS" 2>>"$FAILURES"
    done
fi

# ── what came back ───────────────────────────────────────────────────────────

# A runner that cannot start reports zero findings, which reads exactly like a
# clean run. Say so rather than let it pass silently.
for runner in $RUNNERS; do
    log="$REPO_ROOT/reviewdog.$runner.stderr.log"
    [ -s "$log" ] || continue
    printf '\033[0;31m%s could not run cleanly:\033[0m\n' "$runner" >&2
    sed 's/^/  /' "$log" >&2
done
grep -q 'failed with zero findings: The command itself failed' "$FAILURES" 2>/dev/null \
    && sed 's/^/  /' "$FAILURES" >&2

count="$(grep -c '^[^ ]*:[0-9]*: \[' "$FINDINGS" 2>/dev/null)"
count="${count:-0}"

if [ "$count" -eq 0 ]; then
    say "no findings"
    exit 0
fi

# The messages are written for a GitHub comment, so they carry <br> and the Cc
# line the bot uses to pull in the security team. Unfold them for a terminal.
if [ "$RAW" -eq 1 ]; then
    cat "$FINDINGS"
else
    sed -E -e 's|(<br ?/?>)+|\n    |g' \
        -e 's|<!-- Category: ([a-z]+) -->|[\1]|g' \
        -e 's|^([^ ]*:[0-9]+:) |\n\1\n    |' "$FINDINGS"
fi

printf '\n\033[0;31m%s finding(s)\033[0m\n' "$count" >&2
exit 1
