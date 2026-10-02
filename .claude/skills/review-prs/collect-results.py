#!/usr/bin/env python3
"""Collect the validators' result files and feed them to post-review.py.

An unfinished review is neither posted nor cached, so the next run retries it.

Usage:
    python3 collect-results.py --work-dir /tmp/review-prs-XXXXX [--auto]
"""

import argparse
import json
import os
import subprocess
import sys

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", ".."))

sys.path.insert(0, os.path.join(BOT_DIR, "scripts"))
from lib.load_config import load_config, load_profile, review_checks


def log(msg):
    """Print to stderr."""
    print(msg, file=sys.stderr)


def load_manifest(work_dir):
    """Load and return manifest.json from work_dir."""
    manifest_path = os.path.join(work_dir, "manifest.json")
    with open(manifest_path) as f:
        return json.load(f)


def collect_violations(pr):
    """(violations, validation_log, skip_reason); skip_reason is set only for an unfinished review."""
    if pr.get("review_incomplete"):
        return [], [], "no detect subagent wrote results"
    if "validation" not in pr:
        return [], [], "select-candidates.py did not run"
    validation = pr["validation"]
    if validation is None:
        return [], [], None
    results_file = validation.get("results_file", "")
    try:
        with open(results_file) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return [], [], f"the validator wrote no results ({e})"
    return data.get("violations", []), data.get("validation_log", []), None


def _count(n, noun):
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def describe_checks(pr, violations, project_checks=()):
    """The "how did this review decide" section a reader can expand, as markdown."""
    prompts = pr.get("subagent_prompts", [])
    finished = [p for p in prompts if os.path.isfile(p.get("results_file", ""))]
    total = pr.get("files_total", 0)
    read = pr.get("files_reviewed", total)
    if read == total:
        lines = [f"The review read {_count(total, 'changed file')}."]
    else:
        lines = [
            f"The review read {read} of the {_count(total, 'changed file')}: "
            "only those that changed since the bot last reviewed this pull request."
        ]

    kinds = {p.get("kind") for p in finished}
    checked = []
    docs = [p for p in finished if p.get("kind") == "rules"]
    if docs:
        names = sorted({os.path.splitext(p["doc"])[0] for p in docs})
        rules = sum(p.get("rule_count", 0) for p in docs)
        checked.append(
            f"**Written best practices.** The changes were compared with "
            f"{_count(rules, 'rule')} from this project's best-practice documents "
            f"({', '.join(names)})."
        )
    if "correctness" in kinds:
        checked.append(
            "**Bugs.** The changes were read for mistakes that would make the "
            "code misbehave or stop it building."
        )
    if "project" in kinds:
        item = "**This project's own criteria.**"
        if project_checks:
            item += " The changes were checked against these questions:\n" + "\n".join(
                f"  - {c}" for c in project_checks
            )
        checked.append(item)
    if checked:
        lines += ["", "What it checked:", ""] + [f"- {c}" for c in checked]

    if len(finished) < len(prompts):
        lines += [
            "",
            f"{len(prompts) - len(finished)} of {len(prompts)} checks produced no "
            "result and are not counted above.",
        ]

    detected = pr.get("detected", 0)
    validation = pr.get("validation") or {}
    candidates = validation.get("candidates", 0)
    lines.append("")
    if not detected:
        lines.append(
            "None of these checks flagged anything, so there was nothing to double-check."
        )
    elif not candidates:
        lines.append(
            f"The checks flagged {_count(detected, 'possible problem')}, all "
            "repeats of each other or of comments already on the pull request, "
            "so none needed a second look."
        )
    else:
        kept = len(violations)
        outcome = (
            f"kept {kept}"
            if kept
            else "found none of them to be a real problem introduced by this change"
        )
        lines.append(
            f"The checks flagged {_count(detected, 'possible problem')}. "
            f"{candidates} of them went to a second reader, who checked each "
            f"against the full source code rather than only the diff, and {outcome}."
        )

    body = "\n".join(lines)
    return (
        "<details>\n<summary>How this review reached its recommendation</summary>"
        f"\n\n{body}\n\n</details>"
    )


def build_post_review_input(manifest, project_checks=()):
    """Build the input JSON structure for post-review.py."""
    pr_results = []

    for pr in manifest.get("prs", []):
        violations, validation_log, skip_reason = collect_violations(pr)
        if skip_reason:
            log(
                f"NOT REVIEWED: PR #{pr['number']} — {skip_reason}; nothing "
                "posted and not cached, so the next run reviews it again"
            )
            continue

        pr_results.append(
            {
                "number": pr["number"],
                "title": pr.get("title", ""),
                "headRefOid": pr.get("headRefOid", ""),
                "hasApproval": pr.get("hasApproval", False),
                "fileHashesFile": pr.get("file_hashes_file"),
                "violations": violations,
                "validation_log": validation_log,
                "checks_details": describe_checks(pr, violations, project_checks),
            }
        )

    return {"pr_results": pr_results}


