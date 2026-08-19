#!/usr/bin/env python3
"""Phase 1 pre-work for the discover-bugs skill.

Zero-LLM candidate selection for a bounded static bug scan of the target
repository. The main LLM session only orchestrates — it never sees file
contents; everything heavy goes through files.

Two selection modes:

  * POD MODE (--pod <id>): scan ONE product/pod from pods.json. Each pod runs
    as its own cron job / subagent. Full-codebase model — the pod's ENTIRE tree
    on master is scanned as full source (never diffs), swept in bounded slices
    that rotate across runs via a cursor in the per-pod cache. When a full sweep
    completes the pod idles for a cooldown (--rescan-days / per-pod
    'rescan_interval_days'), then sweeps the whole tree again. There is no
    changed-only phase: the whole codebase is re-audited every cycle.

  * LEGACY MODE (no --pod): repo-wide diff scan of recently-changed files.
    Kept for `/discover-bugs --since-cache|--commits N|--days N`.

Usage:
    python3 prepare-scan.py --pod ai-chat [--max-files N] [--files-per-chunk N] [--auto]
    python3 prepare-scan.py --pod ai-chat --check      # gate: exit 0 if work exists
    python3 prepare-scan.py --sync-doc                 # regenerate PODS.md from pods.json
    python3 prepare-scan.py [--since-cache|--commits N|--days N]   # legacy
"""

import argparse
import fnmatch
import json
import os
# subprocess is used only to invoke `git` with fixed, list-form argv (never
# shell=True, never a shell string), so there is no shell-injection surface.
import subprocess  # nosemgrep: gitlab.bandit.B404
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BOT_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "..", ".."))
sys.path.insert(0, os.path.join(_BOT_DIR, "scripts"))

from lib.load_config import load_config, require_config, get_config

# --- config -----------------------------------------------------------------
_config = load_config()
ISSUE_REPO = require_config(_config, "project.issueRepository")


def _resolve_target(rel):
    """Match run.sh: absolute paths as-is, else relative to the bot dir's parent."""
    if os.path.isabs(rel):
        return os.path.normpath(rel)
    return os.path.normpath(os.path.join(_BOT_DIR, "..", rel))


TARGET_REPO_PATH = _resolve_target(require_config(_config, "project.targetRepoPath"))
PODS_PATH = os.path.join(_SCRIPT_DIR, "pods.json")
# Per-pod cache files live here (one JSON per pod) so parallel pod cron jobs
# never contend on a single cache file.
POD_CACHE_DIR = os.path.join(_BOT_DIR, ".ignore", "discover-bugs")
# Legacy repo-wide cache (non-pod mode).
CACHE_PATH = os.path.join(_BOT_DIR, ".ignore", "discover-bugs-cache.json")

# Source extensions worth scanning for logic/lifetime bugs.
SOURCE_EXTS = {".cc", ".cpp", ".cxx", ".mm", ".m", ".h", ".hpp", ".ts", ".tsx", ".js"}
# Paths/patterns to skip — tests and generated/vendored trees are low signal.
SKIP_SUBSTR = ("/test/", "/tests/", "/third_party/", "/node_modules/", "/gen/")
SKIP_SUFFIX = ("_unittest.cc", "_browsertest.cc", "_unittest.mm", ".test.ts", ".test.tsx")

DEFAULT_MAX_FILES = 30
# Cooldown between full-codebase sweeps of a pod (days). After a pod finishes
# sweeping its whole tree it goes idle for this long, then re-scans from scratch.
DEFAULT_RESCAN_DAYS = 14
DEFAULT_FILES_PER_CHUNK = 5

