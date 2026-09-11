"""Tests for the reading steps put in front of every iteration.

Covers: select-task.py research_steps(), the field it adds to the task JSON,
and run.sh putting that field in the prompt.
"""

import json
import os

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


@pytest.fixture
def bravebot_config(tmp_path, monkeypatch):
    """A config on the bravebot profile, which declares reading steps."""
    config = {
        "project": {
            "name": "bravebot",
            "targetRepoPath": "../bravebot",
            "profile": "bravebot",
            "defaultBranch": "main",
        },
        "bestPractices": {
            "docsDir": "../bravebot/docs",
            "indexFile": "best_practices.md",
        },
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    monkeypatch.setenv("BOT_CONFIG_FILE", str(path))
    return path


def test_a_profile_with_reading_steps_renders_them(select_task, bravebot_config):
    """What the story never carried: the docs a project is reviewed against."""
    steps = select_task.research_steps(ROOT)

    assert any("AGENTS.md" in s for s in steps)
    assert all("{" not in s for s in steps), "a substitution was left unresolved"


def test_the_steps_name_absolute_paths(select_task, bravebot_config):
    """An iteration's working directory is not the bot directory."""
    for step in select_task.research_steps(ROOT):
        for word in step.split():
            if word.endswith((".md", ".md.")) and "/" in word:
                assert word.startswith("/"), step


def test_a_profile_with_no_steps_renders_none(select_task, tmp_path, monkeypatch):
    """The default profile declares none, and an empty list says so quietly."""
    config = {"project": {"name": "x", "profile": "default", "targetRepoPath": "../x"}}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    monkeypatch.setenv("BOT_CONFIG_FILE", str(path))

    assert select_task.research_steps(ROOT) == []


def test_the_prompt_carries_the_steps_not_the_criteria():
    """A story older than a reading step still has to read it.

    Acceptance criteria are rendered once, when the story is created. run.sh
    reads the steps back out of the task JSON every iteration instead.
    """
    body = open(os.path.join(ROOT, "run.sh")).read()

    assert '\'(.research // []) | map("- " + .) | join("\\n")\'' in body
    assert "$READ_FIRST" in body
