---
name: discover-bugs
description: "Scan the target repo for likely bugs (lifetime/UAF, threading, null/bounds, integer over-underflow, stack overflow, mojo/IPC, security/crypto, logic errors) per product/pod, verify them, prioritize (P0/P1/P2), and file ai-generated GitHub issues for human confirmation. Triggers on: discover bugs, scan for bugs, bug scan, find bugs in code."
argument-hint: "[--pod <id> | --since-cache|--commits N|--days N] [auto] [file-issues]"
allowed-tools: Bash
---

# Discover Bugs (static scan)

Bounded, verify-first static scan of the target repository (**brave-core**,
**master** only) for concrete defects. Findings are **not** auto-fixed — they
are reported for a human to confirm. Confirmed issues then flow through the
normal pipeline (`add-backlog-to-prd` → `run.sh`).

## Two ways to scope a scan

- **Pod mode (`--pod <id>`)** — the primary mode. Scans ONE product/pod defined
  in `pods.json` (AI Chat/Leo, Wallet split by chain/keyring/core, Shields,
  Adblock, Fingerprinting, Sync, VPN, Talk, Talk Premium/SKUs, News, Sidebar,
  Speedreader, Tor, Wayback, Web Discovery, Email Aliases). Each pod is its own
  cron job and its own subagent, so a run works exactly one task. See
  [PODS.md](PODS.md) (generated from `pods.json`).
  - **Full-codebase, cyclic:** a pod scans its **entire tree** on master as
    full source (never diffs) — bounded per run, rotating across runs via a
    cursor in `.ignore/discover-bugs/<pod>.json` until the whole tree is covered.
    When the sweep completes the pod idles for a cooldown
    (`--rescan-days`, default 14; per-pod `rescan_interval_days` in `pods.json`),
    then re-scans the whole codebase again. There is **no changed-only phase**.
- **Legacy mode (no `--pod`)** — repo-wide diff of recently-changed files
  (`--since-cache|--commits N|--days N`). Kept for ad-hoc use.

## Prioritization

`collect-findings.py` scores every surviving finding by severity + bug class
(memory-safety/security/crypto/IPC classes get a bump) into **P0/P1/P2**, sorts
by priority, and puts the tier in the issue title (`[ai-bug][P0][<pod>] …`) and
body.

**NEVER** @-mention or tag anyone in a filed issue (repo rule). The
`ai-generated` label is the sole provenance signal.

---

## Architecture: file-based pipeline (mirrors review-prs)

All heavy data goes through files, not the LLM context:

1. **prepare-scan.py** (zero LLM tokens) — selects the next slice of source
   files to scan, writes one subagent prompt file per chunk, emits a manifest.
2. **Subagents** (subagent tokens only) — each reads its prompt file, reads the
   real source to CONFIRM each finding, writes a results JSON file.
3. **collect-findings.py** (zero LLM tokens) — confidence gate, dedup, then
   local report or issue filing; updates the scan cache.

---

## The Job

When invoked with `/discover-bugs [--pod <id> | --since-cache|--commits N|--days N] [auto] [file-issues]`:

### Step 1: Prepare (zero LLM tokens)

```bash
BOT_DIR="<absolute path to brave-dev-bot directory>"
# Pod mode (primary):
python3 $BOT_DIR/.claude/skills/discover-bugs/prepare-scan.py --pod <id>
# Legacy mode:
python3 $BOT_DIR/.claude/skills/discover-bugs/prepare-scan.py [--since-cache|--commits N|--days N]
```

In pod mode the script auto-selects the next full-sweep slice from the pod's
cache (or emits an empty batch while the pod is in its re-scan cooldown). Legacy
default is `--since-cache` (diff since the last scan; falls back to last 7 days
on first run). Stdout is a tiny JSON: `{"work_dir":..., "manifest":...}`. Parse
it to get `work_dir`.

### Step 2: Read manifest

