#!/bin/bash
# Answer the pings an org member made of the reviewer account: one short session
# per thread, then one comment.
#
#   ./scripts/reply-to-pings.sh
#   REPLY_TO_PINGS_MAX=1 ./scripts/reply-to-pings.sh
#   REPLY_TO_PINGS_MAX_ATTEMPTS=3 ./scripts/reply-to-pings.sh
#
# Run from review-requested.sh on the review-request schedule, so it has the same
# identity (the reviewer) and the same slots. The queue is lib/pings.py's: a thread
# where a member's @-mention has no later comment from us. A ping from anyone else
# is not in it, so nothing here runs for them.
#
# The model never touches GitHub. Three steps, the same split review-session.sh
# makes for a review:
#
#   1. `pings.py prepare` fetches the thread and writes it to a work directory,
#      leaving out everything non-members wrote.
#   2. A claude session with no GitHub token reads that file and writes reply.md.
#   3. `pings.py post` checks the reply (length, no mention but the pinger's),
#      checks the thread again for an answer that landed meanwhile, and posts it.
#
# Overlap is safe the way it is for reviews: a lock per thread for the life of the
# session, and a bounded number of runs by the caller (`with-lock.sh review-prs
# --slots N`). Attempts are counted per ping comment, because a session that ends
# without a postable reply leaves the ping in the queue, and without a cap a
# question the model cannot answer would start a paid session on every poll.
#
# Exits non-zero when a query failed or a session produced no reply.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/load-config.sh"
source "$SCRIPT_DIR/lib/lock.sh"
source "$SCRIPT_DIR/lib/review-agent.sh"
source "$SCRIPT_DIR/lib/review-requests.sh"

PINGS_PY="$SCRIPT_DIR/lib/pings.py"
MAX_THREADS="${REPLY_TO_PINGS_MAX:-3}"
MAX_ATTEMPTS="${REPLY_TO_PINGS_MAX_ATTEMPTS:-2}"
SESSION_SECONDS="${REPLY_TO_PINGS_SESSION_SECONDS:-1800}"

LOCK_DIR="$BOT_DIR/.ignore"
mkdir -p "$LOCK_DIR"

# Exit code the per-thread subshell uses for "someone else has this one", and
# for "no longer waiting" (pings.py's own 3): neither is a failed reply.
THREAD_BUSY_RC=75
GONE_RC=3

if ! PINGS=$(bot_ping_queue); then
  echo "Could not query pings for $BOT_REVIEW_USERNAME." >&2
  exit 1
fi

if [ -z "$PINGS" ]; then
  echo "No pings for $BOT_REVIEW_USERNAME — nothing to do."
  exit 0
fi

bot_ping_args

# Unanswered attempts at this ping comment.
attempts_at() {
  local count
  count=$(cat "$1" 2>/dev/null) || count=0
  [[ "$count" =~ ^[0-9]+$ ]] && echo "$count" || echo 0
}

