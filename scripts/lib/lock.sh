#!/bin/bash
# Single-instance locking, backed by the kernel wherever possible.
#
# Three backends, tried in this order:
#
#   flock(1)  util-linux's binary. Not installed on stock macOS.
#   python3   fcntl.flock() on an fd this shell already holds. A child process
#             can lock an *inherited* fd: the lock lives on the open file
#             description, which the parent keeps open, so it outlives the
#             child that took it and dies with the parent. python3 is a hard
#             dependency of this repo, so this is the path macOS takes.
#   mkdir     Last resort for a machine with neither. The only backend whose
#             state survives `kill -9`, so it carries a pid file and reclaims
#             a lock whose holder is gone.
#
# The first two are held by the kernel: `kill -9` releases them and nothing is
# left behind to go stale. That is the property everything else here relies on
# — a run slot, and any story claim belonging to it, is live if and only if
# its lock cannot be taken.
#
# **Never read a lock file's existence as evidence that something is running.**
# The file is just an inode to lock; it is expected to outlive the run. Ask for
# the lock (bot_lock_probe) or look at scripts/run-status.sh.
#
# Usage:
#   source .../lib/lock.sh
#   bot_acquire_lock "$LOCKFILE"
#   case $? in 0) ;; 1) echo "already running"; exit 0 ;; *) exit 1 ;; esac
#
# Returns 0 when the lock is held by this process, 1 when another live process
# holds it. Anything else is fatal and reported, never silently swallowed.

# fd 200 is fixed rather than dynamic: `exec {fd}>` needs bash 4, and macOS
# ships bash 3.2.
BOT_LOCK_FD=200
BOT_LOCK_DIR=""
BOT_LOCK_FILE=""
BOT_LOCK_BACKEND=""

# Split out so tests can exercise every path on any machine. Set
# BOT_LOCK_FORCE_BACKEND to flock|python|mkdir to pin one.
_bot_have_flock() {
  [ "${BOT_LOCK_FORCE_BACKEND:-}" = "flock" ] && return 0
  [ -n "${BOT_LOCK_FORCE_BACKEND:-}" ] && return 1
  command -v flock >/dev/null 2>&1
}

_bot_have_python_lock() {
  [ "${BOT_LOCK_FORCE_BACKEND:-}" = "python" ] && return 0
  [ -n "${BOT_LOCK_FORCE_BACKEND:-}" ] && return 1
  command -v python3 >/dev/null 2>&1
}

# Name of the backend that will be used. Reported by run-status.sh so an
# operator can tell at a glance whether stale locks are even possible here.
bot_lock_backend() {
  if _bot_have_flock; then
    echo "flock"
  elif _bot_have_python_lock; then
    echo "python"
  else
    echo "mkdir"
  fi
}

