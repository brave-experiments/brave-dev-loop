# State Machine - Story Lifecycle

This document describes the complete state machine for user story progression through the bot's workflow.

## Valid Status Values (ONLY these are allowed)

**CRITICAL: The `status` field in prd.json MUST be one of these exact string values. Using ANY other value is INVALID.**

- `"pending"` — Development in progress
- `"committed"` — Code committed locally, ready to push and create PR
- `"pushed"` — PR created and public, awaiting review/merge
- `"merged"` — PR merged successfully (terminal)
- `"skipped"` — Intentionally skipped (terminal)
- `"invalid"` — Invalid story (terminal)

**NEVER invent new status values** (e.g., "pr_created", "in_review", "ready", "done", etc.). If a transition doesn't fit one of these 6 statuses, you are misunderstanding the workflow.

## State Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         Story Lifecycle                          │
└─────────────────────────────────────────────────────────────────┘

     START
       │
       ▼
  ┌─────────┐
  │ pending │
  └─────────┘
       │
       │ Implement changes
       │ Run ALL acceptance criteria tests
       │ All tests MUST pass
       ▼
  ┌───────────┐
  │ committed │ (Code ready, not yet public)
  └───────────┘
       │
       │ Push branch
       │ Create PR
       ▼
  ┌──────────────────────────────────────────────────────────────┐
  │                          pushed                              │
  │                                                              │
  │  lastActivityBy: null (fresh) / "bot" (waiting) /          │
  │                  "reviewer" (needs response)                │
  │                                                              │
  │  ┌────────────────────────────────────────────────┐        │
  │  │  Every Iteration Check (ALWAYS):                │        │
  │  │  1. Is PR mergeable? (approved + CI + no issues)│        │
  │  │     YES → Merge and go to "merged" ─────────────┼───┐    │
  │  │     NO → Continue to step 2                     │   │    │
  │  │                                                  │   │    │
  │  │  2. Check for new review comments                │   │    │
  │  │     - Compare timestamps: last review vs push    │   │    │
  │  │     - Determine who went last                    │   │    │
  │  │                                                  │   │    │
  │  │  3. Take action:                                │   │    │
  │  │     a) New review comments?                     │   │    │
  │  │        ┌─────────────────────────────────┐      │   │    │
  │  │        │  IMPLEMENTATION SUB-CYCLE:       │      │   │    │
  │  │        │  1. Read & understand feedback   │      │   │    │
  │  │        │  2. Implement changes            │      │   │    │
  │  │        │  3. Run ALL acceptance criteria  │      │   │    │
  │  │        │  4. All tests MUST pass          │      │   │    │
  │  │        │  5. Commit changes               │      │   │    │
  │  │        │  6. Push to same branch          │      │   │    │
  │  │        └─────────────────────────────────┘      │   │    │
  │  │        Set lastActivityBy="bot"                  │   │    │
  │  │        Stay in "pushed" state ◄──────────────────┘   │    │
  │  │                                                      │    │
  │  │     b) No new comments + lastActivityBy="bot"       │    │
  │  │        → Skip to next story (lower priority)        │    │
  │  │        → Will check again next iteration            │    │
  │  └────────────────────────────────────────────────────┘    │
  │                                                              │
  └──────────────────────────────────────────────────────────────┘
       │
       ▼
  ┌─────────┐
  │ merged  │ (Terminal state)
  └─────────┘
       │
       ▼
      END
