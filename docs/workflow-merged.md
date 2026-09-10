# Status: "merged" (Complete with Post-Merge Monitoring)

**Goal: Monitor for post-merge follow-up requests**

When a PR is merged, reviewers or stakeholders may post follow-up comments asking for additional work, reporting related issues, or requesting improvements. The bot monitors merged PRs on an exponential backoff schedule to catch these requests.

## Post-Merge Monitoring Schedule

Merged stories are rechecked at these intervals after merge:
1. **1 day** after merge
2. **2 days** after first check (3 days total)
3. **4 days** after second check (7 days total)
4. **8 days** after third check (15 days total)
5. **Final state** - no more checking

## Data Structure

When a story transitions to "merged", add these fields:
```json
{
  "status": "merged",
  "mergedAt": "2026-02-02T10:00:00Z",
  "nextMergedCheck": "2026-02-03T10:00:00Z",
  "mergedCheckCount": 0,
  "mergedCheckFinalState": false
}
```

## Workflow When Story Becomes Merged

After merging a PR (in the "pushed" → "merged" transition):

1. Set `mergedAt` to current timestamp
2. Set `nextMergedCheck` to `mergedAt + 1 day`
3. Set `mergedCheckCount` to `0`
4. Set `mergedCheckFinalState` to `false`

## During Task Selection - Check for Merged Stories Needing Recheck

Before picking active stories, check for merged stories that need rechecking:

1. Filter stories where:
   - `status: "merged"`
   - `mergedCheckFinalState: false`
   - `nextMergedCheck` is in the past (current time >= nextMergedCheck)

2. If any merged stories need rechecking, pick the one with the OLDEST `nextMergedCheck` timestamp

3. This has priority BELOW all active work (pushed PRs, committed code, and pending development):
   - URGENT: `status: "pushed"` + `lastActivityBy: "reviewer"`
   - HIGH: `status: "committed"`
   - MEDIUM: `status: "pushed"` + `lastActivityBy: "bot"`
   - NORMAL: `status: "pending"`
   - **LOW (Post-Merge Monitoring)**: `status: "merged"` + needs recheck (only when enabled via run-state.json)

## Workflow for Rechecking Merged Story

When a merged story is picked for rechecking:

### 1. Fetch Post-Merge Comments

```bash
$BOT_DIR/scripts/filter-pr-reviews.sh <pr-number> markdown <pr-repository>
```

### 2. Filter to Comments AFTER Merge

- Only look at comments with timestamp > `mergedAt`
- Only consider comments from org members (filtered script handles this)

### 3. Analyze for Follow-Up Requests

- Look for requests for new features, related fixes, or follow-up issues
- Look for reports of problems introduced by the merged PR
- Look for requests to file tracking issues

### 4. If Follow-Up Work is Suggested But Not Needed

When a post-merge comment suggests follow-up work, but you determine it's not necessary for a valid reason:

1. **Evaluate the suggestion**: Determine if it's:
   - A minor optional style improvement (not a bug or functional issue)
   - Already addressed or not applicable
   - A reasonable enhancement but not critical

2. **Post an explanatory comment**: Reply to the comment thread explaining your decision.

**Finding comment IDs in filtered PR data:**

The filtered PR reviews script returns JSON with three comment types:
- `review_comments[]` - Inline code review comments (have `id` field)
- `issue_comments[]` - General PR discussion comments (have `id` field)
- `reviews[]` - Review summaries (use review ID, different API endpoint)

To extract comment IDs from JSON output:
```bash
# Get review comments (inline code comments)
REVIEW_COMMENT_IDS=$(echo "$FILTERED_DATA" | jq -r '.review_comments[].id')

# Get discussion comment IDs
ISSUE_COMMENT_IDS=$(echo "$FILTERED_DATA" | jq -r '.issue_comments[].id')

# Get specific comment ID for a user (e.g., @goodov)
COMMENT_ID=$(echo "$FILTERED_DATA" | jq -r '.issue_comments[] | select(.user.login == "goodov") | .id')
```

**Posting the reply:**

```bash
# Get PR repository from bot config (provided in prompt)
PR_REPO="<project.prRepository from bot config>"

# Calculate next check timespan based on current mergedCheckCount
# mergedCheckCount 0 (just did first check): next in 2 days
# mergedCheckCount 1 (just did second check): next in 4 days
# mergedCheckCount 2 (just did third check): next in 8 days
CURRENT_CHECK_COUNT=$(jq -r '.mergedCheckCount' <<< "$STORY_JSON")
case $CURRENT_CHECK_COUNT in
  0) NEXT_CHECK_DAYS="2 days";;
  1) NEXT_CHECK_DAYS="4 days";;
  2) NEXT_CHECK_DAYS="8 days";;
  *) NEXT_CHECK_DAYS="the final check period";;
esac

# For review comments (inline code comments), use the pull request comment replies API:
gh api \
  --method POST \
  "/repos/$PR_REPO/pulls/comments/<comment-id>/replies" \
  -f body="$(cat <<'EOF'
@[username] Thank you for the suggestion. I've evaluated this and determined it's not necessary to create a follow-up task because:

[Your specific reasoning - e.g., "This is a minor optional style improvement that doesn't affect functionality or correctness"]

If you believe this change is important and should be prioritized, please let me know within the next [timespan] and I'll create a follow-up task for it.

I'll continue monitoring this PR for [timespan] as part of the post-merge review process.
EOF
)"

# For discussion comments (issue comments), reply using the issue comment API:
gh api \
  --method POST \
  "/repos/$PR_REPO/issues/comments/<comment-id>/replies" \
  -f body="[same body as above]"
```

