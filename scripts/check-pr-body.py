#!/usr/bin/env python3
"""Check a pull request body against docs/pr-descriptions.md.

The reviewer is a busy human. This enforces the mechanical half of that: the
four sections in order, a reproduction a person can follow rather than only a
test to run, a closing line that will actually close the issue, and no
machine-generated filler.

    python3 scripts/check-pr-body.py --body-file /tmp/pr-body.md
    python3 scripts/check-pr-body.py --pr 214 --repo brave/bravebot
    gh pr view 214 --json body -q .body | python3 scripts/check-pr-body.py

Errors exit 1 and must be fixed before the PR is created. Warnings print and
exit 0 unless --strict. What it cannot check -- whether the reproduction
reproduces, whether the problem statement is true -- is still the author's.
"""

import argparse
import re
import subprocess
import sys

# The four required sections, in order. Matched case-insensitively, and the
# older headings each one replaced are accepted as aliases so a body written
# to the previous template reports the real problem (a missing reproduction)
# rather than four spurious "section missing" errors.
REQUIRED_SECTIONS = [
    ("The problem", ("the problem", "problem", "summary", "what was true")),
    ("Reproduce", ("reproduce", "reproduction", "steps to reproduce", "repro")),
    ("The fix", ("the fix", "fix", "root cause", "the change")),
    ("Test plan", ("test plan", "testing", "how this was tested")),
]

# Phrases that only ever appear when prose is being padded. Kept to ones with
# no honest use in a PR body -- anything ambiguous belongs in the doc, not here.
SLOP_PHRASES = [
    (
        r"\bthis (?:pr|change|commit|patch) (?:adds|introduces|implements|provides)\b",
        "opens with preamble; start with the problem",
    ),
    (r"\bin this (?:pr|change|commit)\b", "preamble; the reader knows where they are"),
    (
        r"\b(?:robust|comprehensive|seamless|elegant|thorough)(?:ly)?\b",
        "self-assessment; state what it does, not how good it is",
    ),
    (r"\bsignificantly (?:improves?|enhances?|reduces?)\b", "self-assessment"),
    (
        r"\b(?:properly|correctly|gracefully|cleanly) (?:handles?|handled|handling)\b",
        "self-assessment; say what it does instead",
    ),
    (r"\bbest practices?\b", "vague; name the practice"),
    (r"\bas (?:previously )?mentioned\b", "filler"),
    (r"\bit(?:'s| is) (?:worth noting|important to note)\b", "filler; just say it"),
    (r"\bleverag(?:e|es|ing)\b", 'say "use"'),
    (r"\bensur(?:e|es|ing) that\b", "usually padding around a plain verb"),
    (r"\bdelve[sd]? into\b", "filler"),
]

ATTRIBUTION = [
    (r"generated with .{0,30}claude", "AI attribution"),
    (r"co-authored-by:\s*claude", "AI attribution"),
    (r"\U0001F916", "AI attribution"),
]

NOT_REPRODUCIBLE = re.compile(r"^\s*>?\s*not reproducible locally\s*:", re.I | re.M)

# A command that runs a test rather than the product. A reproduction made only
# of these tells the reviewer that a test the author also wrote now passes --
# not what a user saw go wrong, and not how to see it themselves.
TEST_INVOCATION = re.compile(
    r"""(?:
        \b(?:make|npm|yarn|pnpm|cargo|go|bazel|gradle|gradlew|dotnet|mix|rake|swift)
            \s+(?:run\s+)?(?:tests?|check)\b
      | \b(?:pytest|tox|rspec|jest|vitest|ctest|phpunit|nosetests)\b
      | \bpython3?\s+-m\s+(?:pytest|unittest)\b
      | --gtest[-_]filter
    )""",
    re.X | re.I,
)

