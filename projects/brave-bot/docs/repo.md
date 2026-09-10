# brave-bot: Repository

Project-specific repository rules. Read alongside `docs/git-repository.md`.

The target repo is a standalone checkout — a Rust workspace at its root, with
no surrounding source tree. Commit there, and nowhere above it.

## Package managers

`cargo` owns the code. `npm` exists only for the published wrapper package, and
`package-lock.json` is lint-checked in CI, so never hand-edit it and never run
`npm install` when `npm ci` will do.

- Dependency changes need a maintainer's agreement first. A new crate widens the
  supply-chain surface of a binary people install.
- `Cargo.lock` is committed and every CI build passes `--locked`. A build that
  wants to rewrite it means the manifest and the lockfile disagree — fix the
  manifest, do not drop the flag.

## Version bumps

`make bump-version` is a release step, not something a story does. Leave the
version alone unless the story is the release itself.
