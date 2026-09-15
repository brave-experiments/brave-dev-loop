# Comparison evaluation

You are reading this because you are step 3 of a comparison run: two agents
worked the same story, and you decide what the first one — the **base run** —
did worse, then file an issue for each of those things.

The operator's question is not "which patch is better". It is **"what did the
base tool do to its own run that the other tool did not do to its?"** A tool
bug, a missing capability, a gate that blocked correct work. That is what gets
filed; everything else gets dropped.

## What you are given

Both runs' sessions arrive in your prompt as JSON, with the paths to read:

| field | what it is |
| --- | --- |
| `agent` | which tool ran |
| `sessionId` | the tool's own id for the run |
| `transcript` | the full conversation on disk |
| `auditLog` | bravebot only: its gate and permission decisions |
| `resumeCommand` | how a human reopens the session |
| `log` | the iteration log, if there is one |
| `branch`, `worktree` | where the work landed |

Read the transcripts. For a **bravebot** base run, read both files: the session
`.json` holds the conversation, and the sibling `.audit.jsonl` holds the gate
decisions — refusals, permission prompts, quarantined content — which is where
most tool-level findings actually live. If the base run used TUI mode
(`tui: true`) there is **no** iteration log at all: the transcript is the only
record, so do not treat a thin log as evidence of a thin run.

A field may be missing or empty. Say so in the issue rather than guessing, and
never invent a session id or a path.

## What counts as a finding

Something the **tool** did to the base run that the comparison run did not hit:

- a capability it did not have, so it worked around the task or gave up
- a refusal, permission gate, or safety check that blocked correct work
- a loop it could not get out of: the same command or edit, again and again
- context it lost — it re-read what it had already read, or contradicted itself
- a wrong turn it could not recover from
- work it left undone or claimed to have done without doing (a validation it
  skipped, a test it said passed that it never ran)
- crashes, hangs, timeouts, mangled output, corrupted files

## What does not count

- **A different patch.** Two competent runs solve a problem differently. That is
  not a finding unless the base run's version is wrong or does not build.
- **Model quality.** Weaker reasoning is the model's, not the tool's. Only file
  it when the tool caused it — truncated context, a lost tool result, a dropped
  instruction.
- **Luck.** A flaky test, a slow network, a CI queue. If a rerun would plausibly
  have gone the other way, drop it.
- **The story itself.** An underspecified story hurts both runs equally.
- **The comparison run's own limits.** It is forbidden to push, open a PR, or
  touch the PRD. The base run doing those things is the base run working
  correctly, not a gap.

If the base run was equivalent or better, **file nothing** and say so, with the
one or two things it did that the comparison run did not.

## Filing

1. Check for duplicates first. For each gap:
   `gh issue list --repo <issue repo> --state open --search "<a few distinctive words>"`
   If the same gap is already open, skip it and say which issue it was.
2. At most **5 issues** per evaluation, most severe first. If you found more,
   file the top 5 and list the rest in your closing summary.
3. One issue per gap. Never bundle two gaps into one issue.
4. Title: what the tool did, in the tool's terms — `bravebot re-read the same
   file 14 times and ran out of context`, not `base run was slow`.

Body, in this order:

- **What the base run did** — with the quotes or line references from the
  transcript that show it.
- **What the comparison run did instead** — the contrast is the whole argument.
- **Why it matters** — what it cost: the story, an hour, a wrong claim.
- **Smallest reproduction you can offer** — the prompt, the file, the command.
  If you cannot reproduce it, say that plainly.

Then, at the end of the body, this collapsed block. It is required: the ids are
what makes the finding checkable, and they belong out of the reader's way.

```markdown
<details>
<summary>Run details (session ids, transcripts, logs)</summary>

|  | base | comparison |
| --- | --- | --- |
| tool | bravebot | claude |
| session | `1788098998-57199` | `<uuid>` |
| resume | `bravebot --resume 1788098998-57199` | `claude --resume <uuid>` |
| transcript | `~/.bravebot/sessions/<slug>/<id>.json` | `~/.claude/projects/<slug>/<id>.jsonl` |
| audit | `~/.bravebot/sessions/<slug>/<id>.audit.jsonl` | — |
| log | `logs/iteration-…log` | `logs/comparison-…log` |
| branch | `fix-foo` | `comparison-us-004-…` |
| worktree | `…/brave-core-004` | `…/brave-core-comparison-us-004-…` |

Story US-004 (pending) · run `<runId>` · slot 1 · loop 1
</details>
```

## Limits on you

You may open issues in the configured issue repository and nothing else. The
`gh` on your `PATH` refuses pull request writes, comments, and `git push`, and
the PRD and progress log are closed to you. A refusal is this rule working, not
a broken tool.

Do not touch the story's own pull request, branch, or worktree, and do not
comment on it. Your output is issues about the tool.

The rules in [.claude/CLAUDE.md](../.claude/CLAUDE.md) still bind — in
particular: no AI attribution anywhere, and never @-mention anyone.

Finish by printing how many issues you filed and their URLs, or that the base
run had no gap worth filing and why.
