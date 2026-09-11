"""Tests for check-pr-body.py.

The checker is the enforcement half of docs/pr-descriptions.md, so these pin
the rules a reviewer actually cares about: a reproduction exists, the problem
comes first, the Closes line will really close the issue.
"""

import importlib.util
import os
import subprocess
import sys

import pytest

SCRIPT = os.path.join(
    os.path.dirname(__file__), os.pardir, "scripts", "check-pr-body.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("check_pr_body", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def checker():
    return _load()


GOOD = """Closes brave/bravebot#158

## The problem
A model that takes more than 60 seconds to start answering never answers at
all. The request fails as a timeout and each retry dies at the same 60
seconds, though the configured limit for a reply is 600 seconds.

## Reproduce
```sh
cargo test -p bravebot-net a_reply_that_takes_longer_than_the_send_bound
```
Fails on the parent commit with `timeout: send request`; passes here.

## The fix
Our send timeout was also bounding the wait for the reply, because ureq clamps
each phase to the shortest deadline set on any earlier phase. Each value now
accounts for every phase it bounds.

## Test plan
- [x] `make check` - passed
- [ ] CI passes cleanly
"""


def body_without(section):
    """GOOD with one section's heading and content removed."""
    out, skipping = [], False
    for line in GOOD.splitlines():
        if line.startswith("## "):
            skipping = line[3:].strip().lower() == section.lower()
        if not skipping:
            out.append(line)
    return "\n".join(out)


# ── The happy path ───────────────────────────────────────────────────────────


def test_good_body_passes(checker):
    errors, warnings = checker.check(GOOD)
    assert errors == []
    assert warnings == []


def test_empty_body_is_an_error(checker):
    errors, _ = checker.check("")
    assert errors


# ── Sections ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "section", ["The problem", "Reproduce", "The fix", "Test plan"]
)
def test_each_required_section_is_required(checker, section):
    errors, _ = checker.check(body_without(section))
    assert any(section.lower() in e.lower() for e in errors)


def test_sections_out_of_order(checker):
    scrambled = (
        GOOD.replace("## Reproduce", "## TMP")
        .replace("## The fix", "## Reproduce")
        .replace("## TMP", "## The fix")
    )
    errors, _ = checker.check(scrambled)
    assert any("out of order" in e for e in errors)


def test_old_template_headings_report_the_real_gap(checker):
    """A body on the previous template is missing a reproduction, not four sections."""
    old = (
        body_without("Reproduce")
        .replace("## The problem", "## Summary")
        .replace("## The fix", "## Fix")
    )
    errors, _ = checker.check(old)
    assert len(errors) == 1
    assert "Reproduce" in errors[0]


def test_empty_section_is_an_error(checker):
    hollow = "\n".join(
        line for line in GOOD.splitlines() if not line.startswith("Our send timeout")
    )
    hollow = "\n".join(
        line
        for line in hollow.splitlines()
        if not line.startswith(("each phase to the", "accounts for every"))
    )
    errors, _ = checker.check(hollow)
    assert any("empty" in e for e in errors)


def test_decorated_heading_still_matches(checker):
    errors, warnings = checker.check(GOOD.replace("## Test plan", "## ✅ Test Plan"))
    assert errors == []
    assert any("emoji" in w for w in warnings)


# ── The reproduction ─────────────────────────────────────────────────────────


def test_prose_only_reproduction_is_rejected(checker):
    body = GOOD.replace(
        "```sh\ncargo test -p bravebot-net a_reply_that_takes_longer_than_the_send_bound\n```\n"
        "Fails on the parent commit with `timeout: send request`; passes here.",
        "Run the net tests and you will see the timeout.",
    )
    errors, _ = checker.check(body)
    assert any("paste-able" in e for e in errors)


def test_numbered_steps_are_a_reproduction(checker):
    body = GOOD.replace(
        "```sh\ncargo test -p bravebot-net a_reply_that_takes_longer_than_the_send_bound\n```",
        "1. Open the settings pane.\n2. Pick a slow model.\n3. Send any prompt.",
    )
    errors, _ = checker.check(body)
    assert errors == []


def test_not_reproducible_locally_needs_evidence(checker):
    excuse = "Not reproducible locally: ASAN-only, under builder memory pressure."
    body = GOOD.replace(
        "```sh\ncargo test -p bravebot-net a_reply_that_takes_longer_than_the_send_bound\n```\n"
        "Fails on the parent commit with `timeout: send request`; passes here.",
        excuse,
    )
    errors, _ = checker.check(body)
    assert any("evidence" in e for e in errors)

    with_link = body.replace(excuse, excuse + " See https://ci.example.com/job/44.")
    errors, _ = checker.check(with_link)
    assert errors == []


def test_command_without_an_outcome_warns(checker):
    body = GOOD.replace(
        "Fails on the parent commit with `timeout: send request`; passes here.", ""
    )
    _, warnings = checker.check(body)
    assert any("outcome" in w for w in warnings)


# ── The Closes line ──────────────────────────────────────────────────────────


def test_closes_must_be_first(checker):
    body = (
        GOOD.replace("Closes brave/bravebot#158\n\n", "")
        + "\nCloses brave/bravebot#158\n"
    )
    errors, _ = checker.check(body)
    assert any("first line" in e for e in errors)


def test_bare_closes_is_rejected(checker):
    errors, _ = checker.check(GOOD.replace("Closes brave/bravebot#158", "Closes #158"))
    assert any("cross-repo" in e for e in errors)


