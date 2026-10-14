#!/bin/bash
# The reviewer gate: what run.sh does after an iteration that pushed to a pull
# request, when a reviewer account is configured (reviewer.username).
#
# It waits for the reviewer's verdict on the new head. A verdict that asks for
# changes sends the same story round the loop again, so the agent answers the
# review, and the new head is waited on in turn. At most reviewer.maxRounds
# reviews per story; then the story is left as it stands and the run goes on.
#
# The review itself is not run from here. The reviewer account answers
# review requests on its own schedule (scripts/review-requested.sh), possibly on
# another machine, and a second reviewer started from this run would post a
# second review of the same head. This only asks, once, when nobody has, and
# then reads GitHub until the answer is there or the wait runs out.
#
# Sourced by run.sh after lib/load-config.sh. Requires BOT_DIR, BOT_PR_REPO,
# BOT_REVIEWER_USERNAME, BOT_REVIEW_MAX_ROUNDS and BOT_REVIEW_WAIT_MINUTES.
# BOT_REVIEW_POLL_SECONDS (60) and BOT_REVIEW_WAIT_SECONDS (the minutes, in
# seconds) exist so a test does not take a minute to see a poll or an hour to
# see a timeout; nothing else sets them.
#
# State, all of it in these variables:
#   BOT_REVIEW_STORY, BOT_REVIEW_ROUND  the story being reviewed and how many
#                                       reviews of it have been waited on
#   BOT_REVIEW_PIN                      set by bot_review_after_iteration to the
#                                       story that needs a fix round next
#   BOT_REVIEW_VERDICT                  what bot_wait_for_review concluded:
#                                       approved | changes | closed | draft | timeout

BOT_REVIEW_STORY=""
BOT_REVIEW_ROUND=0
BOT_REVIEW_PIN=""
BOT_REVIEW_VERDICT=""

# 0 = the gate applies to this deployment. 1 = it does not, with nothing printed.
bot_review_enabled() {
  [ -n "${BOT_REVIEWER_USERNAME:-}" ] && [ "${BOT_REVIEW_MAX_ROUNDS:-0}" -gt 0 ]
}

# Is the gate's configuration usable? 0 = yes, 1 = no (reason on stderr).
bot_validate_review_config() {
  local key value
  for key in maxRounds:"$BOT_REVIEW_MAX_ROUNDS" waitMinutes:"$BOT_REVIEW_WAIT_MINUTES"; do
    value="${key#*:}"
    case "$value" in
      ''|*[!0-9]*)
        echo "Error: reviewer.${key%%:*} must be a non-negative integer (got '$value')" >&2
        return 1 ;;
    esac
  done
  if bot_review_enabled && [ "$BOT_REVIEW_WAIT_MINUTES" -lt 1 ]; then
    echo "Error: reviewer.waitMinutes must be at least 1 while reviewer.maxRounds is above 0" >&2
    return 1
  fi
  return 0
}

bot_review_reset() {
  BOT_REVIEW_STORY=""
  BOT_REVIEW_ROUND=0
  BOT_REVIEW_PIN=""
}

# The pull request's head commit, or nothing when GitHub cannot say.
bot_pr_head() {
  gh pr view "$1" --repo "$BOT_PR_REPO" --json headRefOid --jq .headRefOid 2>/dev/null || true
}

# Heartbeat the slot, and mark the iteration log as written to when there is one:
# ./run.sh --status reads a log that has been silent for long as a hung run.
_bot_review_alive() {
  declare -F bot_slot_heartbeat >/dev/null && bot_slot_heartbeat
  [ -n "${1:-}" ] && printf '%s\n' "$2" >> "$1"
  return 0
}