# One ping, start to finish. Runs in a subshell holding the thread's lock.
reply_one() {
  local repo="$1" number="$2" comment="$3" work no_gh settings rc
  work=$(mktemp -d "${TMPDIR:-/tmp}/ping-reply-XXXXXX") || return 1
  no_gh=$(mktemp -d) || { rm -rf "$work"; return 1; }

  python3 "$PINGS_PY" prepare "${BOT_PING_ARGS[@]}" \
    --repo "$repo" --number "$number" --comment-id "$comment" --work-dir "$work"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    rm -rf "$work" "$no_gh"
    return "$rc"
  fi

  # The session reads the target checkout too, to check what it is asked about.
  # The work directory first: it is the one place the session may write.
  local add_dirs=("$work")
  [ -n "${BOT_TARGET_REPO_DIR:-}" ] && [ -d "$BOT_TARGET_REPO_DIR" ] && add_dirs+=("$BOT_TARGET_REPO_DIR")

  settings=$(python3 "$SCRIPT_DIR/review-session-settings.py" --reply --work-dir "$work" \
    ${BOT_GH_CONFIG_DIR:+--deny-read "$BOT_GH_CONFIG_DIR"} \
    ${BOT_REVIEWER_GH_CONFIG_DIR:+--deny-read "$BOT_REVIEWER_GH_CONFIG_DIR"} \
    ${GH_CONFIG_DIR:+--deny-read "$GH_CONFIG_DIR"}) || { rm -rf "$work" "$no_gh"; return 1; }

  # No token, and an empty gh config directory so gh cannot fall back to the
  # login in its default one. A failed session is not the end: it may have written
  # the reply first, and post decides on what is on disk.
  bot_review_agent_cmd reply "/reply-to-ping --work-dir $work" "$BOT_REVIEW_MODEL" "$settings" "${add_dirs[@]}" \
    || { rm -rf "$work" "$no_gh"; return 1; }
  (
    cd "$BOT_DIR" || exit 1
    exec env -u GH_TOKEN -u GITHUB_TOKEN -u GH_ENTERPRISE_TOKEN -u GITHUB_ENTERPRISE_TOKEN \
      GH_CONFIG_DIR="$no_gh" \
      "$SCRIPT_DIR/timeout-tree.sh" "$SESSION_SECONDS" \
      "${BOT_REVIEW_AGENT_CMD[@]}" </dev/null
  ) || echo "The reply session for $repo#$number exited $?; posting what it wrote." >&2
  [ -z "$BOT_REVIEW_AGENT_TMP" ] || rm -f "$BOT_REVIEW_AGENT_TMP"

  python3 "$PINGS_PY" post "${BOT_PING_ARGS[@]}" --work-dir "$work"
  rc=$?
  rm -rf "$work" "$no_gh"
  return "$rc"
}

REPLIED=0
FAILED=0
BUSY=""
GAVE_UP=""
DEFERRED=""

while IFS=$'\t' read -r REPO NUMBER KIND COMMENT WHO; do
  [ -n "$REPO" ] || continue
  THREAD="$REPO#$NUMBER"
  SLUG="${REPO//\//_}-$NUMBER"

  if [ "$REPLIED" -ge "$MAX_THREADS" ]; then
    DEFERRED="$DEFERRED $THREAD"
    continue
  fi

  ATTEMPTS_FILE="$LOCK_DIR/.ping-$SLUG-$COMMENT.attempts"
  ATTEMPTS=$(attempts_at "$ATTEMPTS_FILE")
  if [ "$ATTEMPTS" -ge "$MAX_ATTEMPTS" ]; then
    GAVE_UP="$GAVE_UP $THREAD"
    continue
  fi

  # `200>&-` for the reason with-lock.sh gives: a subagent that outlived the
  # session holding the lock fd would keep this thread locked with nothing running.
  RC=0
  (
    bot_acquire_lock "$LOCK_DIR/.ping-$SLUG.lock" || exit "$THREAD_BUSY_RC"
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] answering $THREAD — $WHO pinged $BOT_REVIEW_USERNAME in a $KIND"
    reply_one "$REPO" "$NUMBER" "$COMMENT" 200>&- </dev/null
  ) || RC=$?

  case "$RC" in
    0)
      REPLIED=$((REPLIED + 1))
      rm -f "$ATTEMPTS_FILE"
      echo "Answered $THREAD."
      ;;
    "$THREAD_BUSY_RC")
      BUSY="$BUSY $THREAD"
      ;;
    "$GONE_RC")
      # Answered by someone else between the queue and the post. Not a failure.
      rm -f "$ATTEMPTS_FILE"
      echo "$THREAD is no longer waiting."
      ;;
    *)
      REPLIED=$((REPLIED + 1))
      FAILED=$((FAILED + 1))
      echo "$ATTEMPTS" | awk '{print $1 + 1}' > "$ATTEMPTS_FILE"
      echo "No reply posted on $THREAD ($((ATTEMPTS + 1)) of $MAX_ATTEMPTS attempts at comment $COMMENT)." >&2
      ;;
  esac
done <<< "$PINGS"

[ -z "$GAVE_UP" ] || echo "Gave up on$GAVE_UP after $MAX_ATTEMPTS sessions without a postable reply (delete .ignore/.ping-<repo>-<n>-<comment>.attempts to retry)."
[ -z "$BUSY" ] || echo "Already being answered by another run; skipped$BUSY"
[ -z "$DEFERRED" ] || echo "Capped at $MAX_THREADS per run; leaving$DEFERRED for the next poll."

[ "$FAILED" -eq 0 ]
