"""A pull request's unified diff, including PRs past GitHub's 300-file limit.

`gh pr diff` asks the diff endpoint, which refuses a PR past 300 files with
HTTP 406 (`too_large`) — every Chromium upgrade. The files API pages through up
to 3000 files with the same hunk text per file, so the diff is rebuilt from it.
prepare-review.py and post-review.py both read the diff, and they must read the
same one: a finding prepare-review found in file 400 has to be in the diff
post-review places it against, or it is dropped as "file not in diff".

    from lib.pr_diff import fetch_pr_diff

    text = fetch_pr_diff("brave/brave-core", 39947)
"""

import json
import subprocess

# A file whose patch GitHub left out of the files API (binary, or a text diff
# too big to render). Callers that show diffs turn the section into a stub.
NO_PATCH = "Patch not available from GitHub"

# The files API stops listing here; a diff rebuilt at the cap is partial.
FILES_API_LIMIT = 3000


def diff_too_large(stderr):
    """GitHub refuses a whole-PR diff past 300 files (HTTP 406, "too_large")."""
    return "too_large" in stderr or "HTTP 406" in stderr


def fetch_pr_diff(repo, pr_number, log=None):
    """The PR's unified diff. Raises RuntimeError when it cannot be had whole."""
    result = subprocess.run(
        ["gh", "pr", "diff", "--repo", repo, str(pr_number)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode == 0:
        return result.stdout
    if not diff_too_large(result.stderr):
        raise RuntimeError(f"Failed to fetch diff: {result.stderr.strip()}")
    if log:
        log(
            f"    Diff of #{pr_number} is over GitHub's limit; fetching it file by file"
        )
    return fetch_diff_by_file(repo, pr_number)


def fetch_diff_by_file(repo, pr_number):
    """The PR's diff rebuilt from the paginated files API."""
    result = subprocess.run(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{repo}/pulls/{pr_number}/files?per_page=100",
            "--jq",
            ".[]",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to fetch files: {result.stderr.strip()}")
    files = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if len(files) >= FILES_API_LIMIT:
        raise RuntimeError(
            f"PR lists {len(files)} files, the files API's limit; the diff would be incomplete"
        )
    sections = [file_section(f) for f in files]
    return "\n".join(sections) + "\n" if sections else ""


def file_section(f):
    """One file's section of a unified diff, from a files API entry. The hunks
    are GitHub's own; the headers are rebuilt from the file's status."""
    path = f["filename"]
    old = f.get("previous_filename") or path
    status = f.get("status")
    lines = [f"diff --git a/{old} b/{path}"]
    if status == "added":
        lines.append("new file mode 100644")
    elif status == "removed":
        lines.append("deleted file mode 100644")
    elif status == "renamed":
        lines += [f"rename from {old}", f"rename to {path}"]
    patch = f.get("patch")
    if patch is None:
        # A pure rename has no patch to lose. Anything else does, whatever
        # `changes` says: GitHub reports 0 for some text it will not render.
        if not (status == "renamed" and f.get("changes", 0) == 0):
            lines.append(f"{NO_PATCH}: a/{old} b/{path}")
        return "\n".join(lines)
    lines.append("--- /dev/null" if status == "added" else f"--- a/{old}")
    lines.append("+++ /dev/null" if status == "removed" else f"+++ b/{path}")
    lines.append(patch.rstrip("\n"))
    return "\n".join(lines)
