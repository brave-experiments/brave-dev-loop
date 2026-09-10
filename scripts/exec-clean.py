#!/usr/bin/env python3
"""Run a command with no inherited file descriptors above stdio.

A run holds its slot lock on an open file descriptor, and `flock` locks live
on the *open file description* — so any child that inherits the fd keeps the
lock alive after the run itself is gone. The slot then reads as held with
nothing running in it.

Closing the fd per-command (`somecmd 200>&-`) does not solve it on bash 3.2,
which macOS ships: to apply that redirection bash first duplicates fd 200 to a
free fd around 10 so it can restore it afterwards, and *that* duplicate is
what children inherit. Same open file description, same lock.

So the agent and the `tee` beside it are started through this: it closes
everything above stdin/stdout/stderr and execs, which cannot leak anything by
construction.

Usage: exec-clean.py [--cd DIR] [--] <command> [args...]

--cd is not a convenience: writing `(cd DIR && exec-clean.py ...)` instead
leaves the subshell running as the command's parent, and that subshell is a
fork of the run — holding the very fd this exists to drop.
"""

import os
import resource
import sys


def _fd_limit():
    soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft in (resource.RLIM_INFINITY, -1):
        soft = 65536
    return min(max(soft, 256), 65536)


def main():
    args = sys.argv[1:]
    workdir = None
    while args:
        if args[0] == "--cd" and len(args) > 1:
            workdir, args = args[1], args[2:]
        elif args[0].startswith("--cd="):
            workdir, args = args[0][len("--cd=") :], args[1:]
        elif args[0] == "--":
            args = args[1:]
            break
        else:
            break
    if not args:
        print("Usage: exec-clean.py [--] <command> [args...]", file=sys.stderr)
        return 2

    if workdir:
        try:
            os.chdir(workdir)
        except OSError as e:
            print(f"exec-clean: cannot cd to {workdir}: {e}", file=sys.stderr)
            return 1

    # stdin/stdout/stderr are the pipeline; everything above them belongs to
    # the parent and must not travel.
    os.closerange(3, _fd_limit())

    try:
        os.execvp(args[0], args)
    except OSError as e:
        print(f"exec-clean: cannot run {args[0]}: {e}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    sys.exit(main())
