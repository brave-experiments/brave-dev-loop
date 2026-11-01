# bravebot: Clustering related issues

Read when the prompt has a `Clustering:` paragraph. It appears only when this
profile's `clustering.sizeLimits` gives the lead story's `size/` label a ceiling
above 1 and the selector found candidates no larger than the lead. No paragraph
means a single story: ignore this file.

This adapts bravebot's own `/implement-issues` skill
(`agents/skills/implement-issues/SKILL.md` in the target repo) to one loop
iteration. Where this file is silent, that skill's rules apply, and so do
`docs/workflow-pending.md`, `docs/workflow-committed.md` and the other files in
this profile's `docs/`. Where they disagree, this file wins for a clustered
iteration, because the loop owns claims, statuses and the iteration boundary and
the skill does not know about them.

## The ceiling

| Lead's `size/` label | Most issues in the cluster, lead included |
|---|---|
| `size/1` | 10 |
| `size/2` | 6 |
| `size/3` | 4 |
| `size/4`, `size/5`, or none | 1 |

The ceiling is a maximum. A cluster of one is the common result and a good one.
A cluster is only larger when the issues are related, and padding it to the
ceiling is a failure: it makes a pull request nobody can review. The table lives
in the profile (`clustering.sizeLimits`) and is enforced by
`scripts/claim-companions.py`, which refuses a story past the ceiling whatever
you ask for. The selector offers only candidates whose `size/` is labelled and no
larger than the lead's, so the lead's size is the size of the biggest thing in
the cluster.

## Related

Issues are related when one of these holds, and only these:

1. one root cause;
2. one is a prerequisite of another;
3. the same function, file or spec clause changes in both;
4. their changes would conflict when combined.

The same area, the same crate, the same label or a similar title is not
relatedness. Similar-sounding issues with different causes are separate pull
requests. When unsure whether two are related, they are: a conflict between
pull requests costs more than a larger one. When unsure whether a candidate
belongs at all, it does not, and it stays in the queue for a later iteration.

## Steps

Do these in order, and stop at the first that leaves you with a cluster of one.

1. **Work out the lead's change first.** Read the lead issue with the research
   steps as usual, and find what it changes: the files, functions and spec
   clauses. Do this before reading any candidate.
2. **Screen the candidates cheaply.** For each candidate read the issue with
   `gh issue view <n> --repo $ISSUE_REPO` and no more. Discard it at once unless
   one of the four relations above holds against the lead's change. Do not open
   a candidate's code until it has passed this. A candidate that a closed or
   merged pull request already covers, or that has an open pull request, is
   dropped, and you say which in `progress.txt`.
3. **Claim the survivors, once.**

   ```sh
   $BOT_DIR/scripts/claim-companions.py --lead <lead-story-id> <story-id> <story-id> ...
   ```

   It prints `claimed`, `refused` (with the reason for each) and `limit`. Only
   what is in `claimed` is yours. The claim also puts `bot/in-progress` on each
   companion's issue and records `clusterCompanions` on the lead, so the
   companions' statuses follow the lead's and `run.sh` hands every claim back
   when the iteration ends. Call it once with the whole list; a second call is
   for a candidate you found related only after starting, and counts against the
   same ceiling.
4. **One worktree, one branch, one pull request.** The lead's worktree
   (`../bravebot-<lead-issue-number>`) and branch hold all of it. Make no second
   worktree for a companion: the claim is for the pull request, and the shared
   `.git` cannot check one branch out twice. If two claimed issues turn out to
   conflict in the same lines so that both cannot survive, keep the lead's
   behaviour, drop the companion from the pull request, and record it under
   Decisions in the body. Skip it with
   `update-prd-status.py skipped <story-id> --reason "..."` only if it is
   finished with; otherwise leave it pending, and say so in `progress.txt`.
