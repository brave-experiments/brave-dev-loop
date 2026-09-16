# Run State Management

> With `bot.maxConcurrentRuns` above 1, each run slot has its own state
> file: slot 1 uses `data/run-state.json`, slot N uses
> `data/run-state.slot-N.json`. The operator settings on this page live in
> `data/run-state.json` and are copied into every slot at run start, so set
> them there. `scripts/reset-run-state.sh --slot N` resets one slot.
> See [Concurrent runs](./concurrent-runs.md).

## When run-state.json Gets Reset

The `run-state.json` file tracks which stories have been checked in the current run. It gets reset automatically in these situations:

1. **First iteration ever**: When `runId` is `null`, a new run is initialized
2. **All stories processed**: When all remaining stories are either merged, skipped, or already checked in `storiesCheckedThisRun`, the run state resets automatically
3. **Manual reset**: You can manually reset by setting `runId: null` and `storiesCheckedThisRun: []` in the file

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