def test_missing_closes_only_warns(checker):
    errors, warnings = checker.check(GOOD.replace("Closes brave/bravebot#158\n\n", ""))
    assert errors == []
    assert any("Closes" in w for w in warnings)


def test_no_closes_flag_silences_the_warning(checker):
    body = GOOD.replace("Closes brave/bravebot#158\n\n", "")
    errors, warnings = checker.check(body, require_closes=False)
    assert errors == []
    assert warnings == []


# ── The test plan ────────────────────────────────────────────────────────────


def test_checked_ci_box_is_an_error(checker):
    errors, _ = checker.check(
        GOOD.replace("- [ ] CI passes cleanly", "- [x] CI passes cleanly")
    )
    assert any("CI passes cleanly" in e for e in errors)


def test_test_plan_without_checkboxes(checker):
    body = GOOD.replace(
        "- [x] `make check` - passed\n- [ ] CI passes cleanly",
        "Ran everything, all green.",
    )
    errors, _ = checker.check(body)
    assert any("checkbox" in e for e in errors)


# ── Filler and attribution ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "phrase",
    [
        "This PR adds a fix.",
        "We leverage the retry loop.",
        "A robust solution.",
        "It properly handles the case.",
        "This significantly improves latency.",
    ],
)
def test_slop_phrases_warn(checker, phrase):
    _, warnings = checker.check(GOOD.replace("## The fix\n", f"## The fix\n{phrase}\n"))
    assert warnings, f"expected a warning for {phrase!r}"


@pytest.mark.parametrize(
    "line", ["Generated with Claude Code", "Co-Authored-By: Claude <x@y.z>"]
)
def test_attribution_is_an_error(checker, line):
    errors, _ = checker.check(GOOD + f"\n{line}\n")
    assert any("attribution" in e for e in errors)


def test_slop_inside_a_code_block_is_not_flagged(checker):
    body = GOOD.replace(
        "## Test plan", "## Notes\n```\nthis PR adds nothing\n```\n\n## Test plan"
    )
    _, warnings = checker.check(body)
    assert not any("preamble" in w for w in warnings)


# ── Length ───────────────────────────────────────────────────────────────────


def test_long_problem_section_warns(checker):
    padded = GOOD.replace(
        "## The problem\n", "## The problem\n" + ("filler words here. " * 60) + "\n"
    )
    _, warnings = checker.check(padded)
    assert any("budget" in w for w in warnings)


def test_details_block_is_not_counted(checker):
    buried = GOOD.replace(
        "## Test plan",
        "<details><summary>Depth</summary>\n\n"
        + ("word " * 700)
        + "\n\n</details>\n\n## Test plan",
    )
    errors, warnings = checker.check(buried)
    assert errors == []
    assert warnings == []


# ── The CLI ──────────────────────────────────────────────────────────────────


def run_cli(tmp_path, body, *args):
    path = tmp_path / "body.md"
    path.write_text(body, encoding="utf-8")
    return subprocess.run(
        [sys.executable, SCRIPT, "--body-file", str(path), *args],
        capture_output=True,
        text=True,
    )


def test_cli_exit_codes(tmp_path):
    assert run_cli(tmp_path, GOOD).returncode == 0
    assert run_cli(tmp_path, body_without("Reproduce")).returncode == 1


def test_cli_strict_fails_on_warnings(tmp_path):
    warn_only = GOOD.replace(
        "## The fix\n", "## The fix\nWe leverage the retry loop.\n"
    )
    assert run_cli(tmp_path, warn_only).returncode == 0
    assert run_cli(tmp_path, warn_only, "--strict").returncode == 1


def test_cli_reads_stdin():
    result = subprocess.run(
        [sys.executable, SCRIPT], input=GOOD, capture_output=True, text=True
    )
    assert result.returncode == 0


# ── The docs and the checker must agree ──────────────────────────────────────

DOCS_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "docs")


def _doc(name):
    with open(os.path.join(DOCS_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def test_the_shipped_template_passes_its_own_checker(checker):
    """The body the agent copies out of workflow-committed.md must satisfy the
    checker it is told to run. Otherwise the first thing the agent meets is a
    failure in the template it was handed."""
    doc = _doc("workflow-committed.md")
    start = doc.index("cat > /tmp/pr-body-<story-id>.md <<'EOF'\n") + len(
        "cat > /tmp/pr-body-<story-id>.md <<'EOF'\n"
    )
    template = doc[start : doc.index("\nEOF", start)]

    # Fill the placeholders a real body would fill, and nothing else.
    body = template.replace("$ISSUE_REPO#<issue-number>", "owner/repo#1").replace(
        "[the exact command, and the directory it runs in if not the repo root]",
        "make test TEST=Foo.Bar",
    )
    errors, warnings = checker.check(body)
    assert errors == [], errors
    assert warnings == [], warnings


def test_the_spec_doc_names_the_sections_the_checker_requires(checker):
    """docs/pr-descriptions.md is what the agent reads; the checker is what
    gates it. A rename in one without the other is a silent contradiction."""
    doc = _doc("pr-descriptions.md")
    for canonical, _ in checker.REQUIRED_SECTIONS:
        assert f"## {canonical}" in doc, f"spec doc never shows '## {canonical}'"


def test_the_spec_doc_is_pointed_at_from_the_pr_creating_workflow():
    assert "pr-descriptions.md" in _doc("workflow-committed.md")
    assert "check-pr-body.py" in _doc("workflow-committed.md")
