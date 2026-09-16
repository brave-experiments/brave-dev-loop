.PHONY: test lint format check check-reviewdog check-reviewdog-full setup schedules view-schedules clean archive archive-progress archive-prd backlog backlog-dry-run worktree unmount-worktrees

# Prefer .venv when it exists so no target needs an activated shell. PEP 668
# interpreters (Homebrew, recent Debian) refuse a system-wide pytest install, so
# a venv is the usual outcome of `make setup` -- but a system pytest/ruff still
# works, hence the fallback rather than a hard dependency.
VENV := $(CURDIR)/.venv
PYTHON := $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)
RUFF := $(if $(wildcard $(VENV)/bin/ruff),$(VENV)/bin/ruff,ruff)

# Run the test suite
test:
	$(PYTHON) -m pytest tests/ -v

# Lint Python files (check only)
lint:
	$(RUFF) check .
	$(RUFF) format --check .

# Lint and auto-fix Python files
format:
	$(RUFF) check --fix .
	$(RUFF) format .

# The whole local pass, in one target. The security scan is the only one of the
# three that anything runs for us -- there are no workflows in this repository,
# so lint and the tests are enforced here or nowhere. Run it before pushing.
check: lint test check-reviewdog

# The security scan that comments on our pull requests, before pushing rather
# than after. Nothing here configures it: it arrives as an organization-level
# workflow calling brave/security-action, so scripts/check-reviewdog.sh clones
# that repository and drives its reviewdog runners against this checkout,
# pinning the tool versions its action.yml pins. First run downloads opengrep,
# reviewdog and the rule set into ~/.cache; later runs re-use them and take
# about half a minute.
#
# The two targets are the action's own two modes. On a pull request it scans
# what the branch changed; on workflow_dispatch it scans everything. A finding
# here is one the bot would post, so the full scan reports plenty that predates
# any given branch -- check-reviewdog is the one to run before pushing.
#
# No model is involved, so both are deterministic.
check-reviewdog:
	@./scripts/check-reviewdog.sh

check-reviewdog-full:
	@./scripts/check-reviewdog.sh --full

# Install dev dependencies and run project setup
setup:
	@./scripts/install-dev-deps.sh
	@rm -rf *.egg-info
	./scripts/setup.sh

# Install/update cron schedules
schedules:
	./scripts/sync-schedules.sh

# Show schedule summary
view-schedules:
	./scripts/view-schedules.sh

# Archive progress.txt (append to progress.archived.txt, reset progress.txt)
archive-progress:
	@if [ ! -f data/progress.txt ]; then echo "No progress.txt to archive."; exit 0; fi
	@echo "Archiving progress.txt..."
	@cat data/progress.txt >> data/progress.archived.txt
	@echo "" >> data/progress.archived.txt
	@echo "# Progress Log" > data/progress.txt
	@echo "Started: $$(date)" >> data/progress.txt
	@echo "---" >> data/progress.txt
	@echo "Archived $$(wc -l < data/progress.archived.txt) lines to progress.archived.txt"

# Sync open issues assigned to the bot into the PRD backlog (no LLM involved)
backlog:
	python3 scripts/add-backlog-to-prd.py

# Show which assigned issues would be added, without writing prd.json
backlog-dry-run:
	python3 scripts/add-backlog-to-prd.py --dry-run

# Archive completed PRD stories (merged/invalid → prd.archived.json)
archive-prd:
	python3 scripts/archive-prd.py data/prd.json

# Archive both progress.txt and completed PRD stories
archive: archive-progress archive-prd

# Open a shell in the worktree for a pull request:
#
#   make worktree PR=https://github.com/brave/bravebot/pull/351
#   make worktree PR=351
#   make worktree              # asks which pull request
#
# An existing worktree for the pull request's branch is reused -- the branch is
# what identifies it, since a worktree's directory name follows the story's
# issue and not the branch. Otherwise one is created from the pull request's
# head. Either way the main checkout's .envrc is copied in and allowed, because
# untracked files are not shared between worktrees and a fresh one would
# otherwise have no environment at all. No model is involved.
#
# The cd happens here rather than in the script because a child process cannot
# change its parent's directory: the only way to leave you somewhere is to open
# a shell there. Exit that shell to come back.
#
# make reserves the name SHELL for the shell that runs recipes and refuses to
# read it from the environment, so the user's own shell is read explicitly.
USER_SHELL := $(shell printenv SHELL 2>/dev/null)
worktree:
	@dir=$$(python3 scripts/worktree-for-pr.py $(if $(PR),"$(PR)")) || exit $$?; \
	 echo "--> $$dir" >&2; \
	 cd "$$dir" && { $(or $(USER_SHELL),/bin/sh) -i || true; }

# Remove the target repository's story worktrees and the directories they live
# in -- the `../<repo>-<issue>` checkouts a worktree profile leaves behind, each
# one carrying a build directory of a few gigabytes:
#
#   make unmount-worktrees          # every one idle for over 24 hours
#   make unmount-worktrees ALL=1    # every one, whatever it holds
#   make unmount-worktrees DRY_RUN=1
#
# The default is the pass run.sh makes at the start of a run, so it keeps what
# that keeps: anything used in the last day, and anything holding work no remote
# has. ALL=1 drops both -- for emptying the directory rather than collecting
# after a run. Uncommitted changes are what that loses and it names each one as
# it goes; the branches are the repository's, not the worktrees', so unpushed
# commits survive and `git checkout <branch>` still finds them.
#
# Either way a worktree a live run claims, or one git has locked, stays: pulling
# the directory out from under a running session breaks it. No model is involved.
unmount-worktrees:
	@python3 scripts/clean-worktrees.py \
	  $(if $(ALL),--all,--max-age-hours 24) $(if $(DRY_RUN),--dry-run) >/dev/null

# Clean up generated/temporary files
clean:
	rm -f .run.lock
