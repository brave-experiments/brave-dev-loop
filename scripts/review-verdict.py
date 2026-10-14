#!/usr/bin/env python3
"""What the configured reviewer has said about a pull request's current head.

    scripts/review-verdict.py 351
    scripts/review-verdict.py --repo owner/name --reviewer someone 351

Prints one JSON object. `verdict` is one of:

    pending   the reviewer has not reviewed this head yet
    draft     as pending, but the pull request is a draft nobody asked the
              reviewer to look at, so no review is coming
    approved  the review at this head asks for nothing: an approval, or a
              comment review that leaves none of the reviewer's threads open
    changes   the review at this head asks for work: a request for changes, or
              a comment review that leaves a thread of the reviewer's open
    closed    the pull request is merged or closed, so nothing is coming
    error     GitHub could not be read; nothing is known

Only a review made at the head commit counts. A push makes every earlier review
a review of different code, so the answer after a push is `pending` until the
reviewer looks again. `requested` says whether the reviewer is still in the
pull request's requested reviewers, which is how a pending review is told from
one nobody has asked for.

Exit 0 whenever GitHub answered (whatever the verdict), 1 on `error`, 2 on bad
arguments. Read-only, and needs no account of the reviewer's own: the reviews
are public to anyone who can read the pull request.
"""

import argparse
import json
import os
import subprocess  # nosemgrep
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.load_config import get_config, load_config  # noqa: E402

# A review that was never submitted (PENDING) or was withdrawn (DISMISSED) says
# nothing about the code.
COUNTED_STATES = {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}

THREADS_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!,$after:String){"
    "repository(owner:$owner,name:$name){pullRequest(number:$number){"
    "reviewThreads(first:100,after:$after){pageInfo{hasNextPage endCursor}"
    "nodes{isResolved comments(first:1){nodes{author{login}}}}}}}}"
)
# One page is 100 threads; this many pages is far past a pull request anyone
# could address in one pass, and bounds the calls a slow poll can make.
MAX_THREAD_PAGES = 5


class GitHubError(Exception):
    pass


def gh(*args):
    """Run gh and return its stdout, or raise GitHubError."""
    try:
        result = subprocess.run(  # nosemgrep
            ["gh", *args], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GitHubError(f"gh {args[0]}: {e}") from e
    if result.returncode != 0:
        raise GitHubError(f"gh {args[0]}: {result.stderr.strip() or result.returncode}")
    return result.stdout


def same_login(a, b):
    return (a or "").lower() == (b or "").lower()


def latest_review(reviews, reviewer, head):
    """The reviewer's newest counted review made at `head`, or None."""
    mine = [
        r
        for r in reviews
        if same_login((r.get("author") or {}).get("login"), reviewer)
        and r.get("state") in COUNTED_STATES
        and (r.get("commit") or {}).get("oid") == head
    ]
    return max(mine, key=lambda r: r.get("submittedAt") or "") if mine else None


def requested_logins(pr):
    return [r.get("login") for r in pr.get("reviewRequests") or [] if r.get("login")]


def open_threads_by(repo, number, reviewer):
    """How many unresolved review threads the reviewer started."""
    owner, _, name = repo.partition("/")
    count, after = 0, None
    for _ in range(MAX_THREAD_PAGES):
        args = [
            "api",
            "graphql",
            "-f",
            f"query={THREADS_QUERY}",
            "-f",
            f"owner={owner}",
            "-f",
            f"name={name}",
            "-F",
            f"number={number}",
        ]
        if after:
            args += ["-f", f"after={after}"]
        try:
            data = json.loads(gh(*args))
            threads = data["data"]["repository"]["pullRequest"]["reviewThreads"]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise GitHubError(f"unreadable review threads: {e}") from e
        for t in threads["nodes"]:
            if t.get("isResolved"):
                continue
            first = (t.get("comments") or {}).get("nodes") or [{}]
            if same_login((first[0].get("author") or {}).get("login"), reviewer):
                count += 1
        if not threads["pageInfo"]["hasNextPage"]:
            break
        after = threads["pageInfo"]["endCursor"]
    return count


def verdict(pr, reviewer, threads_open):
    """The answer for a pull request as `gh pr view` reports it.

    `threads_open` is called, and only for a comment review, because that is the
    one state that does not say by itself whether it asks for anything.
    """
    state = pr.get("state")
    head = pr.get("headRefOid") or ""
    out = {
        "state": state,
        "head": head,
        "requested": any(same_login(login, reviewer) for login in requested_logins(pr)),
        "review": None,
        "openThreads": None,
    }
    if state != "OPEN":
        return {**out, "verdict": "closed"}
    review = latest_review(pr.get("reviews") or [], reviewer, head)
    if review is None:
        if pr.get("isDraft") and not out["requested"]:
            return {**out, "verdict": "draft"}
        return {**out, "verdict": "pending"}
    out["review"] = {
        "state": review["state"],
        "submittedAt": review.get("submittedAt"),
    }
    if review["state"] == "APPROVED":
        return {**out, "verdict": "approved"}
    if review["state"] == "CHANGES_REQUESTED":
        return {**out, "verdict": "changes"}
    out["openThreads"] = threads_open()
    return {**out, "verdict": "changes" if out["openThreads"] else "approved"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pr", type=int, help="pull request number")
    parser.add_argument("--repo", help="owner/name (default: project.prRepository)")
    parser.add_argument(
        "--reviewer", help="login whose review counts (default: reviewer.username)"
    )
    args = parser.parse_args(argv)

    config = load_config()
    repo = args.repo or get_config(config, "project.prRepository")
    reviewer = args.reviewer or get_config(config, "reviewer.username")
    if not repo or not reviewer:
        print(
            "No reviewer account configured (reviewer.username in config.json).",
            file=sys.stderr,
        )
        return 2

    try:
        pr = json.loads(
            gh(
                "pr",
                "view",
                str(args.pr),
                "--repo",
                repo,
                "--json",
                "state,isDraft,headRefOid,reviews,reviewRequests",
            )
        )
        result = verdict(pr, reviewer, lambda: open_threads_by(repo, args.pr, reviewer))
    except (GitHubError, json.JSONDecodeError) as e:
        print(json.dumps({"verdict": "error", "error": str(e)}))
        return 1
    print(json.dumps({"pr": args.pr, "reviewer": reviewer, **result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
