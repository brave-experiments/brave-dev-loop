"""Run a review-prs script as the configured reviewer account.

A review posted as the bot cannot approve a PR the bot opened. It can only
comment, and the commit is then cached as reviewed, so a later run as the
reviewer skips the PR and nothing ever approves it. Scheduled jobs go through
scripts/as-reviewer.sh; a /review-prs typed into a session did not.
"""

import os
import subprocess  # nosemgrep
import sys

from lib.load_config import bot_dir, get_config

GUARD = "BOT_REVIEWER_REEXECED"


def current_login():
    result = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def ensure_reviewer(config, log, login=None):
    """Return when running as the reviewer, or when none is configured.

    Otherwise run the same command through as-reviewer.sh, which stops
    rather than fall back to the bot, and exit with its status.
    """
    reviewer = get_config(config, "reviewer.username") or ""
    if not reviewer:
        return
    login = current_login() if login is None else login
    if login.lower() == reviewer.lower():
        return
    if os.environ.get(GUARD):
        log(
            f"ERROR: still '{login or 'nobody'}' after as-reviewer.sh, not '{reviewer}'"
        )
        sys.exit(1)
    log(f"Running as '{login}'; re-running as the reviewer '{reviewer}'")
    os.environ[GUARD] = "1"
    wrapper = os.path.join(bot_dir(), "scripts", "as-reviewer.sh")
    argv = [wrapper, "--", sys.executable, *sys.argv]
    sys.exit(subprocess.run(argv).returncode)  # nosemgrep
