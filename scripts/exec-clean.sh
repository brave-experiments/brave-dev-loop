#!/bin/bash
# Run a command without this run's slot lock file descriptor.
#
# A run holds its slot lock on an open file descriptor, and a flock lives on
# the open file description — so any child that inherits the fd keeps the lock
# alive after the run itself is gone, and the slot reads as held with nothing
# running in it.
#
# Closing it per-command (`somecmd 200>&-`) does not work on the bash 3.2
# macOS ships: to apply a redirection to a command, bash first duplicates the
# fd to a free one near 10 so it can restore it afterwards, and children
# inherit that duplicate. Same open file description, same lock.
#
# A bare `exec 200>&-` has nothing to restore, so bash makes no duplicate: it
# just closes the fd in this shell. Then `exec` replaces this shell with the
# command, leaving no wrapper process behind to hold anything either.
#
# Usage: exec-clean.sh [--cd DIR] [--] <command> [args...]
#
# --cd is not a convenience: writing `(cd DIR && exec-clean.sh ...)` instead
# leaves the subshell running as the command's parent, and that subshell is a
# fork of the run — holding the very fd this exists to drop.

if [ "$1" = "--cd" ]; then
  cd "$2" || exit 1
  shift 2
elif [ "${1#--cd=}" != "$1" ]; then
  cd "${1#--cd=}" || exit 1
  shift
fi
[ "$1" = "--" ] && shift

if [ $# -eq 0 ]; then
  echo "Usage: exec-clean.sh [--cd DIR] [--] <command> [args...]" >&2
  exit 2
fi

# BOT_LOCK_FD is exported by lib/lock.sh; the default keeps this runnable on
# its own. Closing an fd that was never open is not an error worth reporting.
eval "exec ${BOT_LOCK_FD:-200}>&-" 2>/dev/null || true

exec "$@"
