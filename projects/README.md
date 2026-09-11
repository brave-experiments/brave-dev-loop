# Project profiles

Everything in this repo outside `projects/` is project-neutral. Anything that
only makes sense for one codebase — build commands, test targets, how upstream
tests are detected, which validation steps a story must pass — lives in a
profile here and is selected by `project.profile` in `config.json`.

```
projects/<name>/
  profile.json   # validations, test steps, test targets
  docs/          # prose the workflow docs point at
```

**`profile.json`**

| Key | Purpose |
| --- | --- |
| `research` | Reading steps every story starts with. `{bestPractices}` becomes the absolute path of `bestPractices.docsDir` + `indexFile`, `{targetRepo}` the absolute target repo path; an entry naming a substitution that does not resolve is dropped rather than pointing an agent at a path the project does not have. |
| `validations` | Ordered acceptance-criteria steps every story ends with. `{testStep}` is replaced per story kind, and dropped when the profile defines no test step for that kind. |
| `testSteps` | Templates for `testFix`, `disabledTest`, and `generic` stories. `{testBinary}` and `{testFilter}` are substituted. |
| `labels` | Project labels: `pr` (applied to bot PRs), `disabledTest` (marks an issue as a disabled test), and `axes` (the label prefix that spells each triage axis, so the backlog can be ordered by them — see [Backlog order](../docs/workflow-state-machine.md#backlog-order-the-three-triage-axes)). `labels.disabledTestLabel` in `config.json` is honoured as a fallback. |
| `testTargets` | Maps a suite (`unit`, `browser`) and a location to a test binary. `local` is a test defined in the target repo, `upstream` one inherited from the surrounding checkout. |

**`docs/`** holds the prose. The shared workflow docs keep a one-line pointer
wherever a step is project-specific; `run.sh` passes the resolved profile docs
path in the agent prompt.

**Defaulting.** A config with no `project.profile` resolves to `brave-core`,
because every deployment predating profiles is a brave-core one and an
unattended bot must not change behaviour just because a key is missing. New
setups get the profile named after the project when a directory of that name
exists here, and `default` otherwise.

**A profile named after the project is that project's profile**, so a config
that still says `default` (or nothing) while `projects/<project.name>/` exists
is one where nobody chose. `run.sh` refuses to start on that rather than
running the story under generic validations, and `add-backlog-to-prd.py` and
`sync-bot-prs-to-prd.py` refuse to write stories from it — wrong acceptance
criteria outlive the config that produced them. `./run.sh --status` says the
same thing without stopping anything. Deliberately pairing a project with a
differently named profile is fine: name it in `project.profile` and nothing
complains.

**Adding a project.** Copy `projects/default/`, fill in `profile.json`, and add
only the docs your workflows actually need — an absent doc just means the
pointer has nothing to add.