5. **Commits follow issues, not rows.** One commit per issue, with its tests, the
   spec clauses it makes wrong and the catalogue entries. Several issues may share
   one commit where a single change fixes them. A further commit is only for a
   dependency or for a refactor that changes no behaviour and that the fix is
   built on. Every commit leaves the tree building. The commit that finishes an
   issue says `Closes #<n>`, one that finishes part of it `Part of #<n>`. Do not
   split by file, platform, call site or test.
6. **Reproduce each issue before changing anything for it,** and show it in the
   body. Run the reproduction on `upstream/main`. If it no longer fails, the issue
   is already fixed: add the test that pins it where none exists, and the pull
   request still closes it, saying so under `## The problem`.
7. **Gate once, on the tree that is pushed.** The profile's presubmit sequence
   runs once on the rebased branch, covering every issue in it: `make
   check-affected` as [testing.md](./testing.md) gives it, and the Docker gates
   only if an issue names another platform. Running it per issue is the
   costliest mistake available here. For each new or changed test, name the wrong
   implementation it rejects and say whether failure was demonstrated, as
   [testing.md](./testing.md#choosing-the-tests) asks, per issue.
8. **Verify each issue against its own words.** Before pushing, write a short
   checklist per issue (one row for each thing the issue asks for) and check the
   final tree against it, not against your memory of the work. An issue with an
   unmet row is `Part of`, never `Closes`.

## The pull request

The body follows [pr-descriptions.md](../../../docs/pr-descriptions.md) and
`scripts/check-pr-body.py`, with these changes for a cluster:

- **A `Closes $ISSUE_REPO#<n>` line for each issue, one per line, at the very
  top.** The lead is first. Use `Part of` for one the pull request only
  partly does, and then `Remaining:` names the piece.
- **`User impact:` is one line for the whole pull request.**
- **`## The problem`, `## Reproduce` and `## The fix` are per issue,** under a
  `**#<n> <short title>**` heading each, in the same order as the `Closes` lines.
  Each reproduction is that issue's own, complete from a cold start. The fix
  section for an issue that shares a root cause with another says so once and
  points at it rather than repeating it.
- **`## Decisions to confirm`,** above `## The problem`, lists anything a
  maintainer could reverse: a reading of an ambiguous issue, an issue dropped
  from the cluster, one issue's behaviour given up for another's, a new flag or
  setting, a permission widened. Each entry gives the issue, what was chosen, the
  alternative, why, and what reversing it takes. Omit the heading if there are
  none.
- **The title is one sentence naming what changes for a person,** no issue
  numbers, no prefix. If it needs "and" between unrelated things, the cluster is
  wrong.
- **Labels:** none are defined for this profile (`labels.pr` is empty). The
  `bot/in-progress` labels the claim put on the companions' issues come off when
  the iteration ends, with no action from you.

Open the pull request once, with the `committed` then `pushed` flow in
`docs/workflow-committed.md` for the **lead**:

```sh
python3 $BOT_DIR/scripts/update-prd-status.py committed <lead-story-id> --branch <branch>
python3 $BOT_DIR/scripts/update-prd-status.py pushed <lead-story-id> --pr-number <n>
```

`update-prd-status.py` moves every claimed companion with the lead, to the same
branch and the same PR number, and prints a line for each. Do not run it for a
companion. Check the output names each one; a companion it did not name was not
claimed, and its issue is not in the cluster. Afterwards the PR belongs to all
of them: the syncs and the review loop key on the PR number, so a merge retires
all the stories together and a review fix round works the one branch.

If the lead is skipped or invalid, its companions return to the queue as
pending. If you drop a companion mid-way and it is not finished with, leave it
pending and remove it from the body; its claim is released with the rest when
the iteration ends.

## What to write in progress.txt

The ordinary entry for the lead, plus one line per candidate: claimed and in the
pull request, claimed and dropped (why), or not claimed (why). A reviewer of the
loop reads that to learn whether the screening was too eager or too shy.
