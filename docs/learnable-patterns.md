# Learnable Patterns

This document defines how the bot identifies, evaluates, and captures reusable patterns from its work. Patterns improve future PRs by preventing repeated mistakes and encoding conventions learned from review feedback.

## Sources of Learnable Patterns

Patterns can be discovered from two sources:

### 1. Review Feedback on the PR Repository's PRs

When reviewers comment on bot PRs, their feedback often encodes team conventions, coding standards, or project-specific idioms that apply beyond the current PR.

### 2. Previous Fix Attempts (GitHub PR History)

Before implementing any fix, the bot must research previous PRs that attempted the same fix. These previous attempts are searchable in GitHub within the PR repository:

```bash
# Search for previous PRs that attempted to fix this issue
gh api search/issues --method GET \
  -f q="repo:$PR_REPO is:pr <issue-number> OR <test-name-or-keywords>" \
  --jq '.items[] | {number, title, state, html_url, user: .user.login}'

# Get the diff from a previous attempt
gh pr diff <pr-number> --repo $PR_REPO

# Get review comments to understand why it failed/was rejected
gh pr view <pr-number> --repo $PR_REPO --json reviews,comments
```

Review comments on closed/rejected PRs are especially valuable since they explain what went wrong and why.

---

## Identifying Learnable Patterns

### IS a Learnable Pattern

Review comments or past failures that indicate general rules:
- "We always do X when Y" or "Our convention is..."
- "This is a common mistake" or "Watch out for this pattern"
- Feedback about coding style, naming conventions, or architectural approaches
- Test patterns or testing requirements specific to this project
- API usage patterns or project-specific idioms
- Security practices or review requirements
- Patterns of why previous fix attempts failed (e.g., "timing-based fixes never work for this class of problem")

### NOT a Learnable Pattern (skip)

- Bug fixes specific to one PR
- Typo corrections
- Logic errors in one specific implementation
- One-time edge cases

---

## Evaluating Patterns After Addressing Review Feedback

After pushing review-requested changes, answer these questions:

1. Does the feedback mention "we always do X", "our convention is", or "in general, we..."?
2. Does the feedback describe a coding style, naming convention, or organizational pattern?
3. Is this about a common mistake or general best practice (not specific to just this PR)?
4. Would other PRs benefit from knowing this pattern upfront?

**If YES to any question:** Create a documentation PR to the bot repository (see [Capturing Patterns](#capturing-patterns) below).

**If NO to all questions:** The feedback is PR-specific, no documentation needed.

---

## Capturing Patterns

### 1. Determine Where the Pattern Belongs

| Pattern Type | Document Location |
|-------------|-------------------|
| Async testing, RunUntil patterns | `$TARGET_REPO/docs/best_practices.md` |
| Git workflow, branch naming | `$BOT_DIR/docs/git-repository.md` |
| Test execution requirements | `$BOT_DIR/docs/testing-requirements.md` |
| Problem-solving approaches | `$BOT_DIR/docs/workflow-pending.md` (Problem-Solving Approach section) |
| PR creation, commit messages | `$BOT_DIR/docs/workflow-committed.md` |
| Review response patterns | `$BOT_DIR/docs/workflow-pushed.md` |
| Security practices | `$BOT_DIR/SECURITY.md` |
| General codebase patterns | `$BOT_DIR/data/progress.txt` (Codebase Patterns section) |

### 2. Create a Separate Branch and PR for the Bot Repository

```bash
# Save current directory
ORIGINAL_DIR=$(pwd)

# Switch to the bot directory
cd $BOT_DIR

# Create a new branch for the documentation update
git checkout master
git pull origin master
git checkout -b docs/learn-<brief-description>

# Make the documentation changes
# ... edit the appropriate file ...

# Commit the changes
git add <file>
git commit -m "Add learned pattern: <brief description>

Learned from review feedback on PR #<pr-number>: <one-line summary of the pattern>"

# Push and create PR
git push -u origin docs/learn-<brief-description>
gh pr create --title "Add learned pattern: <brief description>" --body "$(cat <<'EOF'
## Summary
- Captured a reusable pattern from review feedback on the PR repository's PR #<pr-number>

## Pattern
<describe the pattern that was learned>

## Source
Review comment: <link to the specific review comment if available>
EOF
)"

# Return to original directory
cd "$ORIGINAL_DIR"
```

### 3. Document the Learning in progress.txt

- Note that a pattern was identified and captured
- Reference the bot repository PR number

### When to Skip

- If the review feedback is specific to the current PR only
- If the pattern is already documented

---

## Using Patterns from Previous Fix Attempts

When researching previous fix attempts (see [workflow-pending.md](./workflow-pending.md) step 4), extract patterns from WHY those attempts failed:

1. **Search closed/rejected PRs** in the PR repository for the same issue or test name
2. **Read the diffs** to understand what approaches were tried
3. **Read review comments** to understand why the approach was rejected or failed
4. **Look for recurring themes** across multiple failed attempts (e.g., "all three previous attempts tried timing-based fixes and all were rejected")
5. **Capture generalizable lessons** as patterns (e.g., "observer-based synchronization is required for X type of test")

If a pattern from previous failures would prevent future PRs from repeating the same mistake, document it using the process above.

---

## Codebase Patterns in progress.txt

For quick, lightweight patterns that don't warrant a documentation PR, add them to the `## Codebase Patterns` section at the TOP of `$BOT_DIR/data/progress.txt`:

```
## Codebase Patterns
- Example: Use `sql<number>` template for aggregations
- Example: Always use `IF NOT EXISTS` for migrations
- Example: Export types from actions.ts for UI components
```

Only add patterns that are **general and reusable**, not story-specific details.
