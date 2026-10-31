# Pull request labels

`/review-prs` labels a pull request with the review it needs, so a person or a policy can decide
from the labels how much human review the PR gets. The labels are named per project in
`review.labelPrs` of its [profile](../projects/README.md), and the code is the same for every
project.

## When a PR is labelled

A PR is labelled once, by the first review that reads it. The review cache records it in
`_labelled`, and no later push, re-review or `full` run labels it again. A label a person removed
stays removed. A PR first reviewed before a project's `review.labelPrs` was added is labelled the
next time a review reads it.

PRs the review skips are not labelled: a non-member's PR, which the org gate drops before anything
else reads it, a PR the bot already approved, and an uplift.

Nothing is applied until every label the profile names exists in the PR repository. Creating a
label needs write access, which the reviewer account may not have, so a person creates them with
`gh label create`. While one is missing, each run logs which, writes no label prompt, runs no
model and records no PR, so no PR loses its turn.

## How a label is decided

| Kind | Decided by | Model |
| --- | --- | --- |
| `rules` | the changed file paths, as globs | none |
| `judged` | one subagent that reads the diff and chooses from the profile's list | once per PR |

A glob matches the whole path and `*` crosses `/`. A glob with no `/` matches the file name, so
`DEPS` and `*_unittest.cc` apply at any depth. `any` applies the label when some changed file
matches, `only` when every changed file does, and `exclude` withholds it when some changed file
matches.

The diff is written by the PR's author, so three limits keep it from deciding anything:

- The subagent chooses from the profile's list. A label it names that is not on the list is
  ignored, and nothing it writes reaches a command line.
- A judged label only raises scrutiny. A label that would lower it, such as `no-behavior-change`,
  is not judged by a model: a PR could be written to earn it. It stays a person's label until a
  check that does not read the diff can decide it.
- Labels are only added.

## The labels of a project

A project's labels, what decides each, and the `gh label create` commands for them are in the
"Pull request labels" section of `projects/<name>/docs/labels.md`. The profile is the source of
the labels, and a test checks that the project's doc names every label and has a command for it.

## Reading what it did

A run logs one `LABELS:` line per PR: the model's reason for each judged label, then what was
added. Without `--auto` it logs what it would add and records nothing. When a person removes a
judged label, the reason in the log says why the model chose it.
