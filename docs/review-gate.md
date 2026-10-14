# Review gate

With a reviewer account configured (`reviewer.username`), `run.sh` does not leave
a pull request it has just pushed to until that account has reviewed the new
head. A review that asks for changes is answered and waited for again.

```json
{ "reviewer": { "username": "my-reviewer", "maxRounds": 5, "waitMinutes": 120 } }
```

`reviewer.maxRounds: 0` turns it off. With no `reviewer.username` it does not
apply: a review by the bot itself can only comment, so there is no verdict to
wait for.

## What triggers it

After each iteration, if the story is `pushed` and the pull request's head is
not the head it had when the iteration started. A new pull request counts; a
story that was only checked on does not, so a run that walks a queue of old
pull requests never waits on any of them.

## What it does

1. Reads the reviewer's verdict on the new head (`scripts/review-verdict.py`).
2. If the reviewer has not been asked, asks (`scripts/request-review.sh`),
   once. GitHub drops the reviewer from a pull request the moment they review,
   so a push leaves it unasked.
3. Polls once a minute until there is a verdict or `reviewer.waitMinutes` has
   passed. The story's claim is held throughout, so no other slot works it.

| verdict | meaning | what the run does |
| --- | --- | --- |
| `approved` | approved, or a comment review leaving none of the reviewer's threads open | story is done; the run moves on |
| `changes` | changes requested, or a comment review leaving a thread open | works the story again (below) |
| `closed` | merged or closed | moves on |
| `draft` | a draft nobody asked the reviewer to look at | moves on |
| timeout | no review inside `waitMinutes` | moves on, story stays `pushed` |

Only a review made at the head commit counts. After a push the answer is
"pending" until the reviewer looks again, however many times they reviewed the
code before.

## A fix round

The next loop names the story outright, so it is the one worked, and tells the
agent it is answering the reviewer's review (the Review Response Workflow in
[workflow-pushed.md](workflow-pushed.md)). The round is not charged to the
iteration count: `./run.sh 3` is three stories, not three iterations.

When the agent pushes, the head has moved and the gate runs again. It stops, with
the story left as it is, when:

- the reviewer has asked for changes **`reviewer.maxRounds` times** (default 5, counting the
  first review): `maxRounds` reviews, `maxRounds - 1` fix rounds;
- the fix round pushed nothing (the agent judged the comments wrong and
  replied instead): the reviewer has nothing new to look at;
- the story can no longer be taken back, because another run claimed it in the
  moment its claim was free. The run carries on with ordinary selection.

A story the gate gave up on is not lost: it is still `pushed`, and the ordinary
workflow (reminders, the owner escalation, a later run's own review response)
handles it like any other.

## What it does not do

It does not run the review. The reviewer answers review requests on its own
schedule (`make schedules-review`, `scripts/review-requested.sh`), possibly on
another machine, and starting a second review from here would post a second
review of one head. So the wait is only as good as that schedule: with nothing
answering requests, every wait runs out `waitMinutes` later. Set
`reviewer.maxRounds` to 0 for a deployment like that.

## Looking at it by hand

```bash
./scripts/review-verdict.py 351     # the reviewer's verdict on PR #351's head, as JSON
```

Ctrl+C during a wait ends the run like it does anywhere else: the claim and the slot are
released, and the story is left as it was.

While a run waits, `./run.sh --status` shows the slot as running; the iteration log
gets a `review-wait` line a minute so its age does not read as a hang. A terminal
(`tui`) run has no iteration log to write to, and shows only the slot's heartbeat.
