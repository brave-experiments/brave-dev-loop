"""Shared config helper for brave-dev-loop Python scripts.

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

    If config_path is None, honours $BOT_CONFIG_FILE -- load-config.sh exports
    it, so a script launched from a shell script reads the same file its
    caller did -- and otherwise searches relative to the bot repo root
    (derived from this file's location: lib/ -> scripts/ -> repo root).
    """
    if config_path and os.path.exists(config_path):
        with open(config_path) as f:
            return json.load(f)

    from_env = os.environ.get("BOT_CONFIG_FILE")
    if from_env and os.path.exists(from_env):
        with open(from_env) as f:
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


# Absent project.profile means a deployment that predates profiles. Those are
# all brave-core, so default there rather than to the generic profile -- an
# unattended bot must not change behaviour just because a key is missing.
DEFAULT_PROFILE = "brave-core"


def profile_dir(config, base_dir=None):
    """Absolute path to the selected project profile directory."""
    name = get_config(config, "project.profile") or DEFAULT_PROFILE
    return os.path.join(base_dir or bot_dir(), "projects", name)


def profile_mismatch(config, base_dir=None):
    """Why the configured profile is the wrong one, or None when it is fine.

    A profile directory named after the project is that project's profile.
    When one exists and the config still carries a value nobody chose -- the
    setup wizard's "default", or nothing at all -- the profile was never
    picked, and every story built from here gets generic validations and a
    docs pointer into a directory that does not exist. Nothing fails; the work
    just comes out wrong, which is why it is worth refusing to proceed.

    Mirrors bot_profile_mismatch() in load-config.sh -- keep the two in sync.
    """
    configured = get_config(config, "project.profile") or ""
    effective = configured or DEFAULT_PROFILE
    project = get_config(config, "project.name") or ""
    if not project or effective == project or configured not in ("", "default"):
        return None
    base = base_dir or bot_dir()
    if not os.path.exists(os.path.join(base, "projects", project, "profile.json")):
        return None
    if configured:
        first = f"config.json sets project.profile to '{configured}'"
    else:
        first = f"config.json sets no project.profile, so it defaults to '{effective}'"
    return (
        f"{first} -- but projects/{project}/ exists and is {project}'s profile.\n"
        f"  Under '{effective}' every story gets that profile's validations and "
        f"docs, not {project}'s.\n"
        f'  Set "profile": "{project}" under "project" in config.json.'
    )


def require_matching_profile(config, base_dir=None):
    """Exit rather than write stories built from a profile nobody chose."""
    message = profile_mismatch(config, base_dir)
    if message:
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(1)


def load_profile(config, base_dir=None):
    """Load the selected profile's profile.json.

    Falls back to the default profile when the configured one has no
    profile.json, so a half-created profile directory cannot strand a bot.
    """
    path = os.path.join(profile_dir(config, base_dir), "profile.json")
    if not os.path.exists(path):
        path = os.path.join(
            base_dir or bot_dir(), "projects", DEFAULT_PROFILE, "profile.json"
        )
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def build_research(profile, config, base_dir=None):
    """Render a profile's pre-implementation reading steps.

    `{bestPractices}` becomes the absolute best-practices index path and
    `{targetRepo}` the absolute target repo path. An entry whose substitution
    does not resolve is dropped rather than emitted with a hole in it: a story
    must never tell an agent to read a path this project does not have. That
    is also why the step is profile-owned -- brave-core has a best-practices
    tree and most projects do not.
    """
    index = best_practices_index(config, base_dir)
    repo = resolve_target_repo(config, base_dir)
    steps = []
    for entry in profile.get("research") or []:
        if "{bestPractices}" in entry and not index:
            continue
        if "{targetRepo}" in entry and not repo:
            continue
        steps.append(
            entry.replace("{bestPractices}", index or "").replace(
                "{targetRepo}", repo or ""
            )
        )
    return steps


def build_validations(profile, test_step=None):
    """Render a profile's validation steps into acceptance criteria.

    `{testStep}` is replaced with `test_step`; the entry is dropped entirely
    when there is no test step for this kind of story.
    """
    steps = []
    for entry in profile.get("validations") or []:
        if entry == "{testStep}":
            if test_step:
                steps.append(test_step)
            continue
        steps.append(entry.replace("{testStep}", test_step or ""))
    return steps


def test_step(profile, kind, test_binary=None, test_filter=None):
    """Render the profile's test step for a story kind, or None."""
    template = (profile.get("testSteps") or {}).get(kind)
    if not template:
        return None
    return template.replace("{testBinary}", test_binary or "").replace(
        "{testFilter}", test_filter or ""
    )


def test_binary(profile, suite, location):
    """Resolve the test target for a suite ('unit'/'browser') and location.

    `location` is 'brave' for a test defined in the target repo, anything else
    for one inherited from the surrounding checkout.
    """
    targets = (profile.get("testTargets") or {}).get(suite) or {}
    return targets.get("local" if location == "brave" else "upstream")


def prd_mode(config):
    """'curated' (default) or 'auto'.

    'curated': stories are authored; the PRD is the source of truth.
    'auto': the PRD is a cache the bot refreshes from GitHub before each run,
    so it needs no manual curation. Defaults to 'curated' so deployments that
    predate the key keep treating their PRD as authored input.
    """
    return get_config(config, "project.prdMode") or "curated"
