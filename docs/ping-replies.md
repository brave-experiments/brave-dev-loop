# Replying to pings

An org member can reach the reviewer account without requesting a review: they
@-mention it in a comment, on an issue or on a PR ("what do you think
@the-reviewer?"). The review-request poll answers that too, on its existing
five-minute schedule. There is no cron entry of its own.

## What counts as a ping

A comment, line comment, review body or opening post that @-mentions the
reviewer account (the bot, when no reviewer is configured) and is written by
someone in `.ignore/org-members.txt`, in the PR repository or the issue
repository. Only direct mentions count. A team mention does not.

A thread is **waiting** while the newest such mention has no later comment from
either of our accounts. The queue is GitHub's own, like the review queue: a reply
(or anyone else answering as the bot) takes the thread out, and a new ping puts it
back. Nothing local records what was answered, so every machine agrees.

Also left out: closed threads, pings older than 72 hours (a deployment that starts
polling does not answer last month), and a thread the account already commented on
five times in the last day (two accounts that keep mentioning each other, or a
member who keeps replying "thanks").

## Org members only

The pinger is looked up in the org-member list by `lib/org_gate.py`, with no
model involved. A ping from anyone else is not in the queue, so the gate stays
shut, no session starts and nothing is posted. A members file that cannot be read
fails the query, and a failed query does nothing. A stranger's later ping never
stands in front of a member's earlier one.

## How a ping is answered

`reply-to-pings.sh` handles each waiting thread in three steps, and the model is
only in the middle one. The split is the one `review-session.sh` makes.

1. `pings.py prepare` fetches the thread and writes `context.md` to a work
   directory. It contains only what org members and our own accounts wrote;
   everyone else's words are counted and left out, and so is the title if its
   author is outside the org. For a PR by a member it adds the diff.
2. A `claude` session runs `/reply-to-ping` with **no GitHub token**, under the
   permissions `review-session-settings.py --reply` writes: it can read the work
   directory and the target checkout, and write only into the work directory.
   It writes `reply.md`.
3. `pings.py post` checks the reply (not empty, at most 6000 characters; any
   @-mention except the pinger's is broken with a zero-width space), checks the
   thread once more for an answer that landed meanwhile, and posts one comment.

## Limits

| | |
| --- | --- |
| `REPLY_TO_PINGS_MAX` | threads answered per run, 3 |
| `REPLY_TO_PINGS_MAX_ATTEMPTS` | sessions per ping comment that end with nothing to post, 2 |
| `REPLY_TO_PINGS_SESSION_SECONDS` | time limit of one session, 1800 |

To retry a ping that was given up on, delete its
`.ignore/.ping-<repo>-<n>-<comment id>.attempts`.

## Not covered

- A mention of the bot account when a separate reviewer account is configured.
  The queue asks for the same account the review queue does.
- A reply that changes code. A reply is one comment of text; nothing is checked
  out and nothing is pushed.
- A reply in a line-comment thread. It goes to the PR conversation, with a link to
  the comment it answers.
