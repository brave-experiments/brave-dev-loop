#!/usr/bin/env python3
"""Print the Claude Code settings a scheduled /review-prs session runs under.

    python3 scripts/review-session-settings.py --work-dir /var/tmp/review-prs/review-prs-abc \
        [--deny-read <path>]...

scripts/review-session.sh passes the output to `claude --settings`, with
`--permission-mode dontAsk` so that anything these rules do not allow is
refused instead of asked about. The session reads untrusted text (the PR's
diff, description and source tree), so it is held to what the skill's middle
steps need and nothing else:

- reads only inside its working directories: the bot directory, where it is
  started, and the run's work directory, which the wrapper adds;
- writes only inside the work directory;
- one script, select-candidates.py, through Bash, plus the read-only commands
  Claude Code always allows (cat, grep, head and the like);
- subagents, for the detect and validate passes;
- no web access.

The wrapper starts the session without a GitHub token, and prepare-review.py
and collect-results.py run outside it, so nothing in it can post or push. The
read-only commands still read files, though, so the places a credential lives
on this machine are denied outright, even where they fall inside a working
directory: `.envrc` is in the bot directory itself.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.load_config import get_config, load_config

BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELECT = ".claude/skills/review-prs/select-candidates.py"

# Credentials in the operator's home directory: git's plaintext store, gh's
# config directories (the bot's and the reviewer's), ssh keys, and Claude
# Code's own settings, which can hold an API key.
HOME_SECRETS = (
    ".git-credentials",
    ".netrc",
    ".config/gh/**",
    ".ssh/**",
    ".claude/settings.json",
    ".claude.json",
)


def rule_path(path):
    """An absolute path as a permission rule spells it. A single leading slash
    is relative to the settings file; two make it absolute."""
    return "/" + os.path.abspath(path)


def settings(work_dir, deny_read, home, bot_dir=BOT_DIR):
    secrets = [os.path.join(home, p) for p in HOME_SECRETS]
    secrets += [os.path.join(bot_dir, ".envrc"), "/proc/**"]
    secrets += [p.rstrip("/") + "/**" if os.path.isdir(p) else p for p in deny_read]
    return {
        "permissions": {
            "allow": [
                "Glob",
                "Grep",
                "Agent",
                "Task",
                f"Edit({rule_path(work_dir)}/**)",
                f"Bash(python3 {SELECT} *)",
                f"Bash(python3 {os.path.join(bot_dir, SELECT)} *)",
            ],
            "deny": ["WebFetch", "WebSearch"]
            + [f"Read({rule_path(p)})" for p in secrets],
        }
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--work-dir", required=True)
    parser.add_argument(
        "--deny-read",
        action="append",
        default=[],
        help="another file or directory the session must not read",
    )
    args = parser.parse_args()
    deny = list(args.deny_read)
    key = get_config(load_config(), "bot.sshKeyPath")
    if key:
        deny.append(os.path.expanduser(key))
    home = os.path.expanduser("~")
    print(json.dumps(settings(args.work_dir, [p for p in deny if p], home)))


if __name__ == "__main__":
    main()
