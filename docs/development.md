# Development

Working on this repository — the loop itself, not the target repo it drives.

```sh
make test     # pytest
make lint     # ruff check + format --check
make format   # ruff check --fix + format
make check    # lint, tests, and the security scan: the before-you-push pass
```

`./tests/test-suite.sh` validates an installation end to end.

## The security scan

Pull requests here are scanned by an organization-level workflow that runs
[brave/security-action](https://github.com/brave/security-action) and comments through
reviewdog. It is the only check any CI runs on this repository — there are no workflows
in `.github/` — and nothing here configures it, so the runners and the rule set live over
there.

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
