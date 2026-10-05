---
name: review-prs
description: "Review PRs in the configured PR repository for best practices violations. Supports single PR (#12345), state filter (open/closed/all), and auto mode for cron. Triggers on: review prs, review recent prs, /review-prs, check prs for best practices."
argument-hint: "[days|page<N>|#<PR>] [open|closed|all] [auto] [reviewer-priority] [full]"
allowed-tools: Bash(gh pr diff:*)
---

# Review PRs for Best Practices

Scan recent open PRs in the configured PR repository for violations of documented best practices.

- **Interactive mode** (default): drafts comments and asks for user approval before posting.
- **Auto mode** (`auto` argument): posts all violations automatically without approval. Designed for cron/headless use.
- **`full`**: review every file in each PR. Without it, a PR the bot reviewed before is reviewed only in the files whose changes differ since then, and a PR where none differ (a rebase, a merge of the base branch) is not reviewed again.

**IMPORTANT:** This skill only reviews PRs against existing best practices. It must NEVER create, modify, or add new best practice rules or documentation during a review run.

---

## Architecture: File-Based Pipeline

The review pipeline minimizes LLM token usage by pushing all heavy data through files, not context:

1. **prepare-review.py** (zero LLM tokens) — fetches PRs, diffs, comments; works out which files changed since the last review; writes one detect prompt per rule chunk, plus one for bugs, to a temp work directory (more than one per check when a large diff is split into parts); outputs a tiny JSON pointer to the work dir
2. **Detect subagents** (`review-prs-detect`, Sonnet) — each reads its prompt from a file, checks the diff against its rules, and writes candidate findings to a JSON file. They never read source files, except the ones a project's review guidance names (below).
3. **select-candidates.py** (zero LLM tokens) — drops the candidates post-review.py would drop anyway (no rule link, a rule id that does not exist, duplicates, lines already commented on, everything past twice the per-PR cap) and writes one validate prompt per PR that has any left
4. **Validate subagents** (`review-prs-validate`, Opus) — one per PR; reads the candidates against the PR's source tree and writes the ones that hold up
5. **collect-results.py** (zero LLM tokens) — reads the validated results, feeds them to post-review.py which handles prioritization, dedup, posting, approval, cache updates and notifications

**Project review guidance.** A project profile (`projects/<profile>/profile.json`) may carry a `review` key, documented in [projects/README.md](../../../projects/README.md). Its `guidance` text is read by one extra detect subagent per PR (`chunk_id` `project`, launched like the others from `subagent_prompts`) and by the PR's validator. `prepare-review.py` and `select-candidates.py` add it to the prompts, so the main session does nothing differently. With `verdict`, `post-review.py` opens the review it posts with a recommendation. `collect-results.py` builds a collapsed section from the manifest (files read, rules compared, passes that ran, flagged versus kept) and `post-review.py` appends it under the recommendation. Above it the review says what the PR does, gives steps for trying it by hand when its description has none, and lists the parts of the required description shape (`docs/pr-descriptions.md`, checked with the helpers in `scripts/check-pr-body.py`) that it lacks. The first two come from one more subagent per PR, the manifest's `summary_prompt`; the last is computed without one. A profile without the key reviews as before.

**Which best-practice docs a PR is checked against.** Each doc in the target repo's `best-practices/` declares it in an `<!-- applicability: CONDITION -->` comment in its first 10 lines: `always`, a file-type condition (`has_cpp_files`, `has_frontend_files`, … — see `discover-best-practices.py`), or `paths:ui/,crates/ui-bridge/`, which runs the doc only when a changed file is under one of those directories and shows it only those files.

The main LLM session only orchestrates: run scripts, read a small manifest, launch subagents with tiny prompts, run the collector. It never sees diffs, rule text, or violation details.

---

## The Job

When invoked with `/review-prs [days|page<N>|#<PR>] [open|closed|all] [auto] [reviewer-priority] [full]`:

### Step 1: Prepare (zero LLM tokens)

