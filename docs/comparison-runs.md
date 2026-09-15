# Comparison runs

A comparison run answers a question the loop cannot otherwise answer: did the
tool that just worked this story do the job as well as another tool would have?

With `--comparison-run`, one iteration becomes three steps in the same turn:

1. **Base run** — the story is worked exactly as it always is, by the agent
   `--agent` selects. Nothing about it changes.
2. **Comparison run** — the same story is worked again from scratch, by a second
   tool, in a worktree of its own. It does the whole job and stops dead before
   anything public: no push, no PR, no issue, no PRD or progress-log write. It
   hands its session id back to `run.sh`.
3. **Evaluator run** — a third agent reads both sessions, treats the comparison
   run as the gold standard, and files an issue for each gap that is the base
   tool's fault. Its rules are in
   [comparison-evaluation.md](comparison-evaluation.md).

The output is GitHub issues in `project.issueRepository` describing what the base
tool did worse. That is the point: tool bugs and missing capability found by
comparison, instead of by reading transcripts by hand.

## Running one

```bash
./run.sh 1 tui --agent bravebot --agent-bin ~/projects/brave/bravebot/target/debug/bravebot \
  --comparison-run
```

Off unless asked for. Without `--comparison-run` nothing here happens, and no
config key turns it on.

| Flag | Default |
| --- | --- |
| `--comparison-run` | off |
| `--comparison-agent <name>` | `claude` |
| `--comparison-agent-bin <path>` | that agent's configured binary |
| `--comparison-model <name>` | that agent's configured model |
| `--comparison-branch <name>` | `comparison-<story-id>-<epoch>` |

The comparison agent defaults to `claude` whatever the base agent is — the whole
value is a second opinion from a different tool — and the evaluator uses that
same agent, binary and model: the gold-standard tool is the one that judges.

Passing any `--comparison-*` flag without `--comparison-run` is an error rather
than a silent no-op.

`--comparison-branch` is for a single deliberate run. Leave it off for a loop of
several iterations, or the second iteration refuses: the branch already exists.

## Cost

The story is done twice, then read once. Expect roughly three times the model
spend and wall-clock time of a normal iteration, and note that the comparison
runs against the *same* story the base run just did — so with `--comparison-run`
set, keep the iteration count low.

## What the comparison run cannot do

The prompt tells it where to stop, and `scripts/comparison-guard/` makes it true.
That directory goes on `PATH` in front of the real tools for steps 2 and 3:

- `gh` refuses pull request writes, issue writes, comments, releases, repo and
  workflow mutation, secrets, and any `gh api` call that is not a GET. Reads pass
  through untouched.
- `git` refuses `push`. Everything else, `commit` included, passes — the commit
  in the worktree is what the evaluator reads.
- `scripts/update-prd-status.py` and `scripts/append-progress.sh` refuse while
  `BOT_COMPARISON_RUN` is set.

The evaluator run additionally gets `BOT_COMPARISON_GUARD_ALLOW=issue-create`,
which allows `gh issue create` and nothing more: it has to file its findings.

A refusal prints a line naming this document. It is the rule working, not a
broken tool.

## Artifacts

| | |
| --- | --- |
| Comparison log | `logs/comparison-<runId>-slot-N-loop-M.log` |
| Evaluator log | `logs/evaluator-<runId>-slot-N-loop-M.log` |
| Comparison worktree | `<target repo>-<comparison branch>` |
| Comparison branch | local only — never pushed |

`logs/` is gitignored. The worktree and its branch are **left in place on
purpose**: inspecting the gold-standard attempt is most of the value. Remove one
when you are done with it:

```bash
git -C <target repo> worktree remove <path>
git -C <target repo> branch -D <comparison branch>
```

Sessions are read from each tool's own store —
`~/.bravebot/sessions/<dir-slug>/<id>.json` plus its `<id>.audit.jsonl`,
`~/.claude/projects/<dir-slug>/<id>.jsonl`, `~/.codex/sessions/…` —  by
`scripts/find-agent-session.py`, which prints the paths and the resume command as
JSON. This matters most for `tui` runs: `run.sh` writes no iteration log then, so
the tool's own session store is the only record of what happened. `cursor` is not
supported there; a cursor base run is evaluated from its iteration log alone.

## When it fails

A comparison failure never fails the iteration or the loop. If the worktree
cannot be created, or the comparison agent dies, `run.sh` prints the reason and
moves on to the next iteration — the base run's own result stands.
