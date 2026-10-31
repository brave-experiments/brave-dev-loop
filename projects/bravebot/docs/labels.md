# bravebot: Labels

Project-specific label rules. Read alongside
[docs/workflow-state-machine.md](../../../docs/workflow-state-machine.md), which
owns the generic rule: the loop orders its backlog on the triage axes and never
invents a label.

The profile's `labels.pr` is empty — bravebot has no label every bot PR carries.

## The three triage axes

Every open issue in bravebot carries one value from each of three axes, and the
profile's `labels.axes` maps each to the label prefix that spells it:

| Axis | Labels | The question it answers |
|---|---|---|
| `importance` | `importance/p1` to `importance/p5` (1 is highest) | how much it matters that this is fixed at all |
| `urgency` | `urgency/p1` to `urgency/p5` (1 is highest) | how soon it has to happen |
| `size` | `size/1` to `size/5` (1 is smallest) | how much work it is |

**What each value means is bravebot's to define, not this repo's.** The source of
truth is the "Labelling an issue" section of the target repo's
`docs/development.md`, and bravebot's own `/triage-issues` skill is what applies
the labels. Read that section before arguing with an ordering — a story the loop
put behind another is a claim about the labels, not about the loop.

A missing axis is not a low value. It means nobody has judged the issue yet, and
the loop treats it as the middle value rather than guessing high or low.

`urgency/p1` says everybody should put down what they are holding. It is a
person's call, so neither the triage skill nor this loop ever applies one.

## Pull request labels

`/review-prs` labels each pull request once, the first time it reads it, as
[pr-labels.md](../../../docs/pr-labels.md) describes. The labels are `review.labelPrs` in this
profile, in `brave/bravebot`.

| Label | Decided by | Applies when |
| --- | --- | --- |
| `tests-only` | paths | every changed file is a test file |
| `docs-only` | paths | every changed file is a `.md` file other than under `docs/specs/`, `AGENTS.md`, `agents/` and `.claude/` |
| `risk/dependencies` | paths | some changed file is a dependency manifest or lockfile, `.config/deny.toml` or `socket.yml` |
| `risk/guarantee` | model | the change can let untrusted content decide something, or changes what trust, permissions, routing, confinement or egress allow |
| `risk/network-calls` | model | the change adds or alters a network request or destination |

`docs-only` leaves out `docs/specs/` because a spec states behaviour, and leaves out the agent
instruction files because they steer what an agent does. `tests-only` does not see a test module
inside a source file, so a PR that edits only one is not labelled.

```bash
R=brave/bravebot
gh label create tests-only --repo $R --color 0E8A16 --description "Every changed file is a test file"
gh label create docs-only --repo $R --color 0E8A16 --description "Every changed file is documentation, outside the specs and agent instructions"
gh label create risk/dependencies --repo $R --color D93F0B --description "Changes a dependency manifest or lockfile"
gh label create risk/guarantee --repo $R --color D93F0B --description "Can let untrusted content decide something, or changes what trust or egress allow"
gh label create risk/network-calls --repo $R --color D93F0B --description "Adds or alters a network request or destination"
```
