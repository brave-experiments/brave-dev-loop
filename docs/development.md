# Development

Working on this repository — the loop itself, not the target repo it drives.

```sh
make test     # pytest
make lint     # ruff check + format --check
make format   # ruff check --fix + format
make check    # lint, tests, and the security scan: the before-you-push pass
```

`./tests/test-suite.sh` validates an installation end to end.

## What runs on a pull request

Everything `make check` runs, so a branch that passed locally passes there:

| | where it runs |
| --- | --- |
| `make lint`, `make test` | [`.github/workflows/lint-and-test.yml`](../.github/workflows/lint-and-test.yml) |
| the security scan | an organization-level workflow (below) |

The workflow runs the two `make` targets on the oldest Python `pyproject.toml`
declares and on the current release. It does not run `make check` itself, because
`check-reviewdog` drives the scan the organization workflow is already doing on the
same commit; `tests/test_ci.py` holds it to that split, so a target added to `check`
fails a test rather than quietly going unrun.

Run `make check` before pushing anyway. It is minutes faster than waiting for the
workflow, and `check-reviewdog` is the half nothing tells you about until a reviewer
sees the comment.

## The security scan

Pull requests here are scanned by an organization-level workflow that runs
[brave/security-action](https://github.com/brave/security-action) and comments through
reviewdog. Nothing in this repository configures it — the runners and the rule set live
over there, and `.github/` here holds only the lint-and-test workflow.

```sh
make check-reviewdog       # what this branch changed, against its merge base
make check-reviewdog-full  # the whole tree, however old the finding is
```

`scripts/check-reviewdog.sh` clones that repository, pins the tool versions its
`action.yml` pins, and drives the same reviewdog runners against this checkout:
**opengrep** (the semgrep fork, on Brave's rule set), **npm-audit**, **pip-audit**,
**safesvg**, and **sveltegrep**. Only the runners with something to look at are enabled,
which here means opengrep and pip-audit. A finding is one the bot would post, so run
`check-reviewdog` before pushing; the full scan reports plenty that predates any given
branch — most of it `subprocess` and `urllib` audit notes on `scripts/`.

The first run downloads opengrep, reviewdog and the rules into `~/.cache`; later runs
re-use them and take about half a minute. No model is involved, so both are deterministic.

The tool versions are pinned at the top of the script to match `OPENGREP_VERSION` in the
action's `src/installOpengrep.js` and `reviewdog_version` in its `actions/main/action.yml`.
They move rarely, and when they do this drifts silently rather than breaking — check them
if a finding here disagrees with one on a PR.

### Fixing a finding

Findings are advisory: the scan comments, it does not block a merge. Fix what is real,
and for an audit note on a pattern this repo uses deliberately — `subprocess` with a
literal argv, `urllib` on a hardcoded URL — leave the code and say so in the PR rather
than contorting it to quiet the rule.
