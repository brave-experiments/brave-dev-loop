#!/usr/bin/env python3
"""Phase 3 collection for the discover-bugs skill.

Zero-LLM. Reads all subagent result files, applies the confidence gate,
deduplicates against previously-reported findings and open ai-generated
issues, PRIORITIZES survivors (P0/P1/P2), then EITHER writes a local report
OR files GitHub issues labeled `ai-generated`. Updates the scan cache.

In POD MODE it also advances the per-pod scan state: during a baseline sweep
it moves the cursor forward (and flips the pod to changed-only once the sweep
is complete); in changed-only mode it records the new base commit.

Usage:
    python3 collect-findings.py --work-dir DIR [--file-issues]
"""

import argparse
import hashlib
import json
import os
import re
# subprocess is used only to invoke `git` and `gh` with fixed, list-form argv
# (never shell=True, never a shell string), so there is no shell-injection surface.
import subprocess  # nosemgrep: gitlab.bandit.B404
import sys
from datetime import datetime, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BOT_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "..", ".."))
sys.path.insert(0, os.path.join(_BOT_DIR, "scripts"))

from lib.load_config import load_config, require_config, get_config

_config = load_config()
ISSUE_REPO = require_config(_config, "project.issueRepository")
AI_LABEL = "ai-generated"
LOGS_DIR = os.path.join(_BOT_DIR, "logs")

_SEV_W = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_HOT_CLASSES = {"security", "crypto", "mojo-ipc", "lifetime-uaf", "bounds",
                "integer-over-underflow", "stack-overflow"}


def log(msg):
    print(msg, file=sys.stderr)


def fingerprint(f):
    """Stable dedup id for a finding: file + class + normalized title.

    Non-cryptographic — this is a content-addressed key for deduping findings,
    not a signature. SHA-256 (truncated) purely to satisfy static scanners.
    """
    title = re.sub(r"\s+", " ", (f.get("title") or "").strip().lower())
    key = f"{f.get('file','')}|{f.get('bug_class','')}|{title}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def prioritize(f):
    """Attach _score (int) and _tier (P0/P1/P2) to a finding."""
    sev = (f.get("severity") or "medium").lower()
    cls = (f.get("bug_class") or "").lower()
    score = _SEV_W.get(sev, 2) + (1 if cls in _HOT_CLASSES else 0)
    tier = "P0" if score >= 5 else ("P1" if score >= 3 else "P2")
    f["_score"] = score
    f["_tier"] = tier
    return f


def load_manifest(work_dir):
    with open(os.path.join(work_dir, "manifest.json")) as f:
        return json.load(f)


def load_cache(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {"lastScanSha": None, "lastScanAt": None, "seen": {}}


def save_cache(path, cache):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cache, f, indent=2)


