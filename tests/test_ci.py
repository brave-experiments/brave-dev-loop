"""Tests for .github/workflows/lint-and-test.yml.

`make check` is what a person runs before pushing; the workflow is what runs
whether or not they did. These pin the two together, because the failure this
repository already had was silent: lint and the tests were red on master for a
week and nothing anywhere said so.

Read as text rather than parsed as YAML -- the suite installs pytest and ruff,
and a YAML parser is not worth adding to run four assertions about substrings.
"""

import os
import re

ROOT_DIR = os.path.join(os.path.dirname(__file__), os.pardir)
WORKFLOW = os.path.join(ROOT_DIR, ".github", "workflows", "lint-and-test.yml")

# The one prerequisite of `make check` the workflow deliberately leaves alone:
# check-reviewdog drives brave/security-action's runners, and an
# organization-level workflow already does exactly that on the same commit.
RUN_BY_THE_ORG_WORKFLOW = {"check-reviewdog"}


def _workflow():
    with open(WORKFLOW, encoding="utf-8") as fh:
        return fh.read()


def _make_check_prerequisites():
    with open(os.path.join(ROOT_DIR, "Makefile"), encoding="utf-8") as fh:
        m = re.search(r"^check:(.*)$", fh.read(), re.MULTILINE)
    assert m, "the Makefile has no 'check' target"
    return m.group(1).split()


def test_the_repository_has_a_workflow():
    """The thing that was missing. Every other test here reads this file."""
    assert os.path.isfile(WORKFLOW), WORKFLOW


def test_the_workflow_runs_lint_and_the_tests():
    workflow = _workflow()
    assert "make lint" in workflow
    assert "make test" in workflow


def test_the_workflow_runs_on_a_pull_request_and_on_a_push_to_master():
    """On the pull request so a reviewer sees it, and on master too: a merge can
    go red on a semantic conflict that neither branch showed on its own."""
    workflow = _workflow()
    assert "pull_request:" in workflow
    assert re.search(r"push:\s*\n\s*branches: \[master\]", workflow), workflow


def test_every_target_make_check_runs_is_run_by_ci_or_named_as_an_exception():
    """The drift this is here to catch: `check` grows a target, the workflow
    does not, and the new gate runs only on the machine of whoever added it."""
    workflow = _workflow()
    for target in _make_check_prerequisites():
        if target in RUN_BY_THE_ORG_WORKFLOW:
            continue
        assert f"make {target}" in workflow, (
            f"'make check' runs '{target}' and the workflow does not; run it "
            f"there too, or add it to RUN_BY_THE_ORG_WORKFLOW with the reason"
        )


def test_every_action_is_pinned_to_a_commit_sha():
    """What the security scan asks of a third-party action, made a gate: a tag
    can be moved to point at something else, a SHA cannot. The scan only
    comments, and the scan is what caught this file the first time."""
    used = re.findall(r"uses: (\S+)", _workflow())
    assert used, "the workflow uses no actions at all"
    for ref in used:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), (
            f"{ref}: pin it to a full 40-character commit SHA with the tag in a "
            f"trailing '# vX.Y.Z' comment"
        )
    for ref in used:
        assert re.search(re.escape(ref) + r" # v[\d.]+", _workflow()), (
            f"{ref}: add the '# vX.Y.Z' comment, or nothing says which release "
            f"the SHA is"
        )


def test_ci_tests_the_oldest_python_the_project_claims_to_support():
    """pyproject declares the floor and ruff targets it. Nothing else checks it:
    the scripts are standard library only and every machine running the bot has
    something far newer, so a 3.10-ism would ship unnoticed."""
    with open(os.path.join(ROOT_DIR, "pyproject.toml"), encoding="utf-8") as fh:
        floor = re.search(r'requires-python = ">=([\d.]+)"', fh.read())
    assert floor, "pyproject.toml declares no requires-python"
    assert f'"{floor.group(1)}"' in _workflow(), (
        f"pyproject.toml supports Python {floor.group(1)} and the workflow "
        f"never runs it"
    )
