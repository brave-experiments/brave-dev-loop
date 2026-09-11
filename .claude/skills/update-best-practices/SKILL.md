---
name: update-best-practices
description: "Fetch upstream Chromium documentation and merge guidelines into our best practices files. Detects new rules, conflicts, and outdated entries. Triggers on: update best practices, merge upstream best practices, sync best practices, fetch chromium guidelines."
argument-hint: <url1> [url2] [url3...]
---

# Update Best Practices from Upstream Docs

Fetch upstream Chromium documentation URLs, extract guidelines and rules, compare against our existing best practices, and merge new/corrected content. Detects inter-rule conflicts across all best practices files.

---

## The Job

When the user invokes `/update-best-practices <urls...>`:

1. **Parse URLs** from the arguments (space-separated or comma-separated)
2. **Read all existing best practices files** to understand current state
3. **Fetch each upstream URL** and extract all guidelines/rules
4. **Compare** upstream content against existing best practices
5. **Detect inter-rule conflicts** across all best practices files
6. **Update best practices files** with new rules, corrections, and upstream links
7. **Generate a summary report** of all changes

If no URLs are provided, use the known upstream reference URLs (see below).

---

## Known Upstream Reference URLs

These are authoritative Chromium documentation pages relevant to our best practices. Use these as defaults when no URLs are specified:

| URL | Maps To |
|-----|---------|
| https://www.chromium.org/developers/smart-pointer-guidelines/ | `coding-standards.md`, `architecture.md` |
| https://www.chromium.org/developers/design-documents/cookbook/ | `architecture.md` |
| https://www.chromium.org/chromium-os/developer-library/guides/testing/cpp-writing-tests/ | `testing-async.md`, `testing-isolation.md` |
| https://chromium.googlesource.com/chromium/src/+/HEAD/styleguide/c++/c++.md | `coding-standards.md` |
| https://chromium.googlesource.com/chromium/src/+/HEAD/base/containers/README.md | `coding-standards.md` |

The user may also provide any other Chromium documentation URL.

---

## Step 1: Read All Existing Best Practices

Discover all `.md` files in `$TARGET_REPO/docs/best-practices/` dynamically:

```bash
python3 $BOT_DIR/.claude/skills/review-prs/discover-best-practices.py $TARGET_REPO/docs/best-practices/
```

This returns a JSON array of all best-practice documents. Read every file listed to build a complete picture of current rules.

Also read `$TARGET_REPO/docs/best_practices.md` (the index file).

For each file, catalog every rule by its heading/title so you can check for duplicates and conflicts.

---

## Step 2: Fetch Upstream Documentation

For each URL provided (or from the known list):

1. Use `WebFetch` to retrieve the page content
2. Extract every guideline, rule, convention, and recommendation
3. Include code examples where provided
4. Note the source URL for each extracted rule

**Be thorough** - extract ALL rules from each document, not just the ones that seem relevant at first glance.

---

## Step 3: Compare and Categorize

For each upstream rule, classify it:

### A. Already Covered (No Action)
The rule is already present in our best practices with correct and complete information.

### B. Partially Covered (Update)
We have a related rule but it's incomplete, less detailed, or missing important nuances from upstream. Update the existing section.

### C. Conflicts with Existing Rule (Fix)
The upstream documentation directly contradicts something in our best practices. This is the highest priority to fix.

### D. New Rule (Add)
The upstream rule covers something we don't have at all. Add it to the appropriate best practices file.

### E. Not Applicable (Skip)
The rule is about Chromium-internal processes that don't apply to Brave's workflow.

### F. Covered by Tooling (Skip)
The rule is already enforced automatically by linting, presubmit checks, or code formatters (e.g., clang-format, cpplint, PRESUBMIT.py). Don't add rules that tools already catch — they add noise and go stale when tooling changes.

---

## Step 4: Detect Inter-Rule Conflicts

Scan ALL best practices files for internal contradictions. Common conflict patterns:

- One file says "always do X", another says "never do X"
- A rule in `architecture.md` contradicts a rule in `coding-standards.md`
- A "banned" item in one file is described as "use carefully" in another
- Testing guidance that contradicts coding standards

**Also check every rule you are about to add or edit against the rules already
in the file you are inserting it into.** The most common self-inflicted conflict
is adding guidance that a reviewer is expected to enforce next to a rule that
says the opposite. Real example: a new paragraph in `coding-standards.md` told
reviewers to check that platform includes keep "the normal include order within
that section", while `CS-010` in the same file says **"Do NOT enforce include
order — include ordering is handled by code formatting tools and lint, not by
code review"**. When upstream guidance is about formatting that tooling already
enforces, either drop it or state explicitly that it is not review-enforced, and
link the rule that owns the exception.

**Pay special attention to:**
- Meta-rules about what reviewers should and should not flag (formatting,
  include order, line wrapping) — new rules must not re-import what these
  explicitly exclude
- Smart pointer usage rules (unique_ptr, shared_ptr, scoped_refptr, raw_ptr, WeakPtr)
- Thread safety and callback patterns (Unretained, WeakPtr, PostTask)
- Feature flag guidance (when to guard, where to check)
- Ownership and lifetime rules
- Layering and dependency rules

---

## Step 5: Apply Changes

### For Conflicts (Category C - highest priority)
- Fix the incorrect rule to match upstream
- Add a link to the authoritative upstream document
- If both rules have merit, reconcile them into a single consistent rule

### For Partial Coverage (Category B)
- Enhance the existing section with missing details
- Add code examples from upstream where helpful
- Add link to upstream doc for more detail