def cleanup_worktrees(manifest):
    """Remove git worktrees created by prepare-review.py for each PR."""
    target_repo_path = manifest.get("target_repo_path", "")
    if not target_repo_path:
        return
    for pr in manifest.get("prs", []):
        worktree_path = pr.get("worktree_path")
        if not worktree_path:
            continue
        if not os.path.isdir(worktree_path):
            continue
        result = subprocess.run(
            [
                "git",
                "-C",
                target_repo_path,
                "worktree",
                "remove",
                worktree_path,
                "--force",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            log(
                f"WARNING: failed to remove worktree {worktree_path}: "
                f"{result.stderr.strip()}"
            )
        else:
            log(f"Removed worktree: {worktree_path}")


def print_cached_and_progress(manifest):
    """Print cached PR results and progress lines to stderr."""
    for cached in manifest.get("cached_prs", []):
        number = cached.get("number", "?")
        title = cached.get("title", "")
        reason = cached.get("reason", "cached")
        log(f"CACHED: PR #{number} ({title}) — {reason}")

    for line in manifest.get("progress_lines", []):
        log(line)


def main():
    parser = argparse.ArgumentParser(
        description="Collect subagent results and run post-review.py"
    )
    parser.add_argument(
        "--work-dir",
        required=True,
        help="Temp directory with manifest.json and results",
    )
    parser.add_argument(
        "--auto", action="store_true", help="Pass --auto to post-review.py"
    )
    args = parser.parse_args()

    # Load manifest
    try:
        manifest = load_manifest(args.work_dir)
    except (OSError, json.JSONDecodeError) as e:
        log(f"ERROR: failed to load manifest: {e}")
        sys.exit(1)

    bot_username = manifest.get("bot_username", "")
    pr_repo = manifest.get("pr_repo", "")
    auto_mode = args.auto or manifest.get("auto_mode", False)

    if not bot_username or not pr_repo:
        log("ERROR: manifest missing bot_username or pr_repo")
        sys.exit(1)

    # Print cached PRs and progress lines first (before post-review output)
    print_cached_and_progress(manifest)

    # Build post-review input
    profile = load_profile(load_config(), BOT_DIR)
    post_review_data = build_post_review_input(manifest, review_checks(profile))

    # Collection stats
    prs = manifest.get("prs", [])
    total_chunks = sum(len(pr.get("subagent_prompts", [])) for pr in prs)
    results_found = sum(
        1
        for pr in prs
        for sp in pr.get("subagent_prompts", [])
        if os.path.isfile(sp.get("results_file", ""))
    )
    validations = [pr["validation"] for pr in prs if pr.get("validation")]
    candidates = sum(v.get("candidates", 0) for v in validations)

    total_violations = sum(
        len(pr_r.get("violations", []))
        for pr_r in post_review_data.get("pr_results", [])
    )
    total_validated = sum(
        len(pr_r.get("validation_log", []))
        for pr_r in post_review_data.get("pr_results", [])
    )

    log(f"\n{'=' * 60}")
    log("COLLECTION SUMMARY")
    log(f"{'=' * 60}")
    log(f"Detect prompts: {total_chunks}")
    log(f"Detect results found: {results_found}")
    log(f"Detect results missing: {total_chunks - results_found}")
    log(f"Candidates validated: {candidates} across {len(validations)} validators")
    log(f"PRs left for the next run: {len(prs) - len(post_review_data['pr_results'])}")
    log(f"Total violations collected: {total_violations}")
    log(f"Total validation log entries: {total_validated}")
    for pr_r in post_review_data.get("pr_results", []):
        v_count = len(pr_r.get("violations", []))
        log(f"  PR #{pr_r['number']}: {v_count} violations")
    log(f"{'=' * 60}\n")

    # Write input file
    input_path = os.path.join(args.work_dir, "post-review-input.json")
    with open(input_path, "w") as f:
        json.dump(post_review_data, f, indent=2)

    # Log any errors from manifest
    for error in manifest.get("errors", []):
        log(f"ERROR (from prepare): {error}")

    # Run post-review.py
    cmd = [
        "python3",
        os.path.join(SCRIPT_DIR, "post-review.py"),
        "--pr-repo",
        pr_repo,
        "--bot-username",
        bot_username,
        "--input",
        input_path,
    ]
    if auto_mode:
        cmd.append("--auto")

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=BOT_DIR)

    # Pass through stderr (summary log)
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")

    # Pass through stdout (result JSON)
    if result.stdout:
        print(result.stdout, end="")

    cleanup_worktrees(manifest)

    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
