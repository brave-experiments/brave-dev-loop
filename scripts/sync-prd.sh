#!/bin/bash
# Bring data/prd.json up to date with GitHub. Every step here is plain Python
# against the API — no agent is started, so this costs no tokens.
#
# The backlog sync appends stories for newly assigned issues and never touches
# one the PRD already has, so it runs in either mode. A curated PRD is
# authored, not a list of every issue somebody remembered to type in by hand;
# before this was a script, that same append ran nightly as an agent session.
#
# The three status syncs rewrite story status from GitHub, which is only true of
# a PRD that is a cache. They run in "auto" alone: a curated PRD is never
# rewritten from GitHub behind the operator. The first adopts the bot's open PRs
# as "pushed" stories, the second retires the ones that have since been merged —
# without it a merged PR holds its place in the pushed queue, which
# select-task.py ranks above pending work, so a run spends iterations moving
# statuses instead of writing code. The third is the same waste at the issue end:
# intake only ever asks GitHub for open issues, so a pending story whose issue
# somebody else closed keeps its place in the queue until an iteration reads the
# issue and reaches the status one API call decides.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"

PRD_FILE="$BOT_DIR/data/prd.json"
if [ ! -f "$PRD_FILE" ]; then
  mkdir -p "$(dirname "$PRD_FILE")"
  printf '{\n  "stories": []\n}\n' > "$PRD_FILE"
  echo "Created empty PRD at $PRD_FILE"
fi

# Neither sync is fatal: a GitHub hiccup should not cancel the run, which can
# still work the stories already in the PRD.
#
# In auto mode the issue sync runs after the status syncs, not before them. A
# `Part of` PR that a maintainer merged between runs is only retired to
# "merged" by the merged-PR sync, and the issue sync only adds the story that
# finishes its issue once every story on that issue is merged. Run in the
# other order, that follow-up story waited a whole extra run.sh. Running after
# the bot-PR sync also means an issue whose open PR was just adopted counts as
# tracked, instead of getting a second, pending story.
if [ "$BOT_PRD_MODE" = "auto" ]; then
  python3 "$SCRIPT_DIR/sync-bot-prs-to-prd.py" || echo "WARNING: PR sync failed — continuing with the cached PRD." >&2
  python3 "$SCRIPT_DIR/sync-merged-prs-to-prd.py" || echo "WARNING: merged-PR sync failed — continuing with the cached PRD." >&2
  python3 "$SCRIPT_DIR/sync-closed-issues-to-prd.py" || echo "WARNING: closed-issue sync failed — continuing with the cached PRD." >&2
fi

python3 "$SCRIPT_DIR/add-backlog-to-prd.py" || echo "WARNING: issue sync failed — continuing with the PRD as it stands." >&2

if [ "$BOT_PRD_MODE" = "auto" ]; then
  # Last, so the stories the two retiring syncs just finished go too. A cached
  # PRD is rebuilt from GitHub every run, so a finished story left in it is only
  # something for the selector to filter and a reader to scroll past; the two
  # syncs that add stories read prd.archived.json as well, and will not re-add
  # work that moved there. A curated PRD is the operator's list and is archived
  # on the operator's say-so — `make archive-prd`.
  python3 "$SCRIPT_DIR/archive-prd.py" "$PRD_FILE" || echo "WARNING: archiving finished stories failed — continuing with the cached PRD." >&2
fi