# Getting to where the reproduction starts. Neither a test invocation nor a
# user-facing step, so these lines decide nothing either way.
SETUP_COMMAND = re.compile(
    r"^(?:cd|export|source|set|git|cmake|ninja|autoninja|gn|"
    r"(?:npm|yarn|pnpm)\s+(?:install|ci|run\s+(?:build|init|sync)))\b"
)
CLOSES_LINE = re.compile(r"^\s*(?:closes|fixes|resolves)\b", re.I)
QUALIFIED_CLOSES = re.compile(
    r"^\s*(?:closes|fixes|resolves)\s+[\w.-]+/[\w.-]+#\d+\s*$", re.I
)
FENCE = re.compile(r"^\s*(?:```|~~~)")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
NUMBERED_STEP = re.compile(r"^\s*(?:>\s*)?\d+[.)]\s+\S", re.M)
CHECKBOX = re.compile(r"^\s*[-*]\s*\[( |x|X)\]", re.M)
CI_CHECKBOX = re.compile(r"^\s*[-*]\s*\[ \]\s*CI passes cleanly\s*$", re.I | re.M)
CI_CHECKED = re.compile(r"^\s*[-*]\s*\[[xX]\]\s*CI passes cleanly\s*$", re.M)
DETAILS_BLOCK = re.compile(r"<details\b.*?</details>", re.S | re.I)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
INLINE_CODE = re.compile(r"`([^`\n]+)`")
# An inline-code span that is a symbol or a path rather than a word: contains
# ::, (), a slash with a dot, an underscore or a dot between identifier chars.
SYMBOLISH = re.compile(r"::|\(\)|->|\w/\w|\w\.\w|_")

# Budgets. Prose only -- code blocks, tables and <details> are not counted.
PROBLEM_WORD_BUDGET = 110
VISIBLE_PROSE_BUDGET = 400
VISIBLE_LINE_BUDGET = 60
SYMBOL_BUDGET = 3


def strip_fences(text):
    """Drop fenced code blocks, keeping line count stable is not needed here."""
    out, in_fence = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)


def fenced_lines(text):
    """The lines inside fenced code blocks -- the inverse of strip_fences."""
    out, in_fence = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            out.append(line)
    return out


def reproduction_lines(text):
    """The lines of a Reproduce section that claim to be the reproduction:
    commands inside fences, and numbered steps. Navigation, build and comment
    lines are dropped -- they are true of every reproduction."""
    candidates = fenced_lines(text) + [
        line for line in strip_fences(text).splitlines() if NUMBERED_STEP.match(line)
    ]
    out = []
    for line in candidates:
        line = line.strip().lstrip("$").strip()
        if not line or line.startswith("#"):
            continue
        if SETUP_COMMAND.match(line):
            continue
        out.append(line)
    return out


def split_sections(body):
    """Map heading text (lowercased) -> section body, plus the heading order."""
    sections, order, current, buf, in_fence = {}, [], None, [], False
    for line in body.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence else HEADING.match(line)
        if m:
            if current is not None:
                sections[current] = "\n".join(buf)
            current = m.group(2).strip().lower().rstrip(":")
            order.append(current)
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf)
    return sections, order


def find_section(sections, aliases):
    for name in aliases:
        if name in sections:
            return name, sections[name]
    # Tolerate decoration around the heading text ("📦 Test Plan", "Test Plan (all green)").
    for heading, text in sections.items():
        squashed = re.sub(r"[^a-z ]", "", heading).strip()
        if squashed in aliases:
            return heading, text
    return None, None


def words(text):
    return len(re.findall(r"\b[\w'-]+\b", text))


