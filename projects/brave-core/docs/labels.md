# brave-core: Labels

Project-specific label rules. Read alongside `docs/workflow-committed.md`,
which owns the generic rule: apply the labels the profile defines, invent none.

The profile's `labels.pr` (`ai-generated`) goes on every bot PR. The labels
below are the judgement calls on top of it.

## PR and linked issue

**For test issue fixes (add to BOTH PR and linked issue):**
- `QA/No` — manual QA not needed
- `release-notes/exclude` — not user-facing
- `CI/skip` — **only if the change is limited to filter files** (e.g., `test/filters/`). This skips unnecessary CI for trivial filter-only changes.

**For other PRs:**
- `release-notes/exclude` — add to both PR and linked issue for changes typical users wouldn't care about (code cleanup, refactors, internal tooling, etc.)
- `QA/No` — use judgment based on whether manual QA testing is needed

## OS/platform labels (ALWAYS add to the linked issue)

Determine which platform(s) the fix relates to, then add the matching OS label(s) to the linked issue:
- `OS/Desktop` — the fix targets desktop (Windows, macOS, Linux)
- `OS/Android` — the fix targets Android
- `OS/iOS` — the fix targets iOS

Add all that apply (a cross-platform fix may span multiple platforms). Base the decision on the affected code's location, platform-specific build flags/guards (e.g. `BUILDFLAG(IS_ANDROID)`, `BUILDFLAG(IS_IOS)`, desktop-only code paths), and where the issue was reported.

```bash
gh issue edit <issue-number> --add-label "OS/Desktop" --repo $ISSUE_REPO
```

## Disabled tests

`disabled-brave-test` (the profile's `labels.disabledTest`) marks an issue as a
disabled test. See `docs/testing.md` in this profile.
