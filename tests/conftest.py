"""Shared fixtures for brave-dev-bot tests."""

import importlib.util
import json
import os
import tempfile

import pytest

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "scripts")
ROOT_DIR = os.path.join(os.path.dirname(__file__), os.pardir)
SKILLS_DIR = os.path.join(os.path.dirname(__file__), os.pardir, ".claude", "skills")


def _load_module(name, path):
    """Load a Python file as a module (for scripts without .py packages)."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Lazy-load script modules so tests can import their functions directly.


@pytest.fixture
def update_prd_status():
    return _load_module(
        "update_prd_status",
        os.path.join(SCRIPTS_DIR, "update-prd-status.py"),
    )


@pytest.fixture
def select_task():
    return _load_module(
        "select_task",
        os.path.join(SCRIPTS_DIR, "select-task.py"),
    )


@pytest.fixture
def business_hours():
    return _load_module(
        "business_hours",
        os.path.join(SCRIPTS_DIR, "business-hours-elapsed.py"),
    )


@pytest.fixture
def check_prd_has_work():
    return _load_module(
        "check_prd_has_work",
        os.path.join(SCRIPTS_DIR, "check-prd-has-work.py"),
    )


@pytest.fixture
def sync_bot_prs():
    return _load_module(
        "sync_bot_prs_to_prd",
        os.path.join(SCRIPTS_DIR, "sync-bot-prs-to-prd.py"),
    )


@pytest.fixture
def add_backlog():
    return _load_module(
        "add_backlog_to_prd",
        os.path.join(SCRIPTS_DIR, "add-backlog-to-prd.py"),
    )


@pytest.fixture
def chunk_best_practices():
    return _load_module(
        "chunk_best_practices",
        os.path.join(SKILLS_DIR, "review-prs", "chunk-best-practices.py"),
    )


# --- Temp file helpers ---


@pytest.fixture
def tmp_dir():
    """Provide a temporary directory that's cleaned up after test."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def write_json(tmp_dir):
    """Helper to write a JSON file in tmp_dir and return its path."""

    def _write(filename, data):
        path = os.path.join(tmp_dir, filename)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        return path

    return _write


@pytest.fixture
def read_json():
    """Helper to read a JSON file back."""

    def _read(path):
        with open(path) as f:
            return json.load(f)

    return _read


@pytest.fixture
def repair_config_paths():
    return _load_module(
        "repair_config_paths",
        os.path.join(SCRIPTS_DIR, "repair-config-paths.py"),
    )
