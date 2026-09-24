# Run State Management

> With `bot.maxConcurrentRuns` above 1, each run slot has its own state
> file: slot 1 uses `data/run-state.json`, slot N uses
> `data/run-state.slot-N.json`. The operator settings on this page live in
> `data/run-state.json` and are copied into every slot at run start, so set
> them there. `scripts/reset-run-state.sh --slot N` resets one slot.
> See [Concurrent runs](./concurrent-runs.md).

## When run-state.json Gets Reset

The `run-state.json` file tracks which stories have been checked in the current run. A story is selected at most once per run unless it is named outright. It gets reset in these situations:

1. **Run start**: `run.sh` resets it, and the first iteration initializes `runId`
2. **Manual reset**: You can manually reset by setting `runId: null` and `storiesCheckedThisRun: []` in the file

Once every story is merged, skipped, invalid, or already checked, the run ends rather than starting over. `run.sh` counts the selectable stories at start and lowers the iteration count to match, so `./run.sh 30` against 10 selectable stories runs 10 iterations.

## Manual Reset Script

To manually start a fresh run (useful when you want to re-check all pushed PRs or start over):

```bash
$BOT_DIR/scripts/reset-run-state.sh
```

This resets the iteration state (`runId` and `storiesCheckedThisRun`) while **preserving** the configuration setting (`skipPushedTasks`). This allows all stories to be checked again without losing your configuration preferences.

## Prioritize Specific Tasks

To prioritize specific stories, pass the information as extra arguments to `run.sh`:

```bash
./run.sh 10 tui Prioritize US-012 first
./run.sh 10 tui Work on the socket test fix
```

Any text after `tui` (or after the iteration count if not using TUI mode) is passed as `--extra-prompt` to `scripts/select-task.py`. The script calls `claude --model haiku` with the candidate story list and your prompt to select the best match (e.g., "work on the socket test" → US-002). If the LLM response is unparseable, selection falls back to the deterministic tier/priority algorithm.

## Skip Pushed Tasks Mode

Set `skipPushedTasks: true` in `run-state.json` when you want to:
- Only work on new development (`status: "pending"`)
- Skip checking all `status: "pushed"` PRs (useful when you know reviewers haven't responded)
- Focus on implementing new features rather than monitoring reviews

**This setting is preserved across run resets** - it's a configuration preference, not iteration state.

To toggle this setting:
```bash
# Skip pushed tasks (focus on new development only)
jq '.skipPushedTasks = true' data/run-state.json > tmp.$$.json && mv tmp.$$.json data/run-state.json

# Resume checking pushed tasks (normal mode)
jq '.skipPushedTasks = false' data/run-state.json > tmp.$$.json && mv tmp.$$.json data/run-state.json
```