### For New Rules (Category D)
- Add to the most appropriate best practices file based on topic:
  - Architecture/layering/services/factories -> `architecture.md`
  - C++ style/naming/memory/APIs -> `coding-standards.md`
  - Async testing/synchronization -> `testing-async.md`
  - Test isolation/mocking/patterns -> `testing-isolation.md`
  - JavaScript in tests -> `testing-javascript.md`
  - Navigation/timing in tests -> `testing-navigation.md`
  - Build system/GN/deps -> `build-system.md`
  - chromium_src overrides -> `chromium-src-overrides.md`
- Follow the existing format: `## <emoji> <Title>` with BAD/GOOD code examples
- Keep entries concise - link to upstream for full details

### For Inter-Rule Conflicts
- Fix the incorrect rule
- Add a cross-reference comment if the rules are related but address different aspects

---

## Step 6: Update best_practices.md

If new upstream reference URLs were processed, add them to the References section in `$TARGET_REPO/docs/best_practices.md`.

---

## Step 7: Generate Summary Report

Output a summary to the user with:

```markdown
# Best Practices Update Summary

## Upstream URLs Processed
- <url1> - <number of rules extracted>
- <url2> - <number of rules extracted>

## Changes Made

### Conflicts Fixed
- **<file>: <rule title>** - <what was wrong> -> <what it says now>

### Rules Updated (Partial Coverage)
- **<file>: <rule title>** - <what was added/changed>

### New Rules Added
- **<file>: <rule title>** - <brief description>

### Inter-Rule Conflicts Found and Fixed
- **<file1> vs <file2>**: <description of conflict> -> <resolution>

## Already Covered (No Changes Needed)
- <list of upstream rules that were already correctly documented>

## Skipped (Not Applicable)
- <list of upstream rules that don't apply to Brave>
```

---

## Important

- **Upstream docs are authoritative** - when our docs conflict with Chromium docs, fix ours (unless it's a deliberate Brave-specific deviation, which should be documented as such)
- **Skip rules covered by tooling** - don't add best practices that are already enforced by linting, presubmit checks, or formatters (clang-format, cpplint, PRESUBMIT.py, etc.); these rules are redundant and become misleading when tooling evolves
- **Keep entries concise** - link to upstream for full details rather than duplicating everything
- **Preserve existing format** - match the `## <emoji> <Title>` heading style with BAD/GOOD examples
- **Don't remove Brave-specific rules** - rules about Brave patterns (e.g., `Brave*` prefix convention) have no upstream equivalent and should be kept
- **Do NOT include Co-Authored-By attribution in commits**
- **Check for inter-rule conflicts** even if the user only asks about a specific URL

---

## Step 8: Commit and Create PR

If changes were made to best practices files, commit them and create a PR so they persist:

1. **Navigate to the target repo** directory:
   ```bash
   cd $TARGET_REPO
   ```
2. **Create a new branch** from the upstream default branch, fetched first so the
   base is current:
   ```bash
   git fetch upstream
   git checkout -b docs/best-practices-update-$(date +%Y%m%d-%H%M%S) upstream/master
   ```
   The base is `upstream/master`, not the current HEAD and not `origin/master`:
   `origin` is the bot's fork, nothing pushes upstream's commits to it, and the
   checkout is left on whatever the last story used. Either one puts commits the
   skill did not write into its own pull request. See
   [git-repository.md](../../../docs/git-repository.md#branch-management-for-each-user-story).
3. **Stage and commit** only the changed best practices files:
   ```bash
   git add docs/best_practices.md docs/best-practices/
   git commit -m "Update best practices from upstream Chromium docs"
   ```
4. **Run format and presubmit**, then amend any resulting changes into the commit. Both must pass before the PR is created:
   ```bash
   pnpm run format
   pnpm run presubmit
   ```
   If either command modifies files (e.g. formatting fixes), stage and amend them into the commit:
   ```bash
   git add -A
   git commit --amend --no-edit
   ```
   If presubmit reports errors that aren't auto-fixed, resolve them and amend before proceeding. Do not create the PR until `pnpm run presubmit` passes cleanly.
5. **Push and create a PR**:
   ```bash
   git push -u origin HEAD
   gh pr create --title "Update best practices from upstream Chromium docs" --body "$(cat <<'EOF'
   ## Summary
   - Synced best practices with upstream Chromium documentation
   - <summary of conflicts fixed, rules updated, new rules added>

   Auto-generated by update-best-practices skill.
   EOF
   )"
   ```
6. **Return to the bot directory**:
   ```bash
   cd $BOT_DIR
   ```
7. **Track the PR in the PRD** so the normal `pushed` workflow keeps reviewing,
   rebasing, and landing it. A PR with no story is invisible to `run.sh` and
   stalls open forever:
   ```bash
   python3 $BOT_DIR/scripts/sync-bot-prs-to-prd.py --pr <pr-number>
   ```
   Report the story ID it prints in the recap.

If no changes were made, skip this step entirely.

---

## Step 9: Signal Notification

After the summary report, send a Signal notification. Each changed file goes on its own line.

**If changes were made:**

```bash
$BOT_DIR/scripts/signal-notify.sh "Best practices updated from <N> upstream URLs.
Conflicts fixed: <C>, rules updated: <U>, new rules: <R>.
PR: <pr_url>
Files changed:
<file1>
<file2>"
```

**If no changes were needed:**

```bash
$BOT_DIR/scripts/signal-notify.sh "Best practices sync: <N> upstream URLs checked, all rules already up to date."
```

Do NOT send a notification without listing changed files when changes were made.

This is a no-op if Signal is not configured.
