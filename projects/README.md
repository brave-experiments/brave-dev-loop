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
| `validations` | Ordered acceptance-criteria steps every story ends with. `{testStep}` is replaced per story kind, and dropped when the profile defines no test step for that kind. |
| `testSteps` | Templates for `testFix`, `disabledTest`, and `generic` stories. `{testBinary}` and `{testFilter}` are substituted. |
| `labels` | Project labels: `pr` (applied to bot PRs) and `disabledTest` (marks an issue as a disabled test). `labels.disabledTestLabel` in `config.json` is honoured as a fallback. |
| `testTargets` | Maps a suite (`unit`, `browser`) and a location to a test binary. `local` is a test defined in the target repo, `upstream` one inherited from the surrounding checkout. |

**`docs/`** holds the prose. The shared workflow docs keep a one-line pointer
wherever a step is project-specific; `run.sh` passes the resolved profile docs
path in the agent prompt.

**Defaulting.** A config with no `project.profile` resolves to `brave-core`,
because every deployment predating profiles is a brave-core one and an
unattended bot must not change behaviour just because a key is missing. New
setups get `default`.

**Adding a project.** Copy `projects/default/`, fill in `profile.json`, and add
only the docs your workflows actually need — an absent doc just means the
pointer has nothing to add.