Read `{work_dir}/manifest.json`. Print its `progress_lines` (for cron logs). Key
fields: `chunks` (each has `prompt_file` + `results_file`), `issue_repo`,
`target_repo_path`, `capped` (log the warning if true — files were dropped).

If `chunks` is empty, skip to Step 4 (sweep complete and still in cooldown —
nothing to scan this run).

### Step 3: Launch subagents

For every chunk, launch a **Task subagent** (`subagent_type: "general-purpose"`)
**on a cheap model** — pass `model: "claude-haiku-4-5"` (or `"claude-sonnet-5"`
if depth matters more than cost). Bug confirmation is a bounded, well-specified
task and does NOT need Opus; the model tier is the single biggest cost lever
(see **Cost** below). **Launch ALL chunks in a single message** so they run
concurrently:

```
Read your full scan instructions from: {prompt_file}
Execute them completely. Read the referenced source files under the target repo
to CONFIRM each finding before reporting it. Report ONLY confidence:high bugs.
Write your results JSON to the exact path named in the instructions.
```

Wait for all subagents to return. Do NOT file issues or write reports yourself —
that is Step 4's job.

### Step 4: Collect (zero LLM tokens)

```bash
python3 $BOT_DIR/.claude/skills/discover-bugs/collect-findings.py --work-dir "$WORK_DIR" [--file-issues]
```

Pass `--file-issues` ONLY if the invocation included the `file-issues` argument
(or `auto file-issues` in cron once quality is proven). Otherwise it writes a
local report to `logs/discover-bugs-<stamp>.md` and files nothing.

Read the script's stderr summary and print it for cron logs.

---

## Modes

- **pod scan (cron default):** `/discover-bugs --pod <id> auto file-issues` —
  one pod per cron job (staggered 05:00–12:20 daily, see `sync-schedules.sh`).
  The gate `scripts/check-pod-candidates.sh <id>` skips the run when the pod has
  no work (full sweep done and still within the re-scan cooldown).
- **pod scan (manual, report-only):** `/discover-bugs --pod <id>` — omit
  `file-issues` to write a local prioritized report without filing.
- **legacy report-only:** `/discover-bugs` — repo-wide changed-file scan, local
  report only.
- **legacy file issues:** `/discover-bugs file-issues`.

To add/remove a pod, edit `pods.json`, run
`python3 prepare-scan.py --sync-doc` (regenerates `PODS.md`), and re-run
`scripts/sync-schedules.sh` (installs/updates that pod's cron).

## Cost (tokens)

The scanning subagents are ~all the cost; the orchestrator is negligible. Levers,
biggest first:

1. **Model tier — dominant.** Scan subagents default to **Haiku** (see Step 3).
   Haiku vs Opus is a ~10–20× cost cut; Sonnet ~5×. Never scan on Opus.
2. **Bounded runs.** `--max-files` (default 30) caps files per pod per run.
   ONE pod per cron, staggered. NEVER bulk-run all pods full-tree at once — that
   is thousands of subagents in one shot.
3. **`--files-per-chunk`** (default 5): larger chunks → fewer subagents → less
   repeated rubric/prompt overhead (each agent reads more, though).
4. **`--rescan-days`** (default 14): longer cooldown → the whole codebase is
   re-swept less often.

Rough scale: a single pod-run (30 files, ~6 Haiku subagents) is a few cents to a
few dollars. A full-tree sweep of every pod at once on Opus is hundreds of
dollars — do not do it. Keep a workspace spending cap on.

## Guardrails

- Report ONLY `confidence:high` findings (collect-findings enforces this).
- Dedup against the per-pod local seen-list AND open `ai-generated` issues —
  never re-file the same bug (dedup is why full-tree sweeps and re-scan cycles
  are safe to repeat).
- Never auto-fix. Never @-mention. Never merge.
- `--max-files` caps work per run; sweep progress is tracked by a cursor so
  nothing is silently dropped — it's scanned on a later run.
