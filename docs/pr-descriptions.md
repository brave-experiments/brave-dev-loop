# PR descriptions

The reviewer is a busy human who has not seen this code, did not read the
issue, and is deciding in about thirty seconds whether to read the diff. The
description exists to answer two questions in that time:

1. **How do I see the problem myself?**
2. **What exactly was wrong?**

Everything else is optional. The diff is right there — the description is not
the place to re-narrate it.

`scripts/check-pr-body.py` enforces the mechanical half of this. Run it before
`gh pr create`; see [Checking a body](#checking-a-body).

## The shape

```markdown
Closes <owner>/<repo>#<issue>

## The problem
<2-4 sentences. What goes wrong, who hits it, what they see.>

## Reproduce
<Numbered steps in the running product, then what you observed and what you
expected. The test you added goes last, on one line.>

## The fix
<2-5 sentences in plain language. What changed and why that fixes it.>

## Test plan
- [x] <command> — passed
- [ ] CI passes cleanly
```

Four required sections, in that order. `Closes` first, above everything, or the
cross-repo issue does not close on merge. Anything else you want to say goes
after the test plan or inside `<details>`.

## The problem

Write it for someone who has never opened this repository. Name the symptom
before the mechanism: what a user, an operator, or a CI job actually observes.

- **Good:** "A completion that takes more than 60 seconds to start answering
  fails as a timeout and is retried, even though the configured limit is 600
  seconds. Every retry dies at the same 60 seconds, so a slow model never
  answers at all."
- **Bad:** "`Timeout::preceeding` in ureq 3.4 `src/timings.rs` clamps a phase
  deadline to the minimum of its own and its predecessors', so
  `timeout_send_request` bounds TTFB."

The second sentence is more precise and useless as an opener. It is the
mechanism, and the mechanism belongs in *The fix* — after the reader knows why
they should care. Four sentences is the budget. If the problem genuinely needs
more, the extra goes in `<details>`.

## Reproduce

**This is the section reviewers want most, and the one that is usually
missing.** Write it for a person who is going to follow it in the running
product. If a user can see the bug, that is what the reproduction has to show:

````markdown
## Reproduce
1. Configure a model that takes more than 60 seconds to produce its first
   token, and leave the reply timeout at its 600 second default.
2. Send it any prompt.
3. Watch the request log.

At 60 seconds the request fails with `timeout: send request` and is retried,
and every retry dies at the same 60 seconds, so no answer ever arrives.
Expected: the reply at 75 seconds, inside the 600 second limit.

`make test TEST=SlowReply.FirstByteAfterSendBound` covers it — fails on the
parent commit, passes here.
````

Rules:

- **A user-visible bug gets user-visible steps.** Numbered, in the running
  product, and specific enough to follow without guessing: the screen or URL,
  the setting, the input. Not "enable the feature and use it".
- **State both sides.** What you observed, and what you should have observed. A
  reproduction with no observed-vs-expected is a command, not evidence.
- **Paste-able beats descriptive** wherever a command is part of it: the exact
  command, with the directory it runs in if it is not the repo root. Not "run
  the net tests".
- **The test goes last, and it is not the reproduction.** Name the test you
  added and say it fails on the parent commit — that is how a reviewer checks
  both halves for themselves, and it belongs under the steps as supporting
  evidence. Alone, it says a test the author also wrote now passes, which is
  not the thing users complained about.
- **A test-only change reproduces with the test.** A disable, a flaky-test fix,
  a harness change — nothing a user could ever observe — and the test
  invocation is the whole reproduction. Pass `--test-only-change` to the
  checker so it stops asking for steps.

If it genuinely cannot be reproduced on a laptop — a CI-only flake, a
platform-specific build, a race that needs the fleet — say so on a line
beginning `Not reproducible locally:` followed by the reason and a link to the
evidence (the CI job, the crash report, the issue with the logs). The checker
accepts that line in place of steps. It does not accept silence, and it does
not accept using the line to avoid the work.

## The fix

Now the mechanism, in plain language, in a few sentences. Name a symbol only
where naming it is the shortest true statement — "the retry loop", "the AWS
credential chain" and "the session cache" beat their identifiers most of the
time. No file paths, no line numbers, no upstream source citations: the
reviewer has the Files Changed tab, and a path in prose goes stale the first
time something moves.

Do not enumerate the diff. "Added `foo()`, changed `bar()` to take a `&str`,
updated the callers" tells a reviewer nothing they will not see in colour in
ten seconds. Say what the code now does differently and why that is the fix.

## Test plan

Checkboxes, one per command actually run, with the result. Real commands and
real outcomes — a checkbox for something you did not run is a lie the reviewer
will find in CI. Keep `- [ ] CI passes cleanly` last and unchecked.

## What not to write

These are what make a description read as machine-generated filler. The checker
rejects the ones it can match:

- **Preamble.** "This PR adds…", "In this change, we…". Start with the problem.
- **Self-assessment.** `robust`, `comprehensive`, `seamless`, `elegant`,
  `cleanly handles`, `significantly improves`, `properly handles`. Nobody
  writes these about their own patch except to pad it.
- **Emoji section headers**, ✅ checkmark decoration, bold on every other
  phrase.
- **A file-by-file changelog.** That is the diff.
- **Restating the issue body** at length. Link it and summarise in two lines.
- **Any AI attribution.** No "Generated with", no `Co-Authored-By: Claude`.

Length is the tell that outlasts all of them: if the visible body is longer
than a screen, a busy reviewer reads none of it. Depth is not banned — it is
`<details>`' job:

```markdown
<details>
<summary>Why the timeout knobs had to change together</summary>

<the table, the upstream citation, the four cases>

</details>
```

Collapsed, it costs a reviewer nothing and is there for the one who wants it.
That is where a comparison table, a benchmark, a long root-cause chain, or a
spec-clause reference belongs.

## A rewrite

Real body, shortened to its shape:

> ## Summary
> The effective bound on time-to-first-byte was 60s (`Timeouts::send`), not the
> configured 600s (`Timeouts::reply`). A non-streaming completion POST whose
> answer took 75s to produce its first byte failed with
> `ureq::Error::Timeout(SendRequest)`, became a transient
> `EgressError::Transport`, and was retried — each attempt cut at the same 60s.
>
> ## Fix
> ureq gives a phase the earliest of its own deadline and those of the phases
> before it (`Timeout::preceeding`, ureq 3.4 `src/timings.rs`), which its
> configuration API does not document. `timeout_send_request`/
> `timeout_send_body` therefore bounded the wait for the reply as well as the
> send. <a four-row table follows>

Accurate, and it opens with two type names and a field name. What a reviewer
needs first:

> ## The problem
> A model that takes more than 60 seconds to start answering never answers at
> all. The request fails as a timeout, retries, and each retry dies at the same
> 60 seconds — even though the configured limit for a reply is 600 seconds.
> Slow models and long prompts are unusable.
>
> ## Reproduce
> 1. Configure a model that takes more than 60 seconds to produce its first
>    token, leaving the reply timeout at 600 seconds.
> 2. Send any prompt and watch the request log.
>
> The request fails at 60 seconds with `timeout: send request` and is retried,
> each retry dying at the same 60 seconds. Expected: the reply at 75 seconds.
>
> `make test TEST=SlowReply.FirstByteAfterSendBound` covers it — a loopback
> server that waits 900ms against a 200ms send limit and a 10s reply limit.
> Fails on the parent commit, passes here.
>
> ## The fix
> Our timeout for sending a request was also being applied to waiting for the
> reply, because ureq clamps each phase to the shortest deadline set on any
> earlier phase — undocumented. Each value we hand ureq now accounts for every
> phase it actually bounds, so the wait for a reply is bounded by the reply
> limit alone.
>
> <details><summary>Which knob bounds which phase</summary>
> <the table>
> </details>

Same facts, same precision. The reviewer gets the symptom, steps they can
follow, and the cause before any identifier appears.

## Checking a body

```sh
python3 scripts/check-pr-body.py --body-file /tmp/pr-body.md
python3 scripts/check-pr-body.py --body-file /tmp/pr-body.md --test-only-change
python3 scripts/check-pr-body.py --pr 214 --repo <owner>/<repo>   # after the fact
```

`--test-only-change` is for a diff that touches only tests: it drops the
warning about reproducing by test invocation alone. Passing it for a change a
user can see defeats the point of the section.

Errors exit non-zero and must be fixed before the PR is created. Warnings are
printed and do not fail — read them, they are the slop and reproduction
heuristics, and they are right more often than not. `--strict` makes warnings
fail too.

The checker matches structure and phrasing. It cannot tell whether the
reproduction actually reproduces, or whether the problem statement is true.
Those are still yours.
