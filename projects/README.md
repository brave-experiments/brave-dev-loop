# Project profiles

Everything in this repo outside `projects/` is project-neutral. Anything that
only makes sense for one codebase — build commands, test targets, how upstream
tests are detected, which validation steps a story must pass — lives in a
profile here and is selected by `project.profile` in `config.json`.

```
projects/<name>/
  profile.json   # validations, test steps, test targets
  schedules.sh   # this project's cron jobs
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
| `uiPaths` | Repo-relative paths whose changes a person can look at. A change under one of them has to show the screen it produces, and `check-pr-body.py --diff-base` errors when the body shows none — see [Showing a terminal screen](../docs/pr-descriptions.md#showing-a-terminal-screen). A path with no `*`, `?` or `[` matches everything beneath it. Omit the key for a project with no interface, and no screen is asked for. |

**`schedules.sh`** holds this project's cron jobs — see [Schedules](#schedules).

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

## Schedules

`make schedules` installs one crontab block for one project, and the jobs in it
come from `projects/<profile>/schedules.sh`. A profile with no such file gets
`projects/default/schedules.sh`, so a new project is still just a profile
directory.

Each file prints crontab lines, built with the helpers in
`scripts/lib/cron-jobs.sh`:

```sh
bot_cron_job "0 1 * * *" "./scripts/check-has-work.sh" \
  "./scripts/sync-target-repo.sh && ./run.sh 20" "run-cron.log"
```

`bot_cron_job` gives every job the same prologue — the bot directory, the bot
identity from `.envrc`, its gate, and a hard reset of the bot repo — so a job
line says only what is particular to it. The gate runs before the git sync: a
job with nothing to do costs no fetches. `bot_cron_agent <lock> '<prompt>'`
builds the command for a job that starts an agent session, held under a named
lock.

One machine can run several deployments, and the crontab block each one writes
is marked with its `project.name`. Installing one project's schedules replaces
that project's block and leaves every other block — another project's, or
anything you wrote yourself — where it is.

`make view-schedules` renders the block this deployment would install and says
which file produced it.