# Take an exclusive, non-blocking lock on an already-open fd.
# 0 = acquired, 1 = held by someone else.
_bot_flock_fd() {
  local fd="$1"
  if _bot_have_flock; then
    flock -n "$fd"
    return $?
  fi
  python3 -c "import fcntl, sys
try:
    fcntl.flock($fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(1)
" 2>/dev/null
}

bot_release_lock() {
  if [ -n "$BOT_LOCK_DIR" ] && [ -d "$BOT_LOCK_DIR" ]; then
    # Only clear a lock this process owns; a stale-cleanup race could otherwise
    # delete a lock another run has just taken.
    if [ "$(cat "$BOT_LOCK_DIR/pid" 2>/dev/null)" = "$$" ]; then
      rm -rf "$BOT_LOCK_DIR"
    fi
  fi
  BOT_LOCK_DIR=""
  # Closing the fd drops a kernel lock. Harmless when we never took one.
  eval "exec $BOT_LOCK_FD>&-" 2>/dev/null || true
  BOT_LOCK_FILE=""
  BOT_LOCK_BACKEND=""
}

_bot_lock_holder_alive() {
  local pid="$1"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null
}

# The mkdir backend, kept behind its own function so the kernel-backed paths
# read cleanly. Only reachable on a machine with neither flock nor python3.
_bot_acquire_lock_mkdir() {
  local lockfile="$1"
  local dir="$lockfile.d"

  if mkdir "$dir" 2>/dev/null; then
    echo $$ > "$dir/pid"
    BOT_LOCK_DIR="$dir"
    return 0
  fi

  # Directory exists. Either a live holder, or a lock left behind by a process
  # that was killed before it could clean up.
  local holder
  holder=$(cat "$dir/pid" 2>/dev/null || echo "")
  if _bot_lock_holder_alive "$holder"; then
    return 1
  fi

  echo "Clearing stale lock at $dir (pid ${holder:-unknown} is gone)" >&2
  rm -rf "$dir"
  if mkdir "$dir" 2>/dev/null; then
    echo $$ > "$dir/pid"
    BOT_LOCK_DIR="$dir"
    return 0
  fi
  return 1
}

# A lock taken by the mkdir backend is invisible to the kernel-backed ones. It
# only appears when a deployment upgrades mid-run — the old code holds a
# directory, the new code would take the file and start a second run alongside
# it. Check for a live one before claiming a kernel lock.
_bot_mkdir_lock_is_live() {
  local dir="$1.d"
  [ -d "$dir" ] || return 1
  _bot_lock_holder_alive "$(cat "$dir/pid" 2>/dev/null || echo "")"
}

bot_acquire_lock() {
  local lockfile="$1"
  [ -n "$lockfile" ] || { echo "bot_acquire_lock: no lockfile given" >&2; return 2; }

  if ! _bot_have_flock && ! _bot_have_python_lock; then
    _bot_acquire_lock_mkdir "$lockfile"
    local rc=$?
    [ $rc -eq 0 ] && { BOT_LOCK_FILE="$lockfile"; BOT_LOCK_BACKEND="mkdir"; }
    return $rc
  fi

  if _bot_mkdir_lock_is_live "$lockfile"; then
    return 1
  fi

  # Append, never truncate: `exec 200>file` empties the file at open time,
  # which would wipe the ownership record of the run that actually holds it.
  eval "exec $BOT_LOCK_FD>>\"\$lockfile\"" || {
    echo "Could not open lock file $lockfile" >&2
    return 2
  }
  if _bot_flock_fd "$BOT_LOCK_FD"; then
    BOT_LOCK_FILE="$lockfile"
    BOT_LOCK_BACKEND=$(bot_lock_backend)
    # Record the owner. The kernel lock decides whether the slot is taken;
    # this says *who* took it, which is what makes an orphaned lock (see
    # bot_lock_owner_alive) tellable from a running one.
    printf '%s\n' "$$" > "$lockfile"
    return 0
  fi
  # Not ours — close the fd again so a later attempt on another lock file
  # starts clean, and so a caller that gives up leaves nothing open.
  eval "exec $BOT_LOCK_FD>&-" 2>/dev/null || true
  return 1
}

# Wait for a lock instead of giving up, for short critical sections that must
# not be skipped (git operations on a shared checkout). Polls, because none of
# the backends offer a portable blocking form with a timeout.
# Returns 0 when acquired, 1 on timeout, 2 on error.
bot_acquire_lock_wait() {
  local lockfile="$1"
  local timeout="${2:-300}"
  local waited=0
  while :; do
    bot_acquire_lock "$lockfile"
    case $? in
      0) return 0 ;;
      1) ;;
      *) return 2 ;;
    esac
    if [ "$waited" -ge "$timeout" ]; then
      return 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
}

# Is this lock free? Asks the kernel and releases immediately, so it is safe to
# call from a status command that must not disturb a running job.
# 0 = free, 1 = held, 2 = cannot tell.
bot_lock_probe() {
  local lockfile="$1"
  [ -n "$lockfile" ] || return 2

  if _bot_mkdir_lock_is_live "$lockfile"; then
    return 1
  fi
  if ! _bot_have_flock && ! _bot_have_python_lock; then
    # mkdir backend: a directory without a live pid is stale, i.e. free.
    return 0
  fi
  [ -e "$lockfile" ] || return 0

  # fd 9 rather than 200: probing must never touch a lock this shell holds.
  # Append mode for the same reason acquiring uses it — a probe that truncated
  # the file would erase the ownership record of the run it is asking about.
  (
    exec 9>>"$lockfile" 2>/dev/null || exit 2
    if _bot_flock_fd 9; then exit 0; else exit 1; fi
  )
}

# The pid that recorded itself as this lock's owner, if any.
bot_lock_owner_pid() {
  local lockfile="$1"
  if [ -d "$lockfile.d" ]; then
    cat "$lockfile.d/pid" 2>/dev/null
    return 0
  fi
  [ -f "$lockfile" ] || return 0
  # Trailing newline matters: callers concatenate this with other pid lists,
  # and without it two pids run together into one nonsense number.
  local owner
  owner=$(head -n 1 "$lockfile" 2>/dev/null | tr -dc '0-9')
  [ -n "$owner" ] && printf '%s\n' "$owner"
  return 0
}

# A lock is *orphaned* when the kernel still holds it but the run that took it
# is gone: some child that inherited the lock fd outlived its parent. It is not
# a running job and it is not free either — it needs reset-run.sh --slot N.
#
# 0 = orphaned, 1 = not orphaned (free, or genuinely held by a live owner).
bot_lock_is_orphaned() {
  local lockfile="$1" owner
  bot_lock_probe "$lockfile"
  [ $? -eq 1 ] || return 1
  owner=$(bot_lock_owner_pid "$lockfile")
  # No recorded owner: an old lock file from before ownership was recorded.
  # Assume a live holder rather than inventing an orphan.
  [ -n "$owner" ] || return 1
  _bot_lock_holder_alive "$owner" && return 1
  return 0
}

# pids with the lock file open. Only meaningful for the kernel-backed
# backends, where the holder keeps the file open for its whole life.
bot_lock_holder_pids() {
  local lockfile="$1"
  if [ -d "$lockfile.d" ]; then
    cat "$lockfile.d/pid" 2>/dev/null
  fi
  if command -v fuser >/dev/null 2>&1; then
    fuser "$lockfile" 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$' || true
  fi
}
