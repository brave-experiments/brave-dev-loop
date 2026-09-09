"""Shared config helper for brave-dev-bot Python scripts.

Usage:
    from lib.load_config import load_config, get_config, require_config

    config = load_config()               # auto-discovers config.json
    org = require_config(config, "project.org")          # errors if missing
    repo = get_config(config, "project.prRepository")    # returns None if missing
"""

import json
import os
import sys


def load_config(config_path=None):
    """Load config.json, falling back to config.example.json.

    If config_path is None, searches relative to the bot repo root
    (derived from this file's location: lib/ -> scripts/ -> repo root).
    """
    if config_path and os.path.exists(config_path):
        with open(config_path) as f:
            return json.load(f)

    bot_dir = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    for name in ("config.json", "config.example.json"):
        path = os.path.join(bot_dir, name)
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)

    return {}


def get_config(config, dotted_key, default=None):
    """Read a dotted key from the config dict (e.g. 'project.org')."""
    keys = dotted_key.split(".")
    value = config
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            return default
        if value is None:
            return default
    return value


def require_config(config, dotted_key):
    """Read a dotted key from the config dict, exit with error if missing or empty."""
    value = get_config(config, dotted_key)
    if not value:
        print(
            f"Error: '{dotted_key}' not set in config.json. Run 'make setup'.",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def bot_dir():
    """Absolute path of the bot repo root (lib/ -> scripts/ -> repo root)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def resolve_target_repo(config, base_dir=None):
    """Resolve project.targetRepoPath to an absolute directory, or None.

    The setup wizard documents this path as relative to the bot directory, but
    config.brave-core.json (and every deployment seeded from it) stores a value
    relative to the bot directory's *parent* -- "src/brave", not
    "../src/brave". Both spellings are in the wild, so try both bases and use
    whichever one is actually a git repo. Absolute paths are used as-is.

    Mirrors resolve_target_repo() in load-config.sh -- keep the two in sync.
    """
    path = get_config(config, "project.targetRepoPath", "")
    if not path:
        return None
    if os.path.isabs(path):
        return os.path.normpath(path)

    base = base_dir or bot_dir()
    bot_base = os.path.normpath(os.path.join(base, path))
    parent_base = os.path.normpath(os.path.join(os.path.dirname(base), path))

    if os.path.exists(os.path.join(bot_base, ".git")):
        return bot_base
    if os.path.exists(os.path.join(parent_base, ".git")):
        return parent_base
    # Neither exists -- return the documented base so errors name a sane path.
    return bot_base


def resolve_docs_dir(config, base_dir=None):
    """Resolve bestPractices.docsDir to an absolute directory, or None.

    Stored relative to the bot dir, but the same base ambiguity that affects
    targetRepoPath has produced parent-relative values in the wild, so both
    bases are tried. Prefer this over joining the raw value yourself: the raw
    value is only meaningful against a base, and consumers run from three
    different working directories (bot dir for cron skills, its parent for
    run.sh story iterations, arbitrary for manual invocation).

    Mirrors resolve_docs_dir() in load-config.sh -- keep the two in sync.
    """
    path = get_config(config, "bestPractices.docsDir", "")
    if not path:
        return None
    if os.path.isabs(path):
        return os.path.normpath(path)

    base = base_dir or bot_dir()
    bot_base = os.path.normpath(os.path.join(base, path))
    parent_base = os.path.normpath(os.path.join(os.path.dirname(base), path))

    if os.path.isdir(bot_base):
        return bot_base
    if os.path.isdir(parent_base):
        return parent_base
    return bot_base


def best_practices_index(config, base_dir=None):
    """Absolute path to the best-practices index file, or None.

    Absolute on purpose: this string is embedded verbatim into PRD acceptance
    criteria, which are read by agents whose working directory differs between
    run.sh iterations and cron skill invocations. A relative path cannot be
    correct for both.
    """
    docs = resolve_docs_dir(config, base_dir)
    if not docs:
        return None
    index = get_config(config, "bestPractices.indexFile", "best_practices.md")
    return os.path.join(docs, index)
