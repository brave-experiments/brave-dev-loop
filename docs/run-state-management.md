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

This resets the iteration state (`runId` and `storiesCheckedThisRun`) while **preserving** configuration settings (`skipPushedTasks`, `enableMergeBackoff`, `mergeBackoffStoryIds`). This allows all stories to be checked again without losing your configuration preferences.

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

## Post-Merge Checking Configuration

Control whether the bot performs post-merge monitoring with exponential backoff using configuration in `run-state.json`:

**Configuration Fields (PRESERVED across run resets):**

```json
{
  "runId": "...",
  "storiesCheckedThisRun": [...],
  "skipPushedTasks": false,
  "enableMergeBackoff": true,
  "mergeBackoffStoryIds": null
}
```

**Fields:**
- `enableMergeBackoff` (boolean): Enable/disable post-merge monitoring for all merged stories
  - `true` (default): Bot will check merged PRs on exponential backoff schedule
  - `false`: Skip all post-merge checking, treat merged stories as final immediately
- `mergeBackoffStoryIds` (array of strings or null): Restrict post-merge checking to specific stories
  - `null` (default): Check all merged stories that need rechecking
  - `["US-012"]`: Only check this specific story, skip all other merged stories
  - `["US-012", "US-013"]`: Only check these specific stories, skip all others

**Important:** These configuration values are NOT reset when `runId` becomes `null` or when `storiesCheckedThisRun` is cleared. They persist across runs as configuration preferences.

**Usage Examples:**

```bash
# Disable all post-merge checking temporarily
jq '.enableMergeBackoff = false' data/run-state.json > tmp.$$.json && mv tmp.$$.json data/run-state.json

# Re-enable post-merge checking
jq '.enableMergeBackoff = true' data/run-state.json > tmp.$$.json && mv tmp.$$.json data/run-state.json

# Only check specific merged stories (useful for debugging)
jq '.mergeBackoffStoryIds = ["US-012"]' data/run-state.json > tmp.$$.json && mv tmp.$$.json data/run-state.json
jq '.mergeBackoffStoryIds = ["US-012", "US-013"]' data/run-state.json > tmp.$$.json && mv tmp.$$.json data/run-state.json

# Resume checking all merged stories
jq '.mergeBackoffStoryIds = null' data/run-state.json > tmp.$$.json && mv tmp.$$.json data/run-state.json
```

**During Task Selection:**

When selecting merged stories for post-merge checking, apply these filters:
1. If `enableMergeBackoff` is `false`, skip all post-merge checking entirely
2. If `mergeBackoffStoryIds` is an array (not `null`), only check stories whose IDs are in that array
3. Otherwise (if `mergeBackoffStoryIds` is `null`), check all merged stories that have `nextMergedCheck` in the past

This allows fine-grained control over post-merge monitoring without affecting the main workflow.