Replace:
- `<comment-id>`: The GitHub comment ID from the filtered PR reviews
- `[username]`: The GitHub username who made the suggestion (without @)
- `[Your specific reasoning]`: Clear explanation of why follow-up work isn't needed
- `[timespan]`: The time until next check (e.g., "2 days", "4 days", "8 days")

**Important notes:**
- Always provide a clear, specific reason for declining the follow-up
- Be respectful and acknowledge the reviewer's input
- If it's a borderline case or reasonable suggestion, explicitly invite them to indicate importance
- Try the appropriate reply API first (review comment vs issue comment)
- If the reply API fails (not all comment types support threaded replies), fall back to a top-level comment:

```bash
gh pr comment <pr-number> --body "[same body as above]"
```

### 5. If Follow-Up Work is Needed

For each follow-up task requested in post-merge comments:

#### a. Create GitHub Issue

Get the issue repository from the bot config provided in the prompt (`project.issueRepository`).

Create a detailed GitHub issue:
```bash
gh issue create --repo "$ISSUE_REPO" \
  --title "[Follow-up] Brief description of the follow-up task" \
  --body "$(cat <<'EOF'
## Context
This is a follow-up task from PR #[original-pr-number] which fixed [original-story-title].

## Post-Merge Request
[Quote the relevant comment from the reviewer/stakeholder requesting this work]
- Requested by: @[username]
- Comment timestamp: [timestamp]
- Original PR: [PR URL]

## What Needs to Be Done
[Detailed description of the follow-up work requested]

[If applicable: Why this follow-up is necessary]

## Acceptance Criteria
- [ ] [Specific requirement 1]
- [ ] [Specific requirement 2]
- [ ] [Test to verify the fix]

## Related
- Original Story: [original-story-id]
- Original PR: #[pr-number]
- Post-merge comment: [link to specific comment]
EOF
)"
```

Capture the issue number from the output.

#### b. Add Story to prd.json

Create a new story entry following the same format as existing stories:
```json
{
  "id": "US-XXX",
  "title": "Brief title matching the GitHub issue",
  "description": "Follow-up from US-[original] PR #[pr-number]: [Description of what needs to be done]",
  "acceptanceCriteria": [
    "[Each entry in `research` from the project profile, with its placeholders resolved]",
    "[Specific test or verification step]",
    "[Additional requirements]"
  ],
  "status": "pending",
  "priority": [appropriate-number],
  "issueUrl": "https://github.com/[issue-repo]/issues/[issue-number]",
  "issueNumber": [issue-number],
  "relatedStories": ["US-[original-story-id]"],
  "relatedPRs": ["#[original-pr-number]"]
}
```

**Key fields:**
- `id`: Next available US-XXX number in sequence
- `title`: Clear, concise title matching the GitHub issue
- `description`: Include context from original story and PR
- `acceptanceCriteria`: Start with the project profile's `research` steps (`projects/<profile>/profile.json`, placeholders resolved as [Project profiles](../projects/README.md) describes), then the specific requirements from the post-merge request, then the profile's `validations`
- `status`: Always `"pending"` for new stories
- `priority`: Set based on urgency (if blocker/critical: low number, if enhancement: higher number)
- `issueUrl`: Full URL to the GitHub issue you just created
- `issueNumber`: The issue number as an integer
- `relatedStories`: Array with original story ID
- `relatedPRs`: Array with original PR number

Write the updated prd.json with the new story added to the `stories` array.

#### c. Reply to Requester on PR (in same thread)

From the filtered PR reviews data, you have the comment ID of the post-merge comment requesting follow-up work.

Reply directly to that comment using the GitHub API:
```bash
# Get PR repository from bot config (provided in prompt)
PR_REPO="<project.prRepository from bot config>"

# Reply to the specific comment (creates a threaded reply)
gh api \
  --method POST \
  "/repos/$PR_REPO/pulls/comments/<comment-id>/replies" \
  -f body="$(cat <<'EOF'
@[username] Thank you for the follow-up request. I've created a tracking issue for this work:

🔗 [Issue #[issue-number]: [issue-title]]([issue-url])

This has been added to the work queue as story US-XXX and will be prioritized accordingly.
EOF
)"
```

Replace:
- `<comment-id>`: The GitHub comment ID from the filtered PR reviews (the comment that requested follow-up)
- `[username]`: The GitHub username who requested the follow-up (without @)
- `[issue-number]`: The issue number from step a
- `[issue-title]`: The issue title
- `[issue-url]`: Full URL to the issue
- `US-XXX`: The story ID from step b

**Important:** The filtered PR reviews data includes comment IDs. Use the ID of the specific comment requesting the follow-up so your reply appears in the same thread as their request.

If the comment type doesn't support replies (e.g., review comments vs PR comments), fall back to a top-level comment:
```bash
gh pr comment <pr-number> --body "[same body as above]"
```

### 6. Update the Recheck Schedule

Run the merged-check command to increment the count and recalculate the backoff:
```bash
python3 $BOT_DIR/scripts/update-prd-status.py merged-check <story-id>
```
This handles the exponential backoff schedule (2d, 4d, 8d) and sets `mergedCheckFinalState: true` after the fourth check.

### 7. Update progress.txt

See [progress-reporting.md](./progress-reporting.md) for the post-merge check format.

## Important Notes

- Post-merge rechecking does NOT require GitHub API calls during task selection - the decision is based purely on timestamps in prd.json
- Merged stories in final state (`mergedCheckFinalState: true`) are never picked during task selection
- This system is independent from the main workflow and doesn't block other work
- If no merged stories need rechecking, task selection proceeds normally to active/pending stories
