"""Tests for check-untranslated.py.

The check exists because a branch reached review with eight English sentences
written into the Rust source, printed inside labels the catalog did translate, so
a French reader got half a sentence in their own language. Nothing mechanical
looked at the source for prose. These pin the two halves that decide whether it
is worth reading: that the string which got through is found, and that a command
line, a path and a test fixture are not.
"""

import importlib.util
import json
import os
import subprocess
import sys

import pytest

SCRIPT = os.path.join(
    os.path.dirname(__file__), os.pardir, "scripts", "check-untranslated.py"
)

BRAVEBOT = {
    "paths": ["crates/*/src/*.rs"],
    "exempt": ["crates/i18n/*"],
    "catalog": "crates/i18n/locales/en-US.ftl",
    "how": "put it in the catalog",
}


@pytest.fixture(scope="module")
def checker():
    spec = importlib.util.spec_from_file_location("check_untranslated", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── What counts as a sentence ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "is not a rule; a rule is written as a line of text",
        "is empty",
        "has no closing bracket",
        "names no tool family this agent has; try Read, Edit or Bash",
        "needs a domain; write WebFetch(domain:example.com)",
        "File changed since this editor opened. Reopen it to review it.",
    ],
)
def test_the_strings_that_shipped_in_english_are_found(checker, text):
    """Every one of these is a real message that reached a reader untranslated.
    `is empty` is why the rule asks for two words and six characters rather than
    the three and twelve that would read more safely: the shortest message is the
    one a length threshold loses."""
    assert checker.is_prose(text)


@pytest.mark.parametrize(
    "text",
    [
        "git diff --stat",
        "cargo build --all --locked",
        "crates/i18n/locales/en-US.ftl",
        "permission-rule-empty",
        "application/json",
        "%Y-%m-%d %H:%M:%S",
        "Bash(git push *)",
    ],
)
def test_a_value_is_not_a_sentence(checker, text):
    """A command, a path, a message name, a media type, a format, a permission
    rule. None holds a word English glues sentences with, which is the whole of
    what separates them from prose -- and a check that fired on these would be
    read once and then ignored."""
    assert not checker.is_prose(text)


def test_machine_text_written_in_english_words_does_fire(checker):
    """The limit worth knowing about. `FROM` is glue whether a query or a
    sentence wrote it, so a query is reported and the author says it is machine
    text. The check is built to be read rather than to be right: the judgement it
    cannot make is the one it hands back."""
    assert checker.is_prose("SELECT name FROM sqlite_master")


# ── Where in a file it looks ──────────────────────────────────────────────────


SOURCE = """//! A comment saying something that is not a message at all.

use bravebot_i18n::t;

/// A doc comment which is also not a message to anyone.
pub fn describe(reason: Reason) -> &'static str {
    let shown = "this rule has no closing bracket";
    let key = "permission-rule-empty";
    let debug = path.to_str().expect("a path that is not valid text");
    /* a block comment
       holding a sentence that goes nowhere */
    shown
}

#[cfg(test)]
mod tests {
    #[test]
    fn a_rule_that_is_empty_is_reported() {
        assert_eq!(describe(Reason::Empty), "this rule is empty");
        let fixture = "a line of text that is not a rule";
    }
}
"""


def test_it_reads_the_sentence_and_nothing_around_it(checker):
    """The comments, the doc comment, the message name, the string written for
    whoever is debugging, and the whole test module: a finding in any of them is
    a finding nobody can act on."""
    assert checker.prose_lines(SOURCE) == {7: ["this rule has no closing bracket"]}


def test_the_line_number_is_the_file_as_it_stands(checker):
    """The number is what the reader of a finding opens the file at, so it is
    counted in the file and not in the diff hunk."""
    assert SOURCE.splitlines()[6].strip().startswith("let shown =")


def test_a_display_impl_is_read_like_any_other_line(checker):
    """`write!(f, ...)` is where the reasons in the branch that prompted this
    lived. Skipping formatting macros as developer output would have skipped the
    defect."""
    found = checker.prose_lines('write!(f, "this rule is empty")\n')
    assert found == {1: ["this rule is empty"]}


