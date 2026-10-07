---
name: logs-analysis
description: "Analyze session logs in ~/.brave-dev-loop/sessions and cron logs in ./logs for errors, schedule gaps, and problems. Checks if cron jobs (run.sh, review-prs, add-backlog, learnable-pattern-search, check-signal) are running correctly. Triggers on: analyze logs, check logs, logs analysis, log report."
---

# Logs Analysis

Analyze session logs in `~/.brave-dev-loop/sessions/` (**never** look in `~/.brave-dev-loop/processed/`) and cron logs in `./logs/*-cron.log` to identify problems and assess schedule health. Each `run.sh` is one session directory holding `iteration-loop-K.log` files, a `housekeeping.log`, and a `stories.txt` naming the story each loop worked. `$BRAVE_DEV_LOOP_HOME` replaces `~/.brave-dev-loop` when set.

**Determine the bot directory:** This SKILL.md file lives inside the bot's `.claude/skills/logs-analysis/` directory. Use this file's path to derive the absolute path to the bot directory (referred to as `$BOT_DIR` below).

---

## Step 1: Gather Log Inventory

List all `.log` files in the session directories:

```bash
find "${BRAVE_DEV_LOOP_HOME:-$HOME/.brave-dev-loop}/sessions" -mindepth 2 -maxdepth 2 -name '*.log' -type f | sort
```

Also list the cron logs:

```bash
ls -la $BOT_DIR/logs/*-cron.log 2>/dev/null
```

If there are **no session logs**, report "No unprocessed logs found" and stop.

---

## Step 2: Analyze Logs Using Subagents

To handle large numbers of logs efficiently, **batch log files into groups of up to 10** and spawn one subagent per batch. Run all subagents in parallel.

For each batch, launch an Agent (subagent_type: "general-purpose") with a prompt like:

> Analyze the following iteration log files for problems. For each log file:
> 1. Read the file
> 2. Check for errors: "command not found", API errors (500, connection refused, socket errors), permission denied, fatal errors, crash/panic, "integer expression expected", unexpected exits
> 3. Check what story was worked on and whether it completed successfully or failed
> 4. Note the start/end timestamps if available
> 5. Note the file size (very small files like <100 bytes usually indicate a problem like "command not found")
>
> Return a structured summary for each file:
> - Filename
> - Story ID worked on (or "unknown")
> - Status: SUCCESS / FAILURE / EMPTY (for tiny files)
> - Errors found (list each error with context)
> - Duration if determinable
>
> Files to analyze: [list of file paths]

Also spawn a separate subagent for cron log analysis:

> Analyze the cron log files for problems. For each file:
> 1. Read the tail (last 200 lines) of the file
> 2. Check for errors, failures, and the last successful run timestamp
> 3. Check for schedule gaps (compare timestamps of runs against expected cron schedule)
>
> Expected cron schedule:
> - run-cron.log: runs at 04:00 and 13:00 daily (EST, which is UTC-5)
> - review-prs-cron.log: runs at 05:00, 08:00, 10:00, 12:00, 14:00, 16:00, 18:00, 20:00 daily (EST)
> - add-backlog-cron.log: runs at 03:00 and 11:00 daily (EST)
> - learnable-pattern-search-cron.log: runs at 06:00 daily (EST)
> - check-signal-cron.log: runs every hour at :05 (EST)
>
> Report:
> - Last run timestamp for each cron job
> - Whether each schedule appears to be running on time
> - Any errors or anomalies in the cron logs
> - Whether there are gaps (missed runs)
>
> Cron log files: [list of cron log paths]

---

## Step 3: Compile Report

After all subagents return, compile a unified report with these sections:

### Schedule Health
- For each cron job: last run time, whether it's on schedule, any missed runs
- Flag any job that hasn't run in over 24 hours as a problem

### Iteration Log Summary
- Total logs analyzed
- Success/failure/empty counts
- Table of each log with: filename, story, status, key errors

### Problems Found
- Group all errors by type (command not found, API errors, permission errors, etc.)
- Highlight recurring patterns
- Flag critical issues that need immediate attention

### Recommendations
- Actionable suggestions based on the problems found

---

## Step 4: Ask to Archive

After presenting the report, ask the user:

> Would you like to move the analyzed session directories to `~/.brave-dev-loop/processed/`?

If the user agrees, move each analyzed session directory into `processed/`:

```bash
H="${BRAVE_DEV_LOOP_HOME:-$HOME/.brave-dev-loop}"; mkdir -p "$H/processed"; mv "$H/sessions/"<session-id> "$H/processed/"
```

Skip a session whose run is still going (`./run.sh --status` lists the live ones).

---

## Important Rules

- **NEVER** read or analyze files in `processed/` — those have already been reviewed
- **NEVER** delete log files — only move them to `processed/`
- Keep the report concise but include enough detail to diagnose issues
- If a log file is very large (>1MB), instruct subagents to focus on the first 100 and last 100 lines
- For stream-json format logs, errors often appear in `"type":"error"` or `"type":"result"` entries — grep for those patterns