def check(body, require_closes=True, test_only_change=False):
    """Return (errors, warnings) as lists of strings."""
    errors, warnings = [], []
    body = body.replace("\r\n", "\n")
    if not body.strip():
        return ["body is empty"], []

    stripped = HTML_COMMENT.sub("", body)
    lines = [ln for ln in stripped.splitlines()]
    nonempty = [ln for ln in lines if ln.strip()]

    # ── The closing line ────────────────────────────────────────────────────
    closes_anywhere = [ln for ln in nonempty if CLOSES_LINE.match(ln)]
    if closes_anywhere:
        first = nonempty[0]
        if not CLOSES_LINE.match(first):
            errors.append(
                "the Closes line must be the very first line of the body, above "
                f"everything else (found {closes_anywhere[0].strip()!r} lower down)"
            )
        for ln in closes_anywhere:
            if not QUALIFIED_CLOSES.match(ln):
                errors.append(
                    f"{ln.strip()!r}: use the cross-repo form "
                    "'Closes <owner>/<repo>#<number>' -- a bare '#<n>' does not "
                    "close an issue in another repository"
                )
    elif require_closes:
        warnings.append(
            "no Closes line: if this PR resolves an issue, "
            "'Closes <owner>/<repo>#<number>' must be the first line"
        )

    # ── Sections, and their order ───────────────────────────────────────────
    sections, order = split_sections(stripped)
    found_at = {}
    for canonical, aliases in REQUIRED_SECTIONS:
        heading, text = find_section(sections, aliases)
        if heading is None:
            errors.append(
                f"missing section '## {canonical}' "
                "(see docs/pr-descriptions.md for the shape)"
            )
            continue
        found_at[canonical] = (heading, text, order.index(heading))
        if not text.strip():
            errors.append(f"section '## {heading}' is empty")

    positions = [(c, v[2]) for c, v in found_at.items()]
    expected = [c for c, _ in REQUIRED_SECTIONS if c in found_at]
    actual = [c for c, _ in sorted(positions, key=lambda p: p[1])]
    if actual != expected:
        errors.append(
            "sections are out of order: found "
            + " -> ".join(actual)
            + "; expected "
            + " -> ".join(expected)
        )

    # ── The reproduction ────────────────────────────────────────────────────
    if "Reproduce" in found_at:
        heading, text, _ = found_at["Reproduce"]
        has_fence = bool(FENCE.search(text)) if text else False
        has_steps = bool(NUMBERED_STEP.search(text or ""))
        has_excuse = bool(NOT_REPRODUCIBLE.search(text or ""))
        if not (has_fence or has_steps or has_excuse):
            errors.append(
                f"'## {heading}' has no paste-able reproduction: give a command in a "
                "code block, or numbered steps, or a line beginning "
                "'Not reproducible locally:' with the reason and a link to the evidence"
            )
        if has_excuse and not re.search(r"https?://|#\d+", text or ""):
            errors.append(
                "'Not reproducible locally:' must link the evidence "
                "(the CI job, the crash report, or the issue holding the logs)"
            )
        prose = strip_fences(text or "")
        if (has_fence or has_steps) and words(prose) < 6:
            warnings.append(
                f"'## {heading}' gives a command but not the outcome: say what happens "
                "today and what happens with this branch"
            )
        if not has_excuse and not test_only_change:
            claimed = reproduction_lines(text or "")
            if claimed and all(TEST_INVOCATION.search(ln) for ln in claimed):
                warnings.append(
                    f"'## {heading}' reproduces only by running a test: for anything a "
                    "user can see, give numbered steps in the running product with what "
                    "you observed and what you expected, and keep the test as a line "
                    "under them. Pass --test-only-change if the diff touches only tests"
                )

    # ── The problem ─────────────────────────────────────────────────────────
    if "The problem" in found_at:
        heading, text, _ = found_at["The problem"]
        prose = strip_fences(DETAILS_BLOCK.sub("", text or ""))
        n = words(prose)
        if n > PROBLEM_WORD_BUDGET:
            warnings.append(
                f"'## {heading}' is {n} words (budget {PROBLEM_WORD_BUDGET}): state the "
                "symptom in 2-4 sentences and move the mechanism to '## The fix' "
                "or into <details>"
            )
        symbols = [c for c in INLINE_CODE.findall(prose) if SYMBOLISH.search(c)]
        if len(symbols) > SYMBOL_BUDGET:
            warnings.append(
                f"'## {heading}' names {len(symbols)} symbols or paths "
                f"({', '.join(symbols[:4])}...): open with what a person observes, "
                "not with identifiers"
            )

    # ── The test plan ───────────────────────────────────────────────────────
    if "Test plan" in found_at:
        heading, text, _ = found_at["Test plan"]
        if not CHECKBOX.search(text or ""):
            errors.append(
                f"'## {heading}' has no checkboxes: one per command actually run, "
                "with its result"
            )
        if CI_CHECKED.search(text or ""):
            errors.append(
                "'CI passes cleanly' is checked: it cannot be, the PR does not exist yet"
            )
        elif not CI_CHECKBOX.search(text or ""):
            warnings.append(
                f"'## {heading}' should end with an unchecked '- [ ] CI passes cleanly'"
            )

    # ── Filler, attribution, length ─────────────────────────────────────────
    visible = DETAILS_BLOCK.sub("", stripped)
    for pattern, why in ATTRIBUTION:
        if re.search(pattern, visible, re.I):
            errors.append(f"remove the {why}: it is forbidden in PR bodies here")
    for pattern, why in SLOP_PHRASES:
        m = re.search(pattern, strip_fences(visible), re.I)
        if m:
            warnings.append(f"{m.group(0)!r}: {why}")

    emoji_heading = [h for h in order if re.search(r"[\U0001F300-\U0001FAFF✅❌✨]", h)]
    if emoji_heading:
        warnings.append(f"emoji in heading {emoji_heading[0]!r}: drop the decoration")

    testplan_text = found_at.get("Test plan", (None, "", 0))[1] or ""
    above = visible.split(testplan_text)[0] if testplan_text else visible
    prose_above = strip_fences(above)
    n = words(prose_above)
    if n > VISIBLE_PROSE_BUDGET:
        warnings.append(
            f"{n} words of prose before the test plan (budget {VISIBLE_PROSE_BUDGET}): "
            "a body longer than a screen gets read by nobody -- move the depth "
            "into <details>"
        )
    visible_lines = len([ln for ln in visible.splitlines() if ln.strip()])
    if visible_lines > VISIBLE_LINE_BUDGET:
        warnings.append(
            f"{visible_lines} visible lines (budget {VISIBLE_LINE_BUDGET}): "
            "collapse the supporting detail into <details>"
        )

    return errors, warnings