# ── Which files, and which lines of them ──────────────────────────────────────


def _read(files):
    return lambda path: files.get(path)


def test_only_a_line_the_branch_added_is_reported(checker):
    """Prose the branch did not write is not this branch's to move, and a check
    that reported the whole file would report hundreds of them."""
    source = 'let old = "this one was already here";\nlet new = "this one is new";\n'
    found = checker.findings(
        {"crates/cli/src/main.rs": {2}},
        BRAVEBOT,
        _read({"crates/cli/src/main.rs": source}),
    )
    assert found == [("crates/cli/src/main.rs", 2, "this one is new")]


def test_the_catalog_crate_is_exempt(checker):
    """The crate that holds the messages is prose by design. Every line of it
    would be a finding."""
    source = 'let message = "this rule is empty";\n'
    files = {"crates/i18n/src/lib.rs": source, "crates/cli/src/main.rs": source}
    found = checker.findings({path: {1} for path in files}, BRAVEBOT, _read(files))
    assert [path for path, _, _ in found] == ["crates/cli/src/main.rs"]


def test_a_path_outside_the_declared_source_is_not_read(checker):
    """An integration test, a build script, a contrib script: none of them says
    anything to a reader of the shipped program."""
    files = {"crates/cli/tests/running.rs": 'assert("this rule is empty")\n'}
    assert (
        checker.findings({"crates/cli/tests/running.rs": {1}}, BRAVEBOT, _read(files))
        == []
    )


def test_a_file_the_branch_deleted_is_skipped(checker):
    """Its lines are in the diff and not on disk. Reading it must not be an
    error -- the branch already did the right thing with it."""
    assert checker.findings({"crates/cli/src/gone.rs": {1}}, BRAVEBOT, _read({})) == []


def test_a_project_that_declares_nothing_checks_nothing(checker):
    """Most projects localize by a route this cannot see. Guessing which of their
    directories holds messages would fire on every diff."""
    files = {"crates/cli/src/main.rs": 'let m = "this rule is empty";\n'}
    assert checker.findings({"crates/cli/src/main.rs": {1}}, {}, _read(files)) == []


# ── Reading the diff ──────────────────────────────────────────────────────────


def test_a_hunk_without_a_count_is_one_line(checker):
    """`@@ -1 +1 @@` means one line, and reading it as none is a finding the
    check would never report."""
    diff = "+++ b/a.rs\n@@ -1 +7 @@\n+let m = 1;\n"
    assert checker.added_from_diff(diff) == {"a.rs": {7}}


def test_a_counted_hunk_covers_the_lines_it_names(checker):
    diff = "+++ b/a.rs\n@@ -0,0 +3,2 @@\n+one\n+two\n"
    assert checker.added_from_diff(diff) == {"a.rs": {3, 4}}


def test_a_deleted_file_adds_nothing(checker):
    diff = "+++ /dev/null\n@@ -1,3 +0,0 @@\n-one\n"
    assert checker.added_from_diff(diff) == {}


def test_a_path_with_a_space_survives_the_prefix(checker):
    """`+++ b/a file.rs` -- only the leading `b/` comes off."""
    assert checker.added_from_diff("+++ b/a file.rs\n@@ -0,0 +1 @@\n+x\n") == {
        "a file.rs": {1}
    }


def test_a_bare_pattern_claims_everything_beneath_it(checker):
    assert checker.matching(["crates/cli/src/main.rs"], ["crates/"]) == [
        "crates/cli/src/main.rs"
    ]


# ── The profile, and the command ──────────────────────────────────────────────


def test_the_bravebot_profile_declares_where_its_messages_live():
    """The rule is inert until a profile names the source it localizes, so the
    one project with a catalog has to name it or nothing above is reachable."""
    path = os.path.join(
        os.path.dirname(__file__), os.pardir, "projects", "bravebot", "profile.json"
    )
    with open(path, encoding="utf-8") as fh:
        rules = json.load(fh)["localization"]
    assert rules["paths"] == ["crates/*/src/*.rs"]
    assert rules["catalog"] == "crates/i18n/locales/en-US.ftl"
    assert "t!" in rules["how"]


