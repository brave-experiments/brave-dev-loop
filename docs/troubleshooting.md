# Troubleshooting

## "Git user not configured"

Run `make setup` again or manually configure git in your target repo with the username/email from `config.json`.

## "Pre-commit hook blocks my commit"

If the bot account is trying to modify dependencies — this is intentional. Use existing libraries or a different git account.

## "Org members cache missing"

Run `make setup` to regenerate `.ignore/org-members.txt`, or manually:
```bash
gh api /orgs/<your-org>/members --paginate --jq '.[].login' > .ignore/org-members.txt
```

## "Tests are taking too long"

Expected. The bot uses `run_in_background: true` and high timeouts (1-2 hours) for long operations.

## "Build failures after sync"

The bot will automatically rebase and re-sync:
```bash
git fetch
git rebase origin/master
pnpm run sync --no-history
```

## Still stuck?

- `data/progress.txt` has per-iteration logs
- `.claude/CLAUDE.md` defines agent behaviour
- `make setup` re-verifies and repairs configuration
- `./tests/test-suite.sh` validates the install