def fetch_body(pr, repo):
    cmd = ["gh", "pr", "view", str(pr), "--json", "body", "-q", ".body"]
    if repo:
        cmd += ["--repo", repo]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(2)
    return result.stdout


def main():
    ap = argparse.ArgumentParser(
        description="Check a PR body against docs/pr-descriptions.md."
    )
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--body-file", help="file holding the PR body (markdown)")
    src.add_argument("--pr", help="PR number to fetch with gh")
    ap.add_argument("--repo", help="owner/repo for --pr")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    ap.add_argument(
        "--no-closes",
        action="store_true",
        help="this PR closes no issue; do not ask for a Closes line",
    )
    ap.add_argument(
        "--test-only-change",
        action="store_true",
        help="the diff touches only tests, so the test invocation is the whole "
        "reproduction; do not ask for user-facing steps",
    )
    args = ap.parse_args()

    if args.pr:
        body = fetch_body(args.pr, args.repo)
    elif args.body_file:
        with open(args.body_file, encoding="utf-8") as fh:
            body = fh.read()
    else:
        body = sys.stdin.read()

    errors, warnings = check(
        body,
        require_closes=not args.no_closes,
        test_only_change=args.test_only_change,
    )

    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"error: {e}")

    if errors or (args.strict and warnings):
        print(
            f"\n{len(errors)} error(s), {len(warnings)} warning(s). "
            "Fix the body and re-run; see docs/pr-descriptions.md."
        )
        return 1
    if warnings:
        print(f"\nOK with {len(warnings)} warning(s). Read them before you push.")
    else:
        print("OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
