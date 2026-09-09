# brave-core: Repository

Project-specific repository rules. Read alongside `docs/git-repository.md`.

The target repo is `src/brave` inside a Chromium checkout; the parent
directories are chromium and not where you should commit.

## pnpm Commands

The target repo uses **pnpm**, not npm. Never run `npm ...` against it — `npm install`
will corrupt `node_modules` and the lockfile.

When running pnpm commands from the PRD acceptance criteria:
- The commands say "pnpm run X from src/brave"
- Change directory to `[targetRepoPath from bot config]` first
- Example: `cd [targetRepoPath from bot config] && pnpm run build`
- Node 24.x and pnpm >= 11.9 are required (`devEngines` in package.json enforces
  this). If pnpm is missing or node is the wrong major, run `nvm use v24.16.0`
  first — `nvm use node` picks up node 25 and every pnpm command fails.
- Script arguments are passed directly, with no `--` separator:
  `pnpm run test brave_unit_tests --filter="Fixture.Test"`