```

## States

### 1. pending
**Description:** Story is ready for development work.

**Entry Conditions:**
- Story newly created
- Or story needs to be re-implemented from scratch

**Actions:**
- Implement the feature
- Run ALL acceptance criteria tests
- Commit changes locally

**Exit Conditions:**
- All tests pass → Move to `committed`

**Can Get Stuck?** ❌ No - Always has a path forward (implement the story)

---

### 2. committed
**Description:** Code is committed locally, needs to be pushed and PR created.

**Entry Conditions:**
- Coming from `pending` with all tests passing
- Code committed to local git branch

**Actions:**
- Push branch to remote
- Create pull request using `gh pr create --draft`
- Store PR number in `prNumber` field

**Exit Conditions:**
- PR created → Move to `pushed` with `lastActivityBy: "bot"`

**Can Get Stuck?** ❌ No - Always has a clear action (create PR)

---

### 3. pushed
**Description:** PR exists and is awaiting review, feedback, or merge.

**Sub-states (via `lastActivityBy` field):**
- `null` - Fresh PR, no activity yet
- `"bot"` - Bot pushed changes, waiting for reviewer
- `"reviewer"` - Reviewer commented, bot should respond

**Entry Conditions:**
- Coming from `committed` after PR creation
- Staying in this state after responding to reviews

**Actions (checked EVERY iteration):**

**Priority 1: Check Merge Readiness**
- ✅ Has required approvals
- ✅ CI checks passing
- ✅ No unresolved comments
- ✅ Mergeable state = true
- If ALL true → Merge PR and move to `merged`

**Priority 2: Check for Review Comments**
- Fetch PR reviews (filtered to the configured org only)
- Compare timestamps: last review vs last bot push
- Determine who went last

**Priority 3: Take Action Based on Who Went Last**

- **If reviewer went last (new comments to address):**

  Enter **Implementation Sub-Cycle** (same rigor as initial development):

  1. **Read & Understand**: Parse review feedback from org members
  2. **Implement**: Make the requested changes to the code
  3. **Test**: Re-run ALL acceptance criteria tests from original story
  4. **Validate**: ALL tests MUST pass (same requirement as `pending → committed`)
  5. **Commit**: Create new commit with changes
  6. **Push**: Push to same branch (updates existing PR)
  7. **Update State**: Set `lastActivityBy: "bot"`
  8. **Stay**: Remain in `pushed` state (will check merge status next iteration)

  **CRITICAL**: Responding to reviews requires the SAME quality gates as initial development. All original acceptance criteria must still pass.

- **If bot went last (no new comments):**
  - No new comments since our last push
  - Skip to next story (lower priority)
  - Will check merge status again next iteration
  - Bot works on other stories instead of waiting

**Exit Conditions:**
- PR approved and mergeable → Move to `merged`
- Reviewer comments → Respond and stay in `pushed`
- Waiting for reviewer → Skip but check again next iteration

**Can Get Stuck?** ❌ No - Merge readiness checked every iteration, even when `lastActivityBy: "bot"`

**Anti-Stuck Mechanism:** Even if reviewer never comments again, the bot checks merge status on every iteration. If the PR is approved (with no comments), it will be detected and merged automatically.

---

### 4. merged
**Description:** PR is merged, story is complete.

**Entry Conditions:**
- Coming from `pushed` when PR is successfully merged

**Actions:**
- None (terminal state)

**Exit Conditions:**
- None (terminal state)

**Can Get Stuck?** ❌ No - Terminal state, story is complete

---

### 5. skipped
**Description:** Story intentionally skipped (e.g., duplicate PR already exists for this issue).

**Entry Conditions:**
- An existing PR is found that addresses the same issue
- Story determined to be a true duplicate after verification

**Actions:**
- None (terminal state)

**Exit Conditions:**
- None (terminal state)

---

### 6. invalid
**Description:** Story is invalid and won't be worked on (e.g., PR was closed without merging, or reviewer indicated work is no longer needed).

**Entry Conditions:**
- PR was closed externally without merging
- Reviewer indicated the task is already completed elsewhere

**Actions:**
- None (terminal state)

**Exit Conditions:**
- None (terminal state)

---

## Task Selection Priority

When the bot picks the next story to work on:

1. **URGENT (Highest Priority)** - `status: "pushed"` AND `lastActivityBy: "reviewer"`
   - **Reviewer is waiting for us** - respond immediately
   - Human is blocked on our response
   - Enter full implementation sub-cycle to address feedback
   - **Never delay this** - reviewer time is most valuable

2. **HIGH Priority** - `status: "pushed"` AND `lastActivityBy: "bot"`
   - Check if reviewer responded (would escalate to URGENT)
   - Check merge readiness (might auto-merge)
   - Ensures we don't miss new feedback
   - Prevents approved PRs from getting stuck

3. **MEDIUM Priority** - `status: "committed"`
   - Code is ready, needs to be made public
   - Create PR so reviewers can start looking
   - Lower than review responses but higher than new work

4. **NORMAL Priority** - `status: "pending"`
   - New development work
   - Only start new work when no reviews pending
   - Parallelizes well with waiting for reviews

5. **SKIP** - `status: "merged"`
   - Story complete

**Story Priority Field**: Within each status priority level above, stories are ordered by their `priority` field from prd.json. **Lower numbers = higher priority**: priority 1 is picked before priority 2, before priority 3, etc.

**Priority Philosophy**: Human reviewers' time > Bot implementation time. Always respond to reviewers before starting new development.

## Edge Cases Handled

### Scenario 1: Reviewer Approves Without Commenting
**Problem:** If `lastActivityBy: "bot"` and reviewer just approves (no comment), PR could get stuck.

**Solution:** Merge readiness is checked EVERY iteration regardless of `lastActivityBy`. Approved PR will be detected and merged.

### Scenario 2: Reviewer Never Responds
**Problem:** PR sits in `pushed` state with `lastActivityBy: "bot"` forever.

**Solution:**
- Bot continues working on other stories (good parallelization)
- PR is checked every iteration for merge status
- If somehow auto-approved (CI green + required reviews), it merges
- Otherwise, human intervention needed (by design - we don't want to auto-merge unreviewed code)

### Scenario 3: Reviewer Keeps Requesting Changes
**Problem:** Story bounces between bot and reviewer many times.

**Solution:**
- Each iteration properly updates `lastActivityBy`
- Tests must pass after every change
- Progress is logged each iteration
- Eventually either merges or human escalates

### Scenario 4: Multiple Stories in Review
**Problem:** Bot needs to juggle multiple PRs awaiting review.

**Solution:**
- Stories with `lastActivityBy: "reviewer"` get high priority
- Stories with `lastActivityBy: "bot"` get medium priority (checked but not actively worked)
- Bot can work on pending stories while waiting
- Efficient parallelization

## State Transition Rules

| From State  | To State    | Trigger                                         | Quality Gate                           |
|-------------|-------------|-------------------------------------------------|----------------------------------------|
| pending     | committed   | Development work complete                       | ALL acceptance criteria tests pass     |
| pending     | skipped     | Duplicate PR already exists for this issue      | Verified existing PR addresses issue   |
| committed   | pushed      | Branch pushed, PR created                       | Code public, awaiting review           |
| pushed      | pushed      | Review feedback implementation complete         | ALL acceptance criteria tests pass     |
| pushed      | merged      | PR approved, CI passing, mergeable             | Required approvals, no blockers        |
| pushed      | invalid     | PR closed without merging / task already done   | Confirmed by reviewer or PR state      |
| merged      | (none)      | Terminal state                                  | N/A                                    |
| skipped     | (none)      | Terminal state                                  | N/A                                    |
| invalid     | (none)      | Terminal state                                  | N/A                                    |

**Note:** The `pushed → pushed` transition (responding to reviews) involves a complete implementation sub-cycle with the same testing requirements as `pending → committed`.

## Validation Checklist

✅ Every state has a clear exit condition
✅ No state can get permanently stuck
✅ Merge readiness checked every iteration
✅ Waiting states have lower priority (good parallelization)
✅ Active work states have higher priority
✅ Terminal state clearly defined
✅ Edge cases documented and handled

## Summary

This state machine ensures:
1. **No stuck states** - Every non-terminal state has a path forward
2. **Automatic progress** - Approved PRs merge automatically
3. **Smart waiting** - Don't spam reviewers, but don't forget PRs
4. **Parallelization** - Work on new stories while waiting for reviews
5. **Quality gates** - Tests must pass at every stage
