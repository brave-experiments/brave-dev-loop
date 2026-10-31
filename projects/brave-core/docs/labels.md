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

## Pull request labels

`/review-prs` labels each pull request once, the first time it reads it, as
[pr-labels.md](../../../docs/pr-labels.md) describes. The labels are `review.labelPrs` in this
profile, in `brave/brave-core`.

| Label | Decided by | Applies when |
| --- | --- | --- |
| `tests-only` | paths | every changed file is a test or test data |
| `chromium-overrides` | paths | some changed file is under `chromium_src/` |
| `risk/dependencies` | paths | some changed file is a dependency manifest or lockfile, `DEPS`, or under `third_party/` |
| `risk/funds` | model | the change can move, lose, expose or miscount a user's money or keys |
| `risk/security` | model | the change touches a security boundary |
| `risk/privacy-promises-affected` | model | the change can alter something Brave tells users it does for their privacy |
| `risk/network-calls` | model | the change adds or alters a network request |

brave-core already has `dependencies` (renovate and dependabot), `tests`, `security` and
`needs-security-review`. They are not reused: `dependencies` marks an update bot's PR, and
`risk/dependencies` marks any PR that changes what the build pulls in.

```bash
R=brave/brave-core
gh label create tests-only --repo $R --color 0E8A16 --description "Every changed file is a test or test data"
gh label create chromium-overrides --repo $R --color 5319E7 --description "Changes a chromium_src override of upstream Chromium code"
gh label create risk/dependencies --repo $R --color D93F0B --description "Changes a dependency manifest, lockfile, DEPS or third_party"
gh label create risk/funds --repo $R --color D93F0B --description "Can move, lose, expose or miscount a user's money or keys"
gh label create risk/security --repo $R --color D93F0B --description "Touches a security boundary: IPC, sandbox, permission checks, parsing, crypto, credentials"
gh label create risk/privacy-promises-affected --repo $R --color D93F0B --description "Can change something Brave tells users it does for their privacy"
gh label create risk/network-calls --repo $R --color D93F0B --description "Adds or alters a network request, endpoint or the data sent"
```
