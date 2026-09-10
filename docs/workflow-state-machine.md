# Workflow State Machine

## Valid Status Values (ONLY these are allowed)

**CRITICAL: The `status` field in prd.json MUST be one of these exact values. Using ANY other value (e.g., "pr_created", "in_review", "ready", etc.) is INVALID and will break the workflow.**

| Status | Type | Description |
|--------|------|-------------|
| `"pending"` | Active | Development in progress |
| `"committed"` | Active | Code committed locally, ready to push and create PR |
| `"pushed"` | Active | PR created and public, awaiting review/merge |
| `"merged"` | Terminal | PR merged successfully |
| `"skipped"` | Terminal | Intentionally skipped (e.g., duplicate PR exists) |
| `"invalid"` | Terminal | Invalid story, won't be worked on |

**Valid transitions:** `pending` → `committed` → `pushed` → `merged`. Any status can also transition to `skipped` or `invalid`. The `pushed` state loops back to itself when responding to review feedback.

**NEVER invent new status values.** If a transition doesn't fit one of these statuses, you are misunderstanding the workflow.

## Your Task - State Machine Workflow

**CRITICAL: One Story Per Iteration**

The story to work on is **pre-selected by `scripts/select-task.py`** and provided in the prompt. You do NOT need to do task selection — just execute the workflow for the assigned story.

Each iteration:
1. Execute the workflow for the assigned story's status
2. Update the PRD and progress.txt
3. **END THE ITERATION** - Stop processing

## Handling Duplicate Test Fix Stories

**CRITICAL RULE: Never skip a test fix story as "duplicate" if the GitHub issue is still open**

When multiple stories exist for the SAME test (same `testFilter` value):

### Decision Logic - Default to NOT skipping

**If the GitHub issue is still OPEN → DO NOT SKIP (assume re-occurrence)**

An open issue means the test is still failing. Treat subsequent stories as NEW fix attempts, not duplicates.

### When to Skip (requires ALL conditions)

Only skip a test fix story as "duplicate" if ALL of these are verified:

1. **Previous fix is merged** (not just open/under review)
2. **GitHub issue was created BEFORE the previous PR merged**
   - Compare timestamps: `issue.createdAt < pr.mergedAt`
   - If issue was created AFTER the merge, this is a RE-OCCURRENCE → work on it
3. **No new failures reported since the merge**
   - Check issue comments for reports of continued failures
   - Check CI/build logs if available
4. **Version/environment matches** (if specified in the issue)
   - If the issue mentions a specific upstream version, verify the fix covers that version

### Timestamp Verification Commands

```bash
# Get issue creation date
gh issue view <issue-number> --repo $ISSUE_REPO --json createdAt

# Get PR merge date
gh pr view <pr-number> --repo $PR_REPO --json mergedAt

# Compare: If issue.createdAt > pr.mergedAt → RE-OCCURRENCE (work on it)
```

### Example

US-016 and US-023 both fix `BraveSearchTestEnabled.DefaultAPIVisibleKnownHost`:
- US-023 has PR #33596 merged on 2024-01-15
- US-016's issue was created on 2024-01-20 (AFTER the merge)
- This is a **RE-OCCURRENCE** → US-016 should be worked on as a NEW fix attempt
- **Do NOT skip US-016** - the previous fix did not address all failure modes

### Important Notes

- **When in doubt, DO NOT SKIP** - intermittent tests often have multiple root causes
- Each fix story may address different race conditions or timing issues
- A merged PR does NOT guarantee the intermittent test is fixed
- Only skip after VERIFICATION using timestamp data that proves it's a true duplicate
- If timestamps are unavailable or ambiguous, proceed with implementation

## State Change Tracking

The `lastIterationHadStateChange` field in run-state.json controls whether the work iteration counter increments. This keeps the iteration count meaningful (representing actual work done) while allowing rapid checking of multiple stories without inflating the count.

- **Set to `true`** when a story's status changes (pending→committed, committed→pushed, pushed→merged, or review response with push)
- **Set to `false`** when checking a story but making no changes (test failures, waiting for reviewer, blocked states)

This allows the system to check multiple pushed PRs rapidly without incrementing the work iteration counter for each one, while still using a fresh context for each check.

## Execute Workflow Based on Status

Execute the workflow based on the story's current status:
   - **"pending"**: See [workflow-pending.md](./workflow-pending.md)
   - **"committed"**: See [workflow-committed.md](./workflow-committed.md)
   - **"pushed"**: See [workflow-pushed.md](./workflow-pushed.md)
   - **"merged"**: See [workflow-merged.md](./workflow-merged.md)
   - **"skipped"** or **"invalid"**: See [workflow-skipped-invalid.md](./workflow-skipped-invalid.md)

## Task Selection (Handled by Script)

Task selection is handled deterministically by `scripts/select-task.py` — the selected story is provided in the prompt. See [run-state-management.md](./run-state-management.md) for configuration options (skip pushed tasks, merge backoff, etc.).

## Backlog Order: the Three Triage Axes

Inside its tier, pending work is ordered by the axes the issue was labelled with
rather than by the order the issues happened to arrive in. A backlog of a
hundred issues that all sort the same can only be read one issue at a time.

| Axis | Range | The question it answers |
|---|---|---|
| `importance` | 1 to 5, 1 highest | how much it matters that this is fixed at all |
| `urgency` | 1 to 5, 1 highest | how soon it has to happen |
| `size` | 1 to 5, 1 smallest | how much work it is |

`scripts/add-backlog-to-prd.py` reads them off an issue's labels into the story's
`triage` block, and brings a still-pending story's block back into line with its
issue on every sync — so raising an urgency reaches the next run instead of only
the issues nobody has filed yet. `scripts/select-task.py` sorts on it, with
`priority` still breaking whatever ties are left.

**Which labels spell an axis is project-specific.** The profile's `labels.axes`
maps each axis to its label prefix, and the project's own docs define what its
values mean — see [Project profiles](../projects/README.md), and the profile's
`docs/labels.md` where it has one. A project that defines no prefixes has no
axes: its stories carry no `triage` block and its backlog is ordered by
`priority` exactly as it was before the axes existed.

Three rules hold whatever the labels are called:

- **Urgency interrupts importance, so it is read first.** Collapsing the two
  into one number loses the work in the middle. A hole in a guarantee that
  nothing currently reaches has to be fixed and costs nothing to wait; a parsing
  slip that eats a typed line is small and costs somebody something every day.
- **A missing axis is not a low value.** It means nobody has judged the issue
  yet, so it sorts as the middle of the range — an unjudged issue should neither
  jump the queue nor be buried under it.
- **Size never orders anything.** It says what fits in the time available.
  Sorting by it would bury exactly the large, important work that needs
  splitting, so it is recorded, reported, and left out of the key.

The axes order the backlog; they do not overrule the tiers. A reviewer waiting
on an answer (URGENT) still comes before the most urgent pending story, and a
story retried `MAX_PENDING_ATTEMPTS` times is still quarantined however it is
labelled.

## Error Handling

**GitHub CLI (gh) Failures:**
- If any `gh` command fails, log the error and abort immediately
- Do NOT attempt workarounds or continue without the gh operation
- Document the failure in $BOT_DIR/data/progress.txt (story remains at current status)
