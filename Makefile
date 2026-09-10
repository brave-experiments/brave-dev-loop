.PHONY: test lint format check check-reviewdog check-reviewdog-full setup schedules view-schedules clean archive archive-progress archive-prd backlog backlog-dry-run

# Run the test suite
test:
	python3 -m pytest tests/ -v

# Lint Python files (check only)
lint:
	ruff check .
	ruff format --check .

# Lint and auto-fix Python files
format:
	ruff check --fix .
	ruff format .

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

# Clean up generated/temporary files
clean:
	rm -f .run.lock