def _repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "crates" / "cli" / "src").mkdir(parents=True)
    git = ["git", "-C", str(repo)]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(git + ["config", "user.email", "t@t"], check=True)
    subprocess.run(git + ["config", "user.name", "t"], check=True)
    (repo / "crates" / "cli" / "src" / "main.rs").write_text("fn main() {}\n")
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-qm", "base"], check=True)
    base = subprocess.run(
        git + ["rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return repo, base


def _run_in(repo, tmp_path, *args, profile="bravebot"):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"project": {"name": "bravebot", "profile": profile}}))
    return subprocess.run(
        [sys.executable, os.path.abspath(SCRIPT), *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env={**os.environ, "BOT_CONFIG_FILE": str(config)},
    )


def test_the_command_names_the_string_and_where_to_put_it(tmp_path):
    repo, base = _repo(tmp_path)
    (repo / "crates" / "cli" / "src" / "main.rs").write_text(
        'fn main() {\n    println!("{}", "this rule is empty");\n}\n'
    )
    subprocess.run(["git", "-C", str(repo), "commit", "-aqm", "change"], check=True)
    done = _run_in(repo, tmp_path, "--diff-base", base)
    assert done.returncode == 0, done.stdout
    assert "crates/cli/src/main.rs:2" in done.stdout
    assert "this rule is empty" in done.stdout
    assert "read it back with t!" in done.stdout


def test_a_string_not_yet_committed_is_found_too(tmp_path):
    """The cheapest moment to move a message is before the gates have compiled
    it, and at that point half the work is uncommitted."""
    repo, base = _repo(tmp_path)
    (repo / "crates" / "cli" / "src" / "main.rs").write_text(
        'fn main() {\n    println!("{}", "this rule is empty");\n}\n'
    )
    done = _run_in(repo, tmp_path, "--diff-base", base)
    assert "crates/cli/src/main.rs:2" in done.stdout


def test_a_file_never_added_to_git_is_read_whole(tmp_path):
    """A new module is in no diff until it is staged, and a new module is where a
    new message is most likely to be. Missing it would make the quiet answer the
    common one."""
    repo, base = _repo(tmp_path)
    (repo / "crates" / "cli" / "src" / "doctor.rs").write_text(
        'fn report() {\n    println!("{}", "this rule is empty");\n}\n'
    )
    done = _run_in(repo, tmp_path, "--diff-base", base)
    assert "crates/cli/src/doctor.rs:2" in done.stdout


def test_strict_is_what_fails(tmp_path):
    """Whether a string is a message or a prompt is not mechanical, so the
    default reports and leaves the judgement to the author. --strict is for a
    caller that has already decided."""
    repo, base = _repo(tmp_path)
    (repo / "crates" / "cli" / "src" / "main.rs").write_text(
        'fn main() {\n    println!("{}", "this rule is empty");\n}\n'
    )
    assert _run_in(repo, tmp_path, "--diff-base", base).returncode == 0
    assert _run_in(repo, tmp_path, "--diff-base", base, "--strict").returncode == 1


def test_a_clean_branch_says_so(tmp_path):
    repo, base = _repo(tmp_path)
    (repo / "crates" / "cli" / "src" / "main.rs").write_text(
        'fn main() {\n    let key = "permission-rule-empty";\n}\n'
    )
    done = _run_in(repo, tmp_path, "--diff-base", base)
    assert done.returncode == 0
    assert "No sentence was added" in done.stdout


def test_a_project_with_no_catalog_is_told_nothing_was_checked(tmp_path):
    """Silence would read as a clean branch. It is a project the check has
    nothing to say about."""
    repo, base = _repo(tmp_path)
    done = _run_in(repo, tmp_path, "--diff-base", base, profile="default")
    assert done.returncode == 0
    assert "declares no localization" in done.stdout


def test_a_ref_that_cannot_be_diffed_is_an_error(tmp_path):
    """Exit 2, as check-pr-body.py does: the run did not happen, which is not
    the same as a branch with nothing to fix."""
    repo, _ = _repo(tmp_path)
    done = _run_in(repo, tmp_path, "--diff-base", "no/such/ref")
    assert done.returncode == 2
    assert "cannot diff" in done.stdout