Run the prepare script with all arguments (`auto` → `--auto`, `reviewer-priority` → `--reviewer-priority`, `full` → `--full`):

```bash
BOT_DIR="<absolute path to brave-dev-loop directory>"
python3 $BOT_DIR/.claude/skills/review-prs/prepare-review.py [days|page<N>|#<PR>] [open|closed|all] [--auto] [--reviewer-priority] [--full]
```

The script's stdout is a tiny JSON with `work_dir` and `manifest` paths. Progress and cost summary go to stderr.

Parse the stdout JSON to get `work_dir`.

### Step 2: Read manifest

Read the manifest file at `{work_dir}/manifest.json`. It contains:

- **`auto_mode`**: whether to post without approval
- **`bot_username`**: the bot's GitHub username
- **`pr_repo`**: the target PR repository
- **`target_repo_path`**: absolute path to the local target repo checkout
- **`fetch_summary`**: stats on how many PRs were fetched/filtered/skipped
- **`progress_lines`**: pre-formatted progress messages — print these to stdout for cron logs
- **`prs`**: array of PRs to review, each containing:
  - `number`, `title`, `headRefOid`, `author`, `hasApproval`
  - `files_reviewed` of `files_total`: how many of the PR's files this run reviews
  - `subagent_prompts`: array of entries with `prompt_file` and `results_file` paths (NOT prompt text)
  - `summary_prompt`: `null`, or one more entry with `prompt_file` and `results_file` for the subagent that describes the PR. Present only for a project whose profile sets `review.verdict`
- **`cached_prs`**: PRs not reviewed this run — already reviewed at this commit, or no file changed since the last review (handled by the prepare script — just log results)
- **`errors`**: per-PR errors encountered during preparation

Print the `progress_lines`. Log any errors.

For each cached PR, log:
- If `approved` is true: `APPROVE: [PR #N](url) (title) - all threads resolved, approved`
- If `thread_resolution.unresolved_bot_threads > 0`: `CACHED: [PR #N](url) (title) - N threads still unresolved`

If no PRs to review (empty `prs` array), skip to Step 6.

### Step 3: Launch the detect subagents

`prepare-review.py` already fetched each PR's head commit and created an isolated `git worktree` for it, which the validators read in Step 5. No checkout work needed here.

A worktree is a full checkout of the target repo, so a run needs tens of gigabytes. They are created under `/var/tmp/review-prs` — override with `REVIEW_PRS_WORK_DIR`, pointing it at a filesystem with room rather than a tmpfs. In `auto` mode a PR whose worktree could not be created is dropped from the run with a `worktree` entry in `errors`, because reviewing it would read the default branch while judging a diff from the PR head. Report those PRs as skipped.

For every PR in `prs`, for every entry in that PR's `subagent_prompts`, launch a subagent with `subagent_type: "review-prs-detect"` and this prompt:

```
Read your instructions from: {prompt_file}
```

If the `review-prs-detect` agent type is not available, use `subagent_type: "general-purpose"` with `model: "sonnet"` and the same prompt.

Every prompt file fits in one Read call. When a check's diff does not fit alongside its rules, `prepare-review.py` writes the diff into page files that each fit one read, lists them in the prompt (and in the entry's `read_files`), and gives each subagent at most four pages, splitting a larger diff into entries with `part` and `total_parts`. Each part is a separate entry, launched like any other. The subagent reads the pages itself; do not read them for it.

A PR's `summary_prompt`, when it is not `null`, gets a subagent in the same way, with the same `subagent_type`. It is not one of the detect prompts: `select-candidates.py` never reads it, and a PR whose summary subagent failed is still reviewed, with no description section.

**Launch ALL detect subagents across ALL PRs in a single message** so they run concurrently.

**CRITICAL: Launch ALL subagents — no exceptions.** The prepare script already filtered documents by file type. Every entry in `subagent_prompts` MUST get a subagent. Do NOT skip any.

Wait for all of them to return.

