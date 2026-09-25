#!/usr/bin/env python3
"""Say whether an iteration ended before its pending story got anywhere.

run.sh asks this once the agent has exited. An iteration on a pending story
ends one of two ways the workflow allows: update-prd-status.py moves the story,
or a progress entry says why it stayed (docs/workflow-pending.md, "Never stop
without leaving a record"). A session that ends its turn to wait on a gate, or
answers a "continue" as if nothing was asked, does neither -- and in a --print
run that turn was the whole session, so the work it had not pushed and every
check it was waiting on end there too.

Prints one JSON object on stdout:

    stoppedShort  the story started and ended pending, with no entry for it in
                  the progress log past --progress-offset
    work          one line on what git shows of the story's work
    entry         the progress entry to write when nobody else did

Nothing here writes: run.sh decides whether to resume the session, and pipes
`entry` to append-progress.sh when it stops trying.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from subprocess import CalledProcessError  # nosemgrep

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.pr_worktree import run, worktree_for_branch


def story_in_prd(prd_path, story_id):
    with open(prd_path) as f:
        prd = json.load(f)
    for story in prd.get("stories", []):
        if story.get("id") == story_id:
            return story
    return {}


def recorded(progress_path, offset, story_id):
    """Whether a heading for the story was written past `offset`.

    Every entry opens with a `## ` heading naming its story, whatever the rest
    of it says. The id has to stand alone: US-31 is not an entry for US-313.
    A log shorter than the offset was rotated, so all of it is new.
    """
    try:
        with open(progress_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(offset if f.tell() >= offset else 0)
            text = f.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return False
    heading = re.compile(
        r"^##\s.*(?<![\w-])" + re.escape(story_id) + r"(?![\w-])", re.MULTILINE
    )
    return bool(heading.search(text))


def work_in(repo, branch):
    """What git shows of the branch: its worktree, head, and what only it holds.

    Untracked files count: a new source file is work as much as an edit is, and
    build output is what .gitignore is for.
    """
    found = {"worktree": None, "head": None, "uncommitted": None, "unpushed": None}
    if not branch:
        return found
    try:
        worktree = worktree_for_branch(repo, branch)
        if not worktree or not os.path.isdir(worktree):
            return found
        found["worktree"] = worktree
        found["head"] = run("git", "-C", worktree, "rev-parse", "--short", "HEAD")
        status = run("git", "-C", worktree, "status", "--porcelain")
        found["uncommitted"] = len(status.splitlines())
        found["unpushed"] = int(
            run(
                "git",
                "-C",
                worktree,
                "rev-list",
                "--count",
                "HEAD",
                "--not",
                "--remotes",
            )
        )
    except (CalledProcessError, ValueError):
        pass
    return found


def describe(branch, found):
    if not branch:
        return "The story has no branch recorded, so there is no worktree to report."
    if not found["worktree"]:
        return f"No worktree holds {branch}."
    if found["uncommitted"] is None:
        return f"{found['worktree']} holds {branch}; git could not read it."
    return (
        f"{found['worktree']} holds {branch} at {found['head']}, with "
        f"{found['uncommitted']} uncommitted file(s) and "
        f"{found['unpushed']} commit(s) no remote has."
    )


def entry(story_id, status, work, resumed, session_id, log):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    after = f", resumed {resumed} time(s)" if resumed else ""
    lines = [
        f"## {now} - {story_id} - Status: {status} (iteration ended, no transition)",
        "- Why it stopped: the session ended without moving the story or saying why"
        f"{after}. run.sh wrote this entry from git, so it cannot say which gates passed.",
        f"- Work left: {work}",
    ]
    if log:
        lines.append(f"- Artefacts: {log} -- read its end before re-running anything.")
    if session_id:
        lines.append(f"- **Resume command:** `claude -r {session_id}`")
    lines.append("---")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prd", required=True)
    parser.add_argument("--story-id", required=True)
    parser.add_argument("--start-status", required=True)
    parser.add_argument("--start-branch", default="")
    parser.add_argument("--progress-file", required=True)
    parser.add_argument("--progress-offset", type=int, default=0)
    parser.add_argument("--repo", required=True, help="the target repo's main checkout")
    parser.add_argument("--resumed", type=int, default=0)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--log", default="")
    args = parser.parse_args(argv)

    story = story_in_prd(args.prd, args.story_id)
    end_status = story.get("status") or ""
    branch = story.get("branchName") or args.start_branch or None
    was_recorded = recorded(args.progress_file, args.progress_offset, args.story_id)
    stopped_short = (
        args.start_status == "pending" and end_status == "pending" and not was_recorded
    )
    found = work_in(args.repo, branch)
    work = describe(branch, found)

    print(
        json.dumps(
            {
                "stoppedShort": stopped_short,
                "startStatus": args.start_status,
                "endStatus": end_status,
                "recorded": was_recorded,
                "branch": branch,
                **found,
                "work": work,
                "entry": entry(
                    args.story_id,
                    end_status,
                    work,
                    args.resumed,
                    args.session_id,
                    args.log,
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