# Bug-class rubric handed to every subagent. Broad on purpose: the ask is
# "code issues, bugs, security issues, any type" — memory safety, arithmetic,
# concurrency, and logic errors.
RUBRIC = """\
Scan ONLY for high-confidence, concrete defects in the classes below. Do NOT
report style, naming, or subjective design preferences.

1. Lifetime / use-after-free: dangling `raw_ptr`/`raw_ref`, pointers outliving
   their owner, missing `RegisterWillDetach`/unsubscribe on teardown, capturing
   `this`/refs in callbacks that outlive the object, iterator invalidation.
2. Null / bad deref: unchecked deref of a pointer that can be null (`GetFoo()`
   returning null, `WeakPtr` used after invalidation), unchecked `std::optional`.
3. Bounds / memory safety: off-by-one, out-of-bounds index/iterator, unchecked
   `.at()`/`[]`, buffer over-read/over-write, unbounded copy from untrusted size.
4. Integer over/underflow & numeric: signed/unsigned overflow, truncating casts,
   underflow on subtraction of unsigned amounts (common in wallet balance/fee
   math), division by zero, precision loss.
5. Stack overflow / unbounded recursion: recursion or allocation driven by
   untrusted depth/size with no limit.
6. Threading / sequence: cross-sequence access without posting, missing
   `SEQUENCE_CHECKER`, data races on shared state, blocking the UI thread.
7. Mojo / IPC & untrusted input: trusting a renderer- or server-supplied value
   without validation, missing `mojo::ReportBadMessage`, parsing untrusted
   network/JSON without bounds checks, self-owned receiver lifetime.
8. Security & crypto: missing input validation, injection, weak/misused crypto,
   key/secret lifetime (not zeroed), TOCTOU, unvalidated URLs/origins,
   entitlement/permission bypass.
9. Uninitialized / resource: uninitialized member read, leaked handle/fd,
   missing `Close()`/`Release()` on an error path.
10. Logic errors: inverted conditions, wrong operator, incorrect state
    transition, mishandled error/return value that leads to a concrete failure.

For each finding you MUST point to the exact line and explain the concrete
failure (what input/state triggers it, what goes wrong). If you are not
confident it is a real bug, DROP it — false positives are worse than misses.
"""


def log(msg):
    print(msg, file=sys.stderr)