**CRITICAL: NEVER post reviews, comments, or approvals to GitHub yourself.** Do NOT use `gh api`, `gh pr review`, `gh pr comment`, or any GitHub API calls to post anything on any PR. All posting is handled exclusively by `collect-results.py` → `post-review.py` in Step 6. If you post reviews directly, it creates duplicates.

### Step 4: Select candidates (zero LLM tokens)

```bash
python3 $BOT_DIR/.claude/skills/review-prs/select-candidates.py --work-dir "$WORK_DIR"
```

Its stdout is a JSON object whose `validators` array has one entry per PR with candidates left, each with a `prompt_file`. A PR with none is clean and needs no validator.

### Step 5: Launch the validate subagents

For every entry in `validators`, launch a subagent with `subagent_type: "review-prs-validate"` and this prompt:

```
Read your instructions from: {prompt_file}
```

If the `review-prs-validate` agent type is not available, use `subagent_type: "general-purpose"` with `model: "opus"` and the same prompt.

Launch them all in a single message and wait for all of them to return. If `validators` is empty, skip to Step 6.

### Step 6: Collect and post (zero LLM tokens)

Run the collector script — it reads the validators' result files and runs post-review.py:

```bash
python3 $BOT_DIR/.claude/skills/review-prs/collect-results.py --work-dir "$WORK_DIR" [--auto]
```

Pass `--auto` if `auto_mode` is true.

The script handles everything: collecting violations from result files, prioritization/capping (5 per PR), rule link validation, deduplication, posting inline reviews, approval for clean PRs, cache updates, Signal notification, and the final summary block. A PR whose detect subagents all failed, or whose validator wrote nothing, is left out: nothing is posted and it is not cached, so the next run reviews it again.

For **interactive mode** (no `--auto`): before running collect-results.py, read each validator's results from `{work_dir}/pr_{number}/validated.json`, present each violation to the user for approval, write only approved violations back to that file, then run collect-results.py.

Read the script's stderr — it contains the summary. Print it for cron logs.

### Summary of what consumes LLM tokens

| Phase | Token cost | Who does it |
|-------|-----------|-------------|
| Fetch PRs, diffs, comments | **Zero** | `prepare-review.py` |
| Build subagent prompts (files) | **Zero** | `prepare-review.py`, `select-candidates.py` |
| Resolve threads, check approval | **Zero** | `prepare-review.py` |
| Read manifest, launch subagents | **~50 tokens per subagent** | Main LLM session (Sonnet on the schedules) |
| Rule-checking the diff | **Subagent tokens** | Detect subagents (Sonnet), one per rule chunk plus one for bugs |
| Checking candidates against the source | **Subagent tokens** | Validate subagents (Opus), one per PR with candidates |
| Collect results, post, approve | **Zero** | `collect-results.py` + `post-review.py` |

---

## PR Link Format

When displaying PR numbers, ALWAYS use a full markdown link: `[PR #<number>](https://github.com/$PR_REPO/pull/<number>)`. NEVER use bare `#<number>` — the TUI auto-links them against the wrong repository.

---

## Closed/Merged PR Workflow

When reviewing closed or merged PRs and a violation is found:

1. **Present the finding** to the user (draft comment + ask for approval)
2. **If approved**, try to post inline review comments. If the API fails, fall back to:
   ```bash
   gh pr comment --repo $PR_REPO {number} --body "[file:line] comment text"
   ```
3. **Create a follow-up issue** in `$PR_REPO` to track the fix:
   ```bash
   gh issue create --repo $PR_REPO --title "Fix: <brief description>" --body "Found during post-merge review of PR #<NUMBER>. <description>"
   ```
   Where the finding is something a person can look at, show it rather than
   describe it: paste the screen as it renders today in a fenced block. For a
   full-screen terminal program its output is not its screen, so capture a raw
   run and replay it with `scripts/terminal-screenshot.py` — see
   [pr-descriptions.md](../../../docs/pr-descriptions.md#showing-a-terminal-screen).
   Whoever fixes this has not seen the screen you found it on.
4. **Reference the new issue** back in the PR comment.
