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
# touch site-packages. There the answer is a virtualenv, which this script
# creates as a last resort -- the Makefile prefers .venv when it exists.
#
# `brew install pytest` is deliberately not suggested: Homebrew puts pytest in
# its own private virtualenv and exposes only the binary, so `import pytest`
# still fails and this script would loop forever telling you to re-run it.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$PROJECT_ROOT/.venv"

have() { command -v "$1" >/dev/null 2>&1; }

# Match how the Makefile actually invokes each tool: pytest via `-m`, so it must
# be importable rather than merely on PATH.
deps_present() {
  if [ -x "$VENV/bin/ruff" ] && "$VENV/bin/python" -c "import pytest" >/dev/null 2>&1; then
    return 0
  fi
  have ruff && python3 -c "import pytest" >/dev/null 2>&1
}

if deps_present; then
  echo "✓ pytest and ruff already available — skipping dev dependency install"
  exit 0
fi

if have uv; then
  # Inside an active venv uv targets it; otherwise it needs --system.
  if [ -n "${VIRTUAL_ENV:-}" ]; then
    uv pip install pytest ruff >/dev/null 2>&1 || true
  else
    uv pip install --system pytest ruff >/dev/null 2>&1 || true
  fi
  if deps_present; then
    exit 0
  fi
fi

if have pip3; then
  # Output suppressed: the expected failure here is PEP 668, whose message is a
  # 25-line wall of advice that does not apply (see the note above), and the
  # venv fallback below handles it. Errors surface as the final message.
  pip3 install pytest ruff >/dev/null 2>&1 ||
    pip3 install --user pytest ruff >/dev/null 2>&1 || true
  if deps_present; then
    exit 0
  fi
fi

# Last resort, and the only route a PEP 668 interpreter permits. No activation
# is needed afterwards: the Makefile runs .venv/bin/python directly.
echo "System-wide install unavailable (PEP 668) — creating $VENV instead"
if python3 -m venv "$VENV" && "$VENV/bin/python" -m pip install --quiet --upgrade pip &&
  "$VENV/bin/python" -m pip install --quiet pytest ruff && deps_present; then
  echo "✓ pytest and ruff installed in .venv"
  exit 0
fi

echo "Error: could not install pytest and ruff automatically." >&2
echo "" >&2
echo "Creating a virtualenv at $VENV failed. Install them by hand:" >&2
echo "  python3 -m venv .venv && .venv/bin/python -m pip install pytest ruff" >&2
echo "  Debian/Ubuntu alternative:  sudo apt install python3-pytest ruff" >&2
echo "" >&2
echo "Then re-run 'make setup'. Only 'make test' and 'make lint' need these —" >&2
echo "the bot itself uses the standard library only." >&2
exit 1