def git(*args):
    """Read-only git in the target repo. Returns stdout (stripped) or ''."""
    try:
        out = subprocess.run(
            ["git", "-C", TARGET_REPO_PATH, *args],
            capture_output=True, text=True, timeout=120,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


# --- pod registry & selection -----------------------------------------------

def load_pods():
    with open(PODS_PATH) as f:
        return json.load(f)


def find_pod(pods_doc, pod_id):
    for p in pods_doc.get("pods", []):
        if p["id"] == pod_id:
            return p
    return None


def pod_cache_path(pod_id):
    return os.path.join(POD_CACHE_DIR, f"{pod_id}.json")


def load_pod_cache(pod_id):
    try:
        with open(pod_cache_path(pod_id)) as f:
            return json.load(f)
    except Exception:
        return {"cycleComplete": False, "sweepCursor": "",
                "cycleCompletedAt": None, "lastScanAt": None}


def _pathspecs(globs):
    """Directory pathspecs to bound `git ls-files` for a set of globs."""
    specs = set()
    for g in globs:
        head = g.split("*", 1)[0]
        if "/" in head:
            specs.add(head.rsplit("/", 1)[0])
        else:
            specs.add(head)
    return sorted(s for s in specs if s)


def _matches(path, globs):
    return any(fnmatch.fnmatch(path, g) for g in globs)


def pod_all_files(pod):
    """All tracked, scannable files in the pod's tree (sorted, deterministic)."""
    specs = _pathspecs(pod["paths"])
    raw = git("ls-files", "--", *specs) if specs else ""
    include = pod["paths"]
    exclude = pod.get("exclude", [])
    files = []
    for ln in raw.splitlines():
        p = ln.strip()
        if not p or not is_scannable(p):
            continue
        if not _matches(p, include):
            continue
        if exclude and _matches(p, exclude):
            continue
        files.append(p)
    return sorted(set(files))


def is_scannable(path):
    p = path.lower()
    if Path(path).suffix.lower() not in SOURCE_EXTS:
        return False
    if any(s in p for s in SKIP_SUBSTR):
        return False
    if any(p.endswith(s) for s in SKIP_SUFFIX):
        return False
    return True


# --- legacy (non-pod) cache & selection -------------------------------------

def load_cache():
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except Exception:
        return {"lastScanSha": None, "lastScanAt": None, "seen": {}}


def resolve_base(args, cache):
    """Legacy base ref: return (base_ref, description). base_ref None = window."""
    if args.commits:
        return f"HEAD~{args.commits}", f"last {args.commits} commits"
    if args.days:
        return None, f"last {args.days} days"
    sha = cache.get("lastScanSha")
    if sha and git("rev-parse", "--verify", "--quiet", sha + "^{commit}"):
        return sha, f"since last scan ({sha[:12]})"
    log("No valid last-scan marker — falling back to last 7 days.")
    args.days = 7
    return None, "last 7 days (no cache)"


def changed_files(base_ref, days):
    if base_ref:
        raw = git("diff", "--name-only", f"{base_ref}..HEAD")
    else:
        raw = git("log", f"--since={days} days ago", "--name-only",
                  "--pretty=format:", "HEAD")
    files = sorted({ln.strip() for ln in raw.splitlines() if ln.strip()})
    return [f for f in files if is_scannable(f)]


# --- prompt construction -----------------------------------------------------

def file_diff(base_ref, path):
    """Diff for a changed file (bounded); full numbered file if no base."""
    if base_ref:
        d = git("diff", f"{base_ref}..HEAD", "--", path)
        if d:
            return d
    full = os.path.join(TARGET_REPO_PATH, path)
    try:
        with open(full, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        body = "\n".join(f"{i+1:>5}\t{ln}" for i, ln in enumerate(lines[:1200]))
        if len(lines) > 1200:
            body += f"\n... [truncated: {len(lines) - 1200} more lines not shown]"
        return body
    except Exception:
        return ""


def build_prompt(chunk_files, base_ref, results_file, pod=None):
    header = ("You are a C++/TypeScript bug-finding subagent scanning Brave (a "
              "Chromium fork) source for concrete defects.")
    if pod:
        header += (f"\n\nYou are scanning ONE product area: **{pod['label']}** "
                   f"(`{pod['id']}`). Pay special attention to: {pod.get('focus','')}")
    parts = [header + "\n\n", RUBRIC,
             "\n--- FILES TO SCAN "
             + ("(unified diffs; scan the changed/new code) ---\n"
                if base_ref else "(full source with line numbers) ---\n")]
    for path in chunk_files:
        parts.append(f"\n### {path}\n```\n{file_diff(base_ref, path)}\n```\n")
    parts.append(f"""
--- OUTPUT ---
Read the actual source files under {TARGET_REPO_PATH} as needed (follow includes
/ callers / callees) to CONFIRM each finding before reporting it. Then write a
JSON file to EXACTLY this path:

  {results_file}

Schema:
{{
  "findings": [
    {{
      "file": "<repo-relative path>",
      "line": <int>,
      "bug_class": "lifetime-uaf|null-deref|bounds|integer-over-underflow|stack-overflow|threading|mojo-ipc|security|uninitialized|resource-leak|logic-error",
      "severity": "critical|high|medium|low",
      "confidence": "low|medium|high",
      "title": "<one line>",
      "description": "<what input/state triggers it and what concretely goes wrong>",
      "suggested_fix": "<concrete direction>"
    }}
  ]
}}

Report ONLY confidence:high findings. If you find nothing solid, write
{{"findings": []}}. Do not invent bugs to fill the list.
""")
    return "".join(parts)


def write_chunks(work_dir, files, files_per_chunk, base_ref, pod):
    chunks = []
    for i in range(0, len(files), files_per_chunk):
        chunk_files = files[i: i + files_per_chunk]
        cid = f"chunk_{i // files_per_chunk}"
        prompt_file = os.path.join(work_dir, f"{cid}_prompt.txt")
        results_file = os.path.join(work_dir, f"{cid}_results.json")
        with open(prompt_file, "w") as f:
            f.write(build_prompt(chunk_files, base_ref, results_file, pod))
        chunks.append({"chunk_id": cid, "files": chunk_files,
                       "prompt_file": prompt_file, "results_file": results_file})
    return chunks


# --- pod-mode driver ---------------------------------------------------------

def _days_since(iso):
    """Whole days since an ISO timestamp; a large number if unset/unparseable."""
    if not iso:
        return 10 ** 6
    try:
        then = datetime.fromisoformat(iso)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - then).total_seconds() / 86400.0
    except Exception:
        return 10 ** 6


def run_pod_mode(args):
    pods_doc = load_pods()
    pod = find_pod(pods_doc, args.pod)
    if not pod:
        log(f"Error: unknown pod '{args.pod}'. Known: "
            + ", ".join(p["id"] for p in pods_doc["pods"]))
        sys.exit(2)

    cache = load_pod_cache(args.pod)
    head_sha = git("rev-parse", "HEAD")

    # Full-codebase model: ALWAYS scan the pod's whole tree on master (full
    # source, never diffs). The tree is swept in bounded slices, rotating across
    # runs via a cursor. When a full sweep completes, the pod goes idle for a
    # cooldown (rescan interval), then sweeps the whole tree again.
    rescan_days = pod.get("rescan_interval_days", args.rescan_days)
    all_files = pod_all_files(pod)
    total_pod = len(all_files)
    cursor = cache.get("sweepCursor", "") or ""
    cycle_complete = bool(cache.get("cycleComplete"))

    scan_mode = "full-sweep"
    base_ref = None  # full source content, no diffs

    if cycle_complete:
        # Waiting out the cooldown before starting the next full sweep.
        idle_days = _days_since(cache.get("cycleCompletedAt"))
        if idle_days >= rescan_days:
            cursor = ""  # start a fresh full sweep of the whole tree
        else:
            remaining = []
            batch = []
            is_last_batch = False
            done_before = total_pod
            wait = max(0, rescan_days - idle_days)
            base_desc = (f"idle — full sweep complete {idle_days:.1f}d ago; "
                         f"next re-scan in ~{wait:.1f}d (rescan every {rescan_days}d)")
            _emit_pod(args, pod, cache, head_sha, scan_mode, base_ref, batch,
                      is_last_batch, total_pod, base_desc)
            return

    remaining = [f for f in all_files if f > cursor]
    batch = remaining[: args.max_files]
    is_last_batch = 0 < len(remaining) <= args.max_files
    done_before = total_pod - len(remaining)
    base_desc = (f"full-codebase sweep {done_before + len(batch)}/{total_pod} "
                 f"file(s) of {pod['id']} tree (master)")

    _emit_pod(args, pod, cache, head_sha, scan_mode, base_ref, batch,
              is_last_batch, total_pod, base_desc)


def _emit_pod(args, pod, cache, head_sha, scan_mode, base_ref, batch,
              is_last_batch, total_pod, base_desc):
    # --check: gate only. Print yes/no and exit; never build a work dir.
    if args.check:
        has_work = len(batch) > 0
        print("yes" if has_work else "no")
        log(f"discover-bugs[{pod['id']}]: {scan_mode} — {len(batch)} file(s) to scan.")
        sys.exit(0 if has_work else 1)

    work_dir = tempfile.mkdtemp(prefix=f"discover-bugs-{pod['id']}-")
    chunks = write_chunks(work_dir, batch, args.files_per_chunk, base_ref, pod)

    batch_last_path = batch[-1] if batch else cache.get("sweepCursor", "")
    progress = [
        f"discover-bugs[{pod['id']}]: {pod['label']} — mode={scan_mode}",
        f"discover-bugs[{pod['id']}]: {base_desc}",
        f"discover-bugs[{pod['id']}]: {len(batch)} file(s) -> {len(chunks)} chunk(s)",
    ]
    if is_last_batch:
        progress.append(f"discover-bugs[{pod['id']}]: this run COMPLETES the full "
                        f"sweep; pod idles until the next re-scan cycle.")
    for line in progress:
        log(line)

    manifest = {
        "work_dir": work_dir,
        "mode": "pod",
        "pod_id": pod["id"],
        "pod_label": pod["label"],
        "scan_mode": scan_mode,
        "auto_mode": args.auto,
        "issue_repo": ISSUE_REPO,
        "target_repo_path": TARGET_REPO_PATH,
        "base_ref": base_ref,
        "base_desc": base_desc,
        "head_sha": head_sha,
        "pod_cache_path": pod_cache_path(pod["id"]),
        "cache_path": pod_cache_path(pod["id"]),
        "sweep_total": total_pod,
        "batch_size": len(batch),
        "batch_last_path": batch_last_path,
        "cycle_complete_after": bool(is_last_batch),
        "capped": False,
        "chunks": chunks,
        "progress_lines": progress,
    }
    manifest_path = os.path.join(work_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(json.dumps({"work_dir": work_dir, "manifest": manifest_path}))


# --- doc generation ----------------------------------------------------------

def sync_doc():
    pods_doc = load_pods()
    lines = [
        "# Bug-scan pods",
        "",
        "> **Generated from `pods.json` — do not edit by hand.**",
        "> Edit `pods.json` and run `python3 prepare-scan.py --sync-doc` to regenerate.",
        "",
        f"Target repo: **brave-core** · branch: **{pods_doc.get('target_branch','master')}**.",
        "",
        "Each pod below is one scan task: its own cron job and its own subagent. "
        "A pod sweeps its **entire tree** on master as full source (never diffs), "
        "bounded per run and rotating across runs until the whole tree is covered; "
        "then it idles for a cooldown and re-scans the whole codebase again. "
        "Findings are prioritized (P0/P1/P2) and filed as `ai-generated` issues "
        "for human confirmation.",
        "",
        f"**{len(pods_doc['pods'])} scan tasks:**",
        "",
        "| # | Pod id | Area | Paths |",
        "| - | ------ | ---- | ----- |",
    ]
    for i, p in enumerate(pods_doc["pods"], 1):
        paths = "<br>".join(f"`{g}`" for g in p["paths"])
        lines.append(f"| {i} | `{p['id']}` | {p['label']} | {paths} |")
    lines += ["", "## Focus per pod", ""]
    for p in pods_doc["pods"]:
        lines.append(f"- **{p['label']}** (`{p['id']}`): {p.get('focus','')}")
        if p.get("exclude"):
            lines.append(f"  - excludes: " + ", ".join(f"`{g}`" for g in p["exclude"]))
    lines.append("")
    out = os.path.join(_SCRIPT_DIR, "PODS.md")
    with open(out, "w") as f:
        f.write("\n".join(lines))
    log(f"Wrote {out} ({len(pods_doc['pods'])} pods).")


# --- legacy driver -----------------------------------------------------------

def run_legacy(args):
    cache = load_cache()
    base_ref, base_desc = resolve_base(args, cache)
    head_sha = git("rev-parse", "HEAD")

    files = changed_files(base_ref, args.days)
    total_found = len(files)
    capped = False
    if len(files) > args.max_files:
        files = files[: args.max_files]
        capped = True

    work_dir = tempfile.mkdtemp(prefix="discover-bugs-")
    chunks = write_chunks(work_dir, files, args.files_per_chunk, base_ref, None)

    progress = [f"discover-bugs: base = {base_desc}",
                f"discover-bugs: {total_found} scannable file(s) changed"
                + (f", capped to {args.max_files}" if capped else "")
                + f" -> {len(chunks)} chunk(s)"]
    if capped:
        progress.append(f"discover-bugs: WARNING — {total_found - args.max_files} "
                        f"changed file(s) NOT scanned this run (raise --max-files).")
    for line in progress:
        log(line)

    manifest = {
        "work_dir": work_dir,
        "mode": "legacy",
        "auto_mode": args.auto,
        "issue_repo": ISSUE_REPO,
        "target_repo_path": TARGET_REPO_PATH,
        "base_ref": base_ref,
        "base_desc": base_desc,
        "head_sha": head_sha,
        "cache_path": CACHE_PATH,
        "seen_count": len(cache.get("seen", {})),
        "total_changed": total_found,
        "capped": capped,
        "chunks": chunks,
        "progress_lines": progress,
    }
    manifest_path = os.path.join(work_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(json.dumps({"work_dir": work_dir, "manifest": manifest_path}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pod", help="Scan a single pod from pods.json.")
    ap.add_argument("--check", action="store_true",
                    help="Gate only: print yes/no and exit 0/1, no work dir.")
    ap.add_argument("--sync-doc", action="store_true",
                    help="Regenerate PODS.md from pods.json and exit.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--since-cache", action="store_true")
    g.add_argument("--commits", type=int)
    g.add_argument("--days", type=int)
    ap.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    ap.add_argument("--files-per-chunk", type=int, default=DEFAULT_FILES_PER_CHUNK)
    ap.add_argument("--rescan-days", type=int, default=DEFAULT_RESCAN_DAYS,
                    help="Cooldown (days) after a full sweep before re-scanning "
                         "the whole tree again. Per-pod override: "
                         "'rescan_interval_days' in pods.json.")
    ap.add_argument("--auto", action="store_true")
    args = ap.parse_args()

    if args.sync_doc:
        sync_doc()
        return

    if not os.path.isdir(os.path.join(TARGET_REPO_PATH, ".git")):
        log(f"Error: target repo not found at {TARGET_REPO_PATH}")
        sys.exit(1)

    if args.pod:
        run_pod_mode(args)
    else:
        run_legacy(args)


if __name__ == "__main__":
    main()
