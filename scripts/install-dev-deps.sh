#!/bin/bash
# Install the dev dependencies (pytest, ruff) used by `make test` and `make lint`.
#
# The Makefile invokes `python3 -m pytest` and bare `ruff`, so pytest only needs
# to be importable and ruff only needs to be on PATH — however they got there.
# This script tries the reasonable install paths in order and stays quiet when
# there is nothing to do, so `make setup` remains idempotent.
#
# Note: on a PEP 668 "externally managed" interpreter (Homebrew Python, recent
# Debian/Ubuntu) both `pip3 install` and `uv pip install --system` refuse to
# touch site-packages. There the correct answer is the OS package manager or a
# virtualenv, so print that instead of pip's wall of text.

have() { command -v "$1" >/dev/null 2>&1; }

# Match how the Makefile actually invokes each tool.
deps_present() {
  have ruff && python3 -c "import pytest" >/dev/null 2>&1
}

if deps_present; then
  echo "✓ pytest and ruff already available — skipping dev dependency install"
  exit 0
fi

if have uv; then
  # Inside an active venv uv targets it; otherwise it needs --system.
  if [ -n "${VIRTUAL_ENV:-}" ]; then
    uv pip install pytest ruff || true
  else
    uv pip install --system pytest ruff || true
  fi
  if deps_present; then
    exit 0
  fi
fi

if have pip3; then
  pip3 install pytest ruff || pip3 install --user pytest ruff || true
  if deps_present; then
    exit 0
  fi
fi

echo "Error: could not install pytest and ruff automatically." >&2
echo "" >&2
echo "This is usually a PEP 668 externally-managed interpreter. Pick one:" >&2
echo "  macOS/Homebrew:  brew install pytest ruff" >&2
echo "  Debian/Ubuntu:   sudo apt install python3-pytest ruff" >&2
echo "  Any platform:    python3 -m venv .venv && . .venv/bin/activate && pip install pytest ruff" >&2
echo "" >&2
echo "Then re-run 'make setup'. Only 'make test' and 'make lint' need these —" >&2
echo "the bot itself uses the standard library only." >&2
exit 1