def open_issue_titles():
    """Titles of currently-open ai-generated issues, for cross-run dedup."""
    try:
        out = subprocess.run(
            ["gh", "issue", "list", "--repo", ISSUE_REPO, "--label", AI_LABEL,
             "--state", "open", "--limit", "300", "--json", "title"],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode == 0:
            return {re.sub(r"\s+", " ", i["title"].strip().lower())
                    for i in json.loads(out.stdout or "[]")}
    except Exception:
        pass
    return set()


def collect_results(manifest):
    findings = []
    for chunk in manifest["chunks"]:
        rf = chunk["results_file"]
        if not os.path.exists(rf):
            log(f"discover-bugs: no result file for {chunk['chunk_id']} (subagent skipped?)")
            continue
        try:
            with open(rf) as f:
                data = json.load(f)
            for item in data.get("findings", []):
                findings.append(item)
        except Exception as e:
            log(f"discover-bugs: bad result file {rf}: {e}")
    return findings


def issue_body(f, head_sha, pod_label):
    pod_line = f"- **Pod:** {pod_label}\n" if pod_label else ""
    return f"""**Automated bug scan finding — please confirm before acting.**

This issue was filed by an automated static scan (label `{AI_LABEL}`). It has
not been human-verified. If it is a false positive, close it.

- **Priority:** {f.get('_tier')} (score {f.get('_score')})
{pod_line}- **File:** `{f.get('file')}:{f.get('line')}`
- **Class:** {f.get('bug_class')}
- **Severity:** {f.get('severity')}  |  **Scanner confidence:** {f.get('confidence')}
- **Scanned commit:** `{(head_sha or '')[:12]}`

**What goes wrong**
{f.get('description','(none)')}

**Suggested direction**
{f.get('suggested_fix','(none)')}
"""


def update_pod_cache(cache, manifest, head_sha, now):
    """Advance the per-pod full-sweep cursor; on completion, reset for the next
    cycle and stamp the cooldown clock. Full-codebase model — no changed-only."""
    if manifest.get("cycle_complete_after"):
        # Whole tree swept this cycle. Reset the cursor so the next cycle starts
        # from the top, and record when we finished so the cooldown can elapse.
        cache["cycleComplete"] = True
        cache["cycleCompletedAt"] = now
        cache["cycleCompletedSha"] = head_sha
        cache["sweepCursor"] = ""
    else:
        # Mid-sweep: advance the cursor to the last file scanned this run.
        cache["sweepCursor"] = manifest.get("batch_last_path") or cache.get("sweepCursor", "")
        cache["cycleComplete"] = False
    cache["lastScanAt"] = now


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--file-issues", action="store_true",
                    help="Create GitHub issues (default: local report only).")
    args = ap.parse_args()

    manifest = load_manifest(args.work_dir)
    is_pod = manifest.get("mode") == "pod"
    pod_id = manifest.get("pod_id")
    pod_label = manifest.get("pod_label")
    cache_path = manifest["cache_path"]
    head_sha = manifest.get("head_sha")
    cache = load_cache(cache_path)
    seen = cache.setdefault("seen", {})

    raw = collect_results(manifest)

    # Confidence gate: keep only high-confidence.
    gated = [f for f in raw if (f.get("confidence") or "").lower() == "high"]
    dropped_conf = len(raw) - len(gated)

    # Dedup against local seen-list and open ai-generated issues.
    existing_titles = open_issue_titles()
    fresh, dup = [], 0
    for f in gated:
        fp = fingerprint(f)
        title_norm = re.sub(r"\s+", " ", (f.get("title") or "").strip().lower())
        if fp in seen or title_norm in existing_titles:
            dup += 1
            continue
        f["_fp"] = fp
        prioritize(f)
        fresh.append(f)

    # Highest priority first.
    fresh.sort(key=lambda f: (f["_score"], _SEV_W.get((f.get("severity") or "").lower(), 0)),
               reverse=True)

    tag = f"[{pod_id}]" if is_pod else ""
    log(f"discover-bugs{tag}: {len(raw)} raw finding(s); "
        f"{dropped_conf} below high-confidence; {dup} duplicate(s); "
        f"{len(fresh)} fresh.")

    now = datetime.now(timezone.utc).isoformat()
    filed = []

    if args.file_issues:
        for f in fresh:
            pod_prefix = f"[{pod_id}]" if is_pod else ""
            title = f"[ai-bug][{f['_tier']}]{pod_prefix} {f.get('title')}"
            body = issue_body(f, head_sha, pod_label)
            try:
                out = subprocess.run(
                    ["gh", "issue", "create", "--repo", ISSUE_REPO,
                     "--label", AI_LABEL, "--title", title, "--body", body],
                    capture_output=True, text=True, timeout=90,
                )
                if out.returncode == 0:
                    url = out.stdout.strip()
                    seen[f["_fp"]] = {"issue": url, "reportedAt": now, "tier": f["_tier"]}
                    filed.append(url)
                    log(f"discover-bugs{tag}: filed {f['_tier']} {url}")
                else:
                    log(f"discover-bugs{tag}: gh issue create failed: {out.stderr.strip()}")
            except Exception as e:
                log(f"discover-bugs{tag}: gh issue create error: {e}")
    else:
        for f in fresh:
            seen[f["_fp"]] = {"issue": None, "reportedAt": now, "tier": f["_tier"]}

    # Local report artifact.
    os.makedirs(LOGS_DIR, exist_ok=True)
    stamp = now.replace(":", "").replace("-", "")[:15]
    slug = f"-{pod_id}" if is_pod else ""
    report_json = os.path.join(LOGS_DIR, f"discover-bugs{slug}-{stamp}.json")
    with open(report_json, "w") as fj:
        json.dump({"scanned_at": now, "pod": pod_id, "scan_mode": manifest.get("scan_mode"),
                   "head_sha": head_sha, "fresh": fresh, "filed": filed,
                   "counts": {"raw": len(raw), "dropped_conf": dropped_conf,
                              "duplicate": dup, "fresh": len(fresh)}},
                  fj, indent=2)

    report_md = os.path.join(LOGS_DIR, f"discover-bugs{slug}-{stamp}.md")
    with open(report_md, "w") as fh:
        area = f"{pod_label} (`{pod_id}`)" if is_pod else "repo-wide"
        fh.write(f"# Bug scan — {area} — {now}\n\n"
                 f"Mode: {manifest.get('scan_mode', manifest.get('base_desc'))}  |  "
                 f"Base: {manifest.get('base_desc')}  |  "
                 f"Commit: {(head_sha or '')[:12]}\n\n")
        if not fresh:
            fh.write("No fresh high-confidence findings.\n")
        for f in fresh:
            link = f" — {seen[f['_fp']]['issue']}" if seen.get(f["_fp"], {}).get("issue") else ""
            fh.write(f"## {f['_tier']} · {f.get('title')}{link}\n"
                     f"- `{f.get('file')}:{f.get('line')}` · {f.get('bug_class')} · "
                     f"sev {f.get('severity')} · score {f['_score']}\n"
                     f"- {f.get('description')}\n"
                     f"- Fix: {f.get('suggested_fix')}\n\n")

    # Update scan state.
    if is_pod:
        update_pod_cache(cache, manifest, head_sha, now)
    else:
        if head_sha:
            cache["lastScanSha"] = head_sha
        cache["lastScanAt"] = now
    save_cache(cache_path, cache)

    mode = "filed as issues" if args.file_issues else "LOCAL REPORT ONLY (no issues filed)"
    tiers = {}
    for f in fresh:
        tiers[f["_tier"]] = tiers.get(f["_tier"], 0) + 1
    log("=" * 60)
    log(f"discover-bugs{tag} summary — {mode}")
    log(f"  fresh high-confidence: {len(fresh)}  "
        f"(P0={tiers.get('P0',0)} P1={tiers.get('P1',0)} P2={tiers.get('P2',0)})")
    if is_pod:
        log(f"  pod scan_mode: {manifest.get('scan_mode')}"
            + ("  [full sweep COMPLETE — pod idles until next re-scan cycle]"
               if manifest.get("cycle_complete_after") else ""))
    if args.file_issues:
        log(f"  issues created: {len(filed)}")
    log(f"  report: {report_md}")
    log("=" * 60)


if __name__ == "__main__":
    main()