# Wait for the reviewer's verdict on the pull request's current head.
#
#   bot_wait_for_review <pr> [iteration-log]
#
# Sets BOT_REVIEW_VERDICT. An iteration-log is appended to while waiting; leave
# it out when the iteration has none (a terminal session).
bot_wait_for_review() {
  local pr="$1" log="${2:-}"
  local poll="${BOT_REVIEW_POLL_SECONDS:-60}"
  local limit="${BOT_REVIEW_WAIT_SECONDS:-$((BOT_REVIEW_WAIT_MINUTES * 60))}"
  local started=$SECONDS deadline=$((SECONDS + limit))
  local requested_by_us=false next_note=$((SECONDS + 300))
  local json verdict requested

  BOT_REVIEW_VERDICT=""
  while true; do
    json=$(python3 "$BOT_DIR/scripts/review-verdict.py" \
      --repo "$BOT_PR_REPO" --reviewer "$BOT_REVIEWER_USERNAME" "$pr" 2>/dev/null) || true
    verdict=$(jq -r '.verdict // "error"' <<< "$json" 2>/dev/null || echo error)
    requested=$(jq -r '.requested // false' <<< "$json" 2>/dev/null || echo false)

    case "$verdict" in
      approved|changes|closed|draft)
        BOT_REVIEW_VERDICT="$verdict"
        return 0 ;;
      pending)
        # Nobody has asked. GitHub drops the reviewer from a pull request the
        # moment they review, so a push leaves it unasked until someone does.
        if [ "$requested" != true ] && [ "$requested_by_us" != true ]; then
          requested_by_us=true
          "$BOT_DIR/scripts/request-review.sh" "$pr" || true
        fi ;;
    esac

    if [ "$SECONDS" -ge "$deadline" ]; then
      BOT_REVIEW_VERDICT=timeout
      return 0
    fi
    if [ "$SECONDS" -ge "$next_note" ]; then
      echo "  PR #$pr: still waiting on $BOT_REVIEWER_USERNAME ($(((SECONDS - started) / 60))m of ${BOT_REVIEW_WAIT_MINUTES}m)."
      next_note=$((SECONDS + 300))
    fi
    _bot_review_alive "$log" "$(jq -nc --arg pr "$pr" --arg verdict "$verdict" \
      --argjson waited "$((SECONDS - started))" \
      '{"type":"review-wait","pr":($pr|tonumber),"verdict":$verdict,"waitedSeconds":$waited}')"
    sleep "$poll"
  done
}

# Called with the story an iteration just worked, once its status is known.
#
#   bot_review_after_iteration <story> <pr> <head-before> <end-status> [iteration-log]
#
# <head-before> is the pull request's head when the iteration started: empty
# when the story had no pull request yet, "?" when GitHub would not say. Only an
# iteration that moved the head has anything new for the reviewer, so a story
# that was merely checked on is never waited for.
#
# Sets BOT_REVIEW_PIN to <story> when another fix round is due, and to nothing
# otherwise.
bot_review_after_iteration() {
  local story="$1" pr="$2" head_before="$3" end_status="$4" log="${5:-}"
  local head fixing=false
  [ "$BOT_REVIEW_STORY" = "$story" ] && fixing=true
  BOT_REVIEW_PIN=""

  if ! bot_review_enabled; then
    bot_review_reset
    return 0
  fi
  if [ "$end_status" != pushed ] || [ -z "$pr" ] || [ "$head_before" = "?" ]; then
    bot_review_reset
    return 0
  fi

  head=$(bot_pr_head "$pr")
  if [ -z "$head" ] || [ "$head" = "$head_before" ]; then
    if [ "$fixing" = true ]; then
      echo "$story: the fix round pushed nothing, so $BOT_REVIEWER_USERNAME has nothing new to review on PR #$pr. Ending the review loop."
    fi
    bot_review_reset
    return 0
  fi

  if [ "$fixing" != true ]; then
    BOT_REVIEW_STORY="$story"
    BOT_REVIEW_ROUND=0
  fi
  BOT_REVIEW_ROUND=$((BOT_REVIEW_ROUND + 1))

  echo ""
  echo "$story: PR #$pr is at ${head:0:12}; waiting for $BOT_REVIEWER_USERNAME (review $BOT_REVIEW_ROUND of $BOT_REVIEW_MAX_ROUNDS, up to ${BOT_REVIEW_WAIT_MINUTES}m)."
  bot_wait_for_review "$pr" "$log"

  case "$BOT_REVIEW_VERDICT" in
    approved)
      echo "$story: $BOT_REVIEWER_USERNAME has no changes to ask for on PR #$pr."
      bot_review_reset ;;
    changes)
      if [ "$BOT_REVIEW_ROUND" -ge "$BOT_REVIEW_MAX_ROUNDS" ]; then
        echo "$story: $BOT_REVIEWER_USERNAME still asks for changes on PR #$pr after $BOT_REVIEW_ROUND reviews. Giving up on it."
        bot_review_reset
      else
        echo "$story: $BOT_REVIEWER_USERNAME asks for changes on PR #$pr. Working them (review $BOT_REVIEW_ROUND of $BOT_REVIEW_MAX_ROUNDS done)."
        BOT_REVIEW_PIN="$story"
      fi ;;
    draft)
      echo "$story: PR #$pr is a draft and $BOT_REVIEWER_USERNAME has not reviewed it, so there is nothing to wait for."
      bot_review_reset ;;
    closed)
      echo "$story: PR #$pr is no longer open."
      bot_review_reset ;;
    timeout)
      echo "$story: $BOT_REVIEWER_USERNAME did not review PR #$pr within ${BOT_REVIEW_WAIT_MINUTES}m. Moving on."
      bot_review_reset ;;
  esac
  return 0
}
