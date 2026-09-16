"""Tests for check-pr-body.py.

The checker is the enforcement half of docs/pr-descriptions.md, so these pin
the rules a reviewer actually cares about: a reproduction exists, the problem
comes first, the Closes line will really close the issue.
"""

import importlib.util
import json
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

User impact: a reply that takes 75 seconds now arrives instead of failing.

## The problem
A model that takes more than 60 seconds to start answering never answers at
all. The request fails as a timeout and each retry dies at the same 60
seconds, though the configured limit for a reply is 600 seconds.

## Reproduce
1. Configure a model that takes over a minute to produce its first token.
2. Leave the reply timeout at its 600 second default and send any prompt.

The request fails at 60 seconds with `timeout: send request` and is retried,
each retry dying at the same 60 seconds. Expected: the reply at 75 seconds.

`cargo test -p bravebot-net a_reply_that_takes_longer_than_the_send_bound`
fails on the parent commit and passes here.

## The fix
Our send timeout was also bounding the wait for the reply, because ureq clamps
each phase to the shortest deadline set on any earlier phase. Each value now
accounts for every phase it bounds.

## Test plan
- [x] `make check` - passed
- [ ] CI passes cleanly
"""


def with_reproduce(text):
    """GOOD with the body of its Reproduce section replaced by `text`."""
    out, skipping = [], False
    for line in GOOD.splitlines():
        if line.startswith("## "):
            skipping = line[3:].strip().lower() == "reproduce"
            out.append(line)
            if skipping:
                out.append(text)
                out.append("")
            continue
        if not skipping:
            out.append(line)
    return "\n".join(out)


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
    body = with_reproduce("Run the net tests and you will see the timeout.")
    errors, _ = checker.check(body)
    assert any("paste-able" in e for e in errors)


def test_numbered_steps_are_a_reproduction(checker):
    body = with_reproduce(
        "1. Open the settings pane.\n2. Pick a slow model.\n3. Send any prompt.\n"
        "\nNo answer ever arrives; expected one inside the configured limit."
    )
    errors, warnings = checker.check(body)
    assert errors == []
    assert warnings == []


def test_not_reproducible_locally_needs_evidence(checker):
    excuse = "Not reproducible locally: ASAN-only, under builder memory pressure."
    body = with_reproduce(excuse)
    errors, _ = checker.check(body)
    assert any("evidence" in e for e in errors)

    with_link = body.replace(excuse, excuse + " See https://ci.example.com/job/44.")
    errors, _ = checker.check(with_link)
    assert errors == []


def test_command_without_an_outcome_warns(checker):
    body = with_reproduce("```sh\ncurl -sv http://localhost:8080/v1/chat\n```")
    _, warnings = checker.check(body)
    assert any("outcome" in w for w in warnings)


# ── A test is not a reproduction ─────────────────────────────────────────────


TEST_ONLY_REPRO = """```sh
make test TEST=SlowReply.FirstByteAfterSendBound
```
Fails on the parent commit with `timeout: send request`; passes here."""


def test_reproducing_only_by_running_a_test_warns(checker):
    errors, warnings = checker.check(with_reproduce(TEST_ONLY_REPRO))
    assert errors == []
    assert any("only by running a test" in w for w in warnings)


def test_test_only_change_silences_it(checker):
    errors, warnings = checker.check(
        with_reproduce(TEST_ONLY_REPRO), test_only_change=True
    )
    assert errors == []
    assert warnings == []


@pytest.mark.parametrize(
    "command",
    [
        "make test TEST=Foo.Bar",
        "make check",
        "npm run test -- brave_unit_tests --filter=Foo.Bar",
        "cargo test -p bravebot-net a_reply",
        "pytest tests/test_pr_body.py",
        "python3 -m pytest tests/",
        "out/Release/brave_unit_tests --gtest_filter=Foo.Bar",
        "bazel test //net:all",
    ],
)
def test_every_shape_of_test_invocation_is_recognised(checker, command):
    body = with_reproduce(f"```sh\n{command}\n```\nFails on the parent commit.")
    _, warnings = checker.check(body)
    assert any("only by running a test" in w for w in warnings), command


def test_a_product_command_is_not_a_test_invocation(checker):
    body = with_reproduce(
        "```sh\ncd bravebot\n./target/debug/bravebot ask 'hello'\n```\n"
        "Hangs for 60 seconds, then reports a timeout. Expected an answer."
    )
    errors, warnings = checker.check(body)
    assert errors == []
    assert warnings == []


def test_a_step_that_only_runs_a_test_still_warns(checker):
    """Numbering a test command does not make it a user-facing reproduction."""
    body = with_reproduce(
        "1. Run `make test TEST=SlowReply.FirstByteAfterSendBound`.\n"
        "\nIt fails on the parent commit and passes here."
    )
    _, warnings = checker.check(body)
    assert any("only by running a test" in w for w in warnings)


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


# ── What a user sees ─────────────────────────────────────────────────────────

IMPACT = "User impact: a reply that takes 75 seconds now arrives instead of failing."


def test_the_impact_line_is_required(checker):
    errors, _ = checker.check(GOOD.replace(IMPACT + "\n\n", ""))
    assert any("User impact" in e for e in errors)


def test_an_impact_line_under_a_heading_is_an_error(checker):
    """Below the problem it is not the first thing read, which is its whole job."""
    body = GOOD.replace(IMPACT + "\n\n", "").replace(
        "## The problem\n", f"## The problem\n{IMPACT}\n"
    )
    errors, _ = checker.check(body)
    assert any("under a heading" in e for e in errors), errors


def test_an_empty_impact_line_is_an_error(checker):
    errors, _ = checker.check(GOOD.replace(IMPACT, "User impact:"))
    assert any("says nothing after the colon" in e for e in errors), errors


def test_a_bare_none_warns(checker):
    """A bare "none" does not say whether this is a spec, a refactor or a test."""
    errors, warnings = checker.check(GOOD.replace(IMPACT, "User impact: none."))
    assert errors == []
    assert any("why nothing changes" in w for w in warnings), warnings


def test_none_with_a_reason_passes(checker):
    body = GOOD.replace(
        IMPACT, "User impact: none -- a spec document, no code changes."
    )
    errors, warnings = checker.check(body)
    assert errors == []
    assert warnings == []


@pytest.mark.parametrize(
    "line",
    [
        "**User impact:** the reply now arrives.",
        "**User impact**: the reply now arrives.",
        "user impact: the reply now arrives.",
    ],
)
def test_the_label_tolerates_emphasis_and_case(checker, line):
    errors, warnings = checker.check(GOOD.replace(IMPACT, line))
    assert errors == []
    assert warnings == []


def test_an_impact_line_inside_an_example_does_not_count(checker):
    """A body quoting the rule has not stated its own impact."""
    body = GOOD.replace(IMPACT + "\n\n", "").replace(
        "## The fix\n", f"## The fix\n```\n{IMPACT}\n```\n"
    )
    errors, _ = checker.check(body)
    assert any("no 'User impact:' line" in e for e in errors), errors


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


def test_a_details_block_below_the_test_plan_does_not_count_the_plan_twice(checker):
    """The budget is about the prose above the test plan. Measuring it by cutting
    the plan's text out of a string the plan is no longer in counted every line
    of it as prose, so a body with a closing <details> was over budget for
    having a test plan."""
    body = GOOD + "\n<details><summary>Depth</summary>\n\nSome depth.\n\n</details>\n"
    _, warnings = checker.check(body)
    assert not any("words of prose" in w for w in warnings), warnings


def test_an_example_block_costs_nothing_against_the_line_budget(checker):
    """Pasting the screen or the file a change produces is the point of the
    section. Charging it against the length budget is what pushes an author into
    describing the artifact instead of showing it."""
    screen = "\n".join(f"  line {n} of the prompt" for n in range(30))
    body = GOOD.replace("## Test plan", f"```\n{screen}\n```\n\n## Test plan")
    errors, warnings = checker.check(body)
    assert errors == []
    assert warnings == []


def test_an_enormous_example_block_warns(checker):
    body = GOOD.replace(
        "## Test plan",
        "```\n" + "\n".join(f"line {n}" for n in range(60)) + "\n```\n\n## Test plan",
    )
    _, warnings = checker.check(body)
    assert any("code block" in w for w in warnings), warnings


# ── Shorthand ────────────────────────────────────────────────────────────────


def test_a_bare_clause_id_is_an_error(checker):
    body = GOOD.replace(
        "## The fix\n", "## The fix\nRUN-19 says the answer may outlive the session.\n"
    )
    errors, _ = checker.check(body)
    assert any("RUN-19" in e for e in errors), errors


def test_a_linked_clause_id_is_accepted(checker):
    body = GOOD.replace(
        "## The fix\n",
        "## The fix\nThe rule it builds is "
        "[RUN-19](https://github.com/o/r/blob/main/docs/specs/tools/run.md#RUN-19).\n",
    )
    errors, warnings = checker.check(body)
    assert errors == []
    assert warnings == []


def test_a_bare_id_inside_details_only_warns(checker):
    body = GOOD.replace(
        "## Test plan",
        "<details><summary>Depth</summary>\n\nRUN-19 governs.\n\n</details>\n\n## Test plan",
    )
    errors, warnings = checker.check(body)
    assert errors == []
    assert any("RUN-19" in w for w in warnings), warnings


@pytest.mark.parametrize(
    "text",
    [
        "The token is UTF-8 and the digest SHA-256.",
        "The hole is CVE-2025-1234, and RFC-7231 says what to send.",
        "Built against MSRV-1.74 on ARM-64.",
    ],
)
def test_a_published_standard_is_not_shorthand(checker, text):
    body = GOOD.replace("## The fix\n", f"## The fix\n{text}\n")
    errors, warnings = checker.check(body)
    assert errors == []
    assert not any("shorthand" in w for w in warnings), warnings


def test_an_id_in_the_test_plan_is_left_alone(checker):
    """A test name or a job id in the plan is a thing to run, not a citation."""
    body = GOOD.replace(
        "- [ ] CI passes cleanly",
        "- [x] `ctest -R SPEC-19` - passed\n- [ ] CI passes cleanly",
    )
    errors, _ = checker.check(body)
    assert errors == []


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


# ── A change a person can look at shows what they would see ──────────────────

SCREEN = """```
trusting /tmp/work

╭─────────────────────────────────╮
│> Ask Brave Bot to do anything   │
╰─────────────────────────────────╯
  ? for shortcuts
```
"""

TUI = ("crates/tui/src/app.rs",)


def with_screen(body, block=SCREEN):
    return body.replace("## The fix\n", f"## The fix\n{block}\n")


def test_a_ui_change_with_no_screen_is_an_error(checker):
    """The whole point: a reviewer of an interface change should not have to
    build the branch to find out what it looks like."""
    errors, _ = checker.check(GOOD, ui_paths_changed=TUI)
    assert any("shows no screen" in e for e in errors), errors


def test_the_error_names_the_files_that_asked_for_it(checker):
    """So the answer to 'why is this being demanded of me' is in the message."""
    errors, _ = checker.check(GOOD, ui_paths_changed=TUI)
    assert any("crates/tui/src/app.rs" in e for e in errors), errors


def test_a_long_list_of_files_is_summarised(checker):
    """Naming forty paths in an error message is naming none of them."""
    many = tuple(f"crates/tui/src/f{n}.rs" for n in range(8))
    errors, _ = checker.check(GOOD, ui_paths_changed=many)
    assert any("and 5 more" in e for e in errors), errors


def test_a_ui_change_that_shows_a_screen_passes(checker):
    errors, warnings = checker.check(with_screen(GOOD), ui_paths_changed=TUI)
    assert errors == [], errors
    assert warnings == [], warnings


def test_a_change_nobody_can_look_at_asks_for_no_screen(checker):
    """Most diffs are not interface diffs, and a rule that fires on all of them
    would be turned off within a week."""
    assert checker.check(GOOD, ui_paths_changed=()) == ([], [])


def test_a_one_line_command_block_is_not_a_screen(checker):
    """A reproduction almost always carries a fenced command, so counting any
    fence would make this rule already satisfied on the bodies it is for."""
    errors, _ = checker.check(
        with_screen(GOOD, "```\ncargo test --all\n```\n"), ui_paths_changed=TUI
    )
    assert any("shows no screen" in e for e in errors), errors


def test_a_screen_below_the_test_plan_does_not_count(checker):
    """A reviewer's thirty seconds are spent above it. A screen underneath is a
    screen they will not see."""
    below = GOOD.replace(
        "- [ ] CI passes cleanly\n", f"- [ ] CI passes cleanly\n\n{SCREEN}"
    )
    errors, _ = checker.check(below, ui_paths_changed=TUI)
    assert any("shows no screen" in e for e in errors), errors


def test_a_bare_pattern_claims_everything_beneath_it(checker):
    """So a profile can write `crates/tui/` and not `crates/tui/**/*`."""
    changed = ["crates/tui/src/app.rs", "crates/core/src/lib.rs", "README.md"]
    assert checker.matching(changed, ["crates/tui/"]) == ["crates/tui/src/app.rs"]


def test_a_glob_pattern_is_matched_as_a_glob(checker):
    changed = ["ui/main.css", "ui/main.rs", "docs/ui.css"]
    assert checker.matching(changed, ["ui/*.css"]) == ["ui/main.css"]


def test_a_path_claimed_twice_is_reported_once(checker):
    assert checker.matching(["ui/a.rs"], ["ui/", "ui/*.rs"]) == ["ui/a.rs"]


def test_a_project_with_no_declared_paths_is_never_asked(checker):
    """A project with no interface has nothing to show, and guessing which of
    its directories counts would fire the rule on the wrong diffs."""
    assert checker.matching(["crates/tui/src/app.rs"], []) == []


def test_the_bravebot_profile_declares_its_interface():
    """The rule is inert until a profile names the paths, so the one project
    with an interface has to name it or nothing above is reachable."""
    path = os.path.join(
        os.path.dirname(__file__), os.pardir, "projects", "bravebot", "profile.json"
    )
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh)["uiPaths"] == ["crates/tui/"]


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


def _repo_with_change(tmp_path, changed):
    """A one-commit repo whose HEAD changes `changed`, and the base to diff it
    against. The rule is decided from a real diff, so a real one is what pins
    it -- a stubbed path list would test the wiring and not the reading."""
    repo = tmp_path / "repo"
    (repo / os.path.dirname(changed)).mkdir(parents=True)
    git = ["git", "-C", str(repo)]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(git + ["config", "user.email", "t@t"], check=True)
    subprocess.run(git + ["config", "user.name", "t"], check=True)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-qm", "base"], check=True)
    base = subprocess.run(
        git + ["rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    (repo / changed).write_text("changed\n", encoding="utf-8")
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-qm", "change"], check=True)
    return repo, base


def _run_in(repo, tmp_path, body, *args, profile="bravebot"):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"project": {"name": "bravebot", "profile": profile}}),
        encoding="utf-8",
    )
    body_file = tmp_path / "body.md"
    body_file.write_text(body, encoding="utf-8")
    return subprocess.run(
        [sys.executable, os.path.abspath(SCRIPT), "--body-file", str(body_file), *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env={**os.environ, "BOT_CONFIG_FILE": str(config)},
    )


def test_the_cli_reads_the_changed_paths_from_the_diff(tmp_path):
    """Not from the body: there is no flag or phrasing that opts a run out."""
    repo, base = _repo_with_change(tmp_path, "crates/tui/src/app.rs")
    done = _run_in(repo, tmp_path, GOOD, "--diff-base", base)
    assert done.returncode == 1
    assert "shows no screen" in done.stdout


def test_the_cli_says_what_it_decided(tmp_path):
    """A rule nobody can see fire is a rule nobody trusts."""
    repo, base = _repo_with_change(tmp_path, "crates/tui/src/app.rs")
    done = _run_in(repo, tmp_path, with_screen(GOOD), "--diff-base", base)
    assert done.returncode == 0, done.stdout
    assert "has to show a screen" in done.stdout


def test_the_cli_passes_a_change_outside_the_interface(tmp_path):
    repo, base = _repo_with_change(tmp_path, "crates/core/src/lib.rs")
    done = _run_in(repo, tmp_path, GOOD, "--diff-base", base)
    assert done.returncode == 0, done.stdout
    assert "No interface file changed" in done.stdout


def test_the_cli_says_so_when_the_project_declares_no_interface(tmp_path):
    repo, base = _repo_with_change(tmp_path, "crates/tui/src/app.rs")
    done = _run_in(repo, tmp_path, GOOD, "--diff-base", base, profile="default")
    assert done.returncode == 0, done.stdout
    assert "no uiPaths" in done.stdout


def test_an_unresolvable_base_stops_rather_than_passing_quietly(tmp_path):
    """Silently skipping the rule when the ref is wrong is how it would come to
    be skipped every time."""
    repo, _ = _repo_with_change(tmp_path, "crates/tui/src/app.rs")
    done = _run_in(repo, tmp_path, GOOD, "--diff-base", "no/such/ref")
    assert done.returncode == 2
    assert "cannot diff against" in done.stdout


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
    body = (
        template.replace("$ISSUE_REPO#<issue-number>", "owner/repo#1")
        .replace(
            "1. [what a person does first in the running product — the screen or URL, the\n"
            "   setting, the input; a command exactly as typed, with its directory]",
            "1. Open the settings pane and pick a slow model.",
        )
        .replace("2. [the step that shows the bug]", "2. Send any prompt.")
    )
    errors, warnings = checker.check(body)
    assert errors == [], errors
    assert warnings == [], warnings
    assert "1. Open the settings pane" in body, "step placeholder no longer matches"


def test_the_template_alone_cannot_satisfy_the_screen_rule(checker):
    """Deliberate, and the one place the template is allowed to fail its own
    checker: the screen has to come from a real run. A fenced placeholder here
    would let a body pass showing a screen the product never drew."""
    doc = _doc("workflow-committed.md")
    start = doc.index("cat > /tmp/pr-body-<story-id>.md <<'EOF'\n")
    template = doc[start : doc.index("\nEOF", start)]
    errors, _ = checker.check(template, ui_paths_changed=TUI)
    assert any("shows no screen" in e for e in errors), errors


def test_the_shipped_template_leads_with_steps_not_a_test(checker):
    """The template is what the bot copies. If its Reproduce block is a bare
    test command again, every PR it produces loses the human reproduction."""
    doc = _doc("workflow-committed.md")
    block = doc[doc.index("## Reproduce", doc.index("cat > /tmp/pr-body")) :]
    block = block[: block.index("## The fix")]
    assert checker.NUMBERED_STEP.search(block), block
    assert not checker.TEST_INVOCATION.search(block), block


def test_the_spec_doc_names_the_sections_the_checker_requires(checker):
    """docs/pr-descriptions.md is what the agent reads; the checker is what
    gates it. A rename in one without the other is a silent contradiction."""
    doc = _doc("pr-descriptions.md")
    for canonical, _ in checker.REQUIRED_SECTIONS:
        assert f"## {canonical}" in doc, f"spec doc never shows '## {canonical}'"


def test_the_spec_doc_teaches_the_rules_the_checker_errors_on(checker):
    """A body rejected for shorthand sends its author to this doc. If the rule is
    not in it, the author has an error and nowhere to read what to do instead."""
    doc = _doc("pr-descriptions.md")
    assert "shorthand" in doc, "the doc never states the bare-id rule"
    assert "Show it" in doc, "the doc never asks for the artifact"
    assert "User impact:" in doc, "the doc never shows the user-impact line"


def test_the_spec_doc_is_pointed_at_from_the_pr_creating_workflow():
    assert "pr-descriptions.md" in _doc("workflow-committed.md")
    assert "check-pr-body.py" in _doc("workflow-committed.md")


def test_the_spec_doc_says_how_to_capture_a_terminal_screen():
    """The error message sends the author to this heading. A rule with nowhere
    to read the recipe is a rule that gets satisfied by a made-up screen."""
    doc = _doc("pr-descriptions.md")
    assert "## Showing a terminal screen" in doc.replace("###", "##")
    assert "terminal-screenshot.py" in doc
    assert "--cols" in doc, "the doc never says the size has to match the capture"


def test_the_error_message_names_a_heading_the_doc_has():
    """Written out separately because a heading rename is exactly the change
    that would leave the error pointing at nothing."""
    named = "Showing a terminal screen"
    mod = _load()
    errors, _ = mod.check(GOOD, ui_paths_changed=TUI)
    assert any(named in e for e in errors), errors
    assert f"### {named}" in _doc("pr-descriptions.md")


def test_the_workflow_tells_the_agent_to_pass_the_diff_base():
    """Without the flag the checker cannot see the diff and every body passes,
    so the template omitting it would turn the rule off everywhere."""
    assert "--diff-base" in _doc("workflow-committed.md")
    assert "--diff-base" in _doc("git-repository.md")


def test_both_shipped_templates_hold_a_slot_for_the_screen():
    """The bot fills a template rather than reading the doc, so a slot missing
    from either copy is a body with no screen and an error the agent then has
    to work out how to fix."""
    for name in ("workflow-committed.md", "git-repository.md"):
        assert "terminal-screenshot.py" in _doc(name), name


def test_the_capture_recipe_is_in_the_projects_own_doc():
    """`terminal-screenshot.py` replays a capture; it cannot make one. What
    drives the interface is the project's, and the profile doc is where the
    workflow says to look for it."""
    path = os.path.join(
        DOCS_DIR, os.pardir, "projects", "bravebot", "docs", "testing.md"
    )
    with open(path, encoding="utf-8") as fh:
        doc = fh.read()
    assert "drive_tui.py" in doc
    assert "--raw" in doc, "the recipe must take the untouched capture"
    assert "terminal-screenshot.py" in doc
