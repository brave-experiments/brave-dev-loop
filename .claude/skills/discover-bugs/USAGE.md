# discover-bugs — usage cheat-sheet

Static bug scanner for the target repo (**brave-core**, **master**). Per-product/pod,
verify-first, prioritized (P0/P1/P2), filed as `ai-generated` GitHub issues for a
human to confirm. Findings are **never auto-fixed**.

## Pipeline (4 steps, same under every run method)

```
1 prepare-scan.py     0 tokens   pick files -> write chunk prompts -> manifest.json
2 read manifest       -          parse chunks; if empty -> skip to step 4
3 subagents (Haiku)   tokens     one subagent per chunk, concurrent -> results JSON
4 collect-findings.py 0 tokens   gate -> dedup -> prioritize -> report OR file issues -> advance cache
```

Only step 3 costs tokens. Steps 1 and 4 are deterministic Python (git/glob/gh).

## Two switches that matter

1. **report-only vs file issues** — `file-issues` word (skill) / `--file-issues` (script).
   Omit = local report only (safe). Present = posts real issues to `brave/brave-browser`.
2. **scan size per run** — `--max-files` (default 30/pod, cyclic). Default is cheap;
   cranking it up to sweep a whole pod at once is what makes a run expensive.

---

## Way 1 — skill (interactive, recommended)

```
/discover-bugs [--pod <id> | --since-cache | --commits N | --days N] [auto] [file-issues]
```

| word | effect |
|---|---|
| `--pod <id>` | pod mode (primary) — scans one pod's whole tree, cyclically |
| `--since-cache` | legacy: diff since last scan (default when no scope given) |
| `--commits N` / `--days N` | legacy: files changed in last N commits / days |
| `auto` | non-interactive (cron) |
| `file-issues` | file GitHub issues (default: report-only) |

Cases:
```
/discover-bugs --pod wayback-machine                 # pod, report-only
/discover-bugs --pod wayback-machine file-issues     # pod, files issues (outward-facing)
/discover-bugs --pod ai-chat auto file-issues        # exact cron form
/discover-bugs                                       # legacy repo-wide, report-only
/discover-bugs --days 7 file-issues                  # legacy last-7-days, files issues
```

Report lands in `logs/discover-bugs-<pod>-<stamp>.md`. `--pod` and the legacy scope
words are mutually exclusive.

Valid pod ids (22): `ai-chat`, `wallet-ethereum`, `wallet-solana`, `wallet-bitcoin`,
`wallet-zcash`, `wallet-other-chains`, `wallet-keyring`, `wallet-core`, `shields`,
`adblock`, `fingerprinting`, `sync`, `vpn`, `talk`, `talk-premium`, `news`, `sidebar`,
`speedreader`, `tor`, `wayback-machine`, `web-discovery`, `email-aliases`.

---

## Way 2 — cron (scheduled, hands-off)

```bash
bash scripts/sync-schedules.sh   # install/refresh one cron per pod from pods.json
crontab -l                       # inspect
```

Each cron line = gate -> git sync -> `claude -p '/discover-bugs --pod <pod> auto file-issues'`
under a per-pod lock, staggered 05:00-12:20 daily.

- `check-pod-candidates.sh <pod>` gates the run (`prepare-scan.py --pod <pod> --check`);
  exits 1 -> line stops -> **no tokens when a pod has no work** (sweep done + in cooldown).
- Change cadence: `rescan_interval_days` in `pods.json`.
- Add/remove a pod: edit `pods.json`, run `sync-schedules.sh` (and `--sync-doc` for PODS.md).

---

## Way 3 — raw scripts (testing / full control)

**Step 1 — prepare (0 tokens, writes to a temp dir):**
```bash
python3 .claude/skills/discover-bugs/prepare-scan.py --pod <id> [flags]
```

| flag | default | effect |
|---|---|---|
| `--pod <id>` | — | pod mode |
| `--check` | off | gate only: print yes/no, exit 0/1, build nothing |
| `--sync-doc` | off | regenerate PODS.md from pods.json and exit |
| `--max-files N` | 30 | hard cap/run (excess picked up next run via cursor) |
| `--files-per-chunk N` | 5 | files per subagent (larger = fewer agents = cheaper) |
| `--rescan-days N` | 14 | cooldown after a full sweep before re-sweeping |
| `--auto` | off | non-interactive marker |
| `--since-cache`/`--commits N`/`--days N` | — | legacy scope (no `--pod`) |

Output: `{"work_dir": "...", "manifest": "..."}`.

**Step 3 — launch a Haiku subagent per `chunks[].prompt_file`** (from `manifest.json`);
each reads its prompt, confirms against source, writes its `results_file`.

**Step 4 — collect:**
```bash
python3 .claude/skills/discover-bugs/collect-findings.py --work-dir "$WORK_DIR" [--file-issues]
```

| flag | default | effect |
|---|---|---|
| `--work-dir <path>` | required | temp dir from step 1 |
| `--file-issues` | off | create GitHub issues (default: local report only) |

Zero-cost checks:
```bash
python3 .claude/skills/discover-bugs/prepare-scan.py --pod wayback-machine --check
python3 .claude/skills/discover-bugs/prepare-scan.py --pod wayback-machine --max-files 2 --files-per-chunk 1
```

---

## Scan model (per pod)

Full-codebase, cyclic: scans the pod's **entire tree** on master as full source
(never diffs), bounded per run (`--max-files`) and rotating via a cursor in
`.ignore/discover-bugs/<pod>.json` until the whole tree is covered; then idles for
`--rescan-days` and re-sweeps. To force a re-scan now: delete that pod's cache file.

## Cautions

- `file-issues` is **outward-facing** — real issues on `brave/brave-browser`
  (`project.issueRepository`). Test against a throwaway repo first; needs `gh` authed.
- Scan subagents run on **Haiku** (see SKILL.md step 3) — pennies per pod-chunk.
  Never bulk-run all pods full-tree on Opus.
- Dedup (seen-list + open `ai-generated` titles) makes re-runs and re-scan cycles safe.
- Never auto-fix, never @-mention, never merge.
