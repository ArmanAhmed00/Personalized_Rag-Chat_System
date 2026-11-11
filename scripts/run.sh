#!/usr/bin/env sh
# Start Outlawz from a source checkout (macOS and Linux).
#
#   ./scripts/run.sh                 # set up once, then start and open the browser
#   ./scripts/run.sh --port 9000     # any `outlawz start` option works
#
# The first run makes a Python environment in backend/.venv, installs Outlawz into it and, if
# Node.js is installed, builds the web app. Later runs just start it.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/backend/.venv"
PY_IN_VENV="$VENV/bin/python"

PY=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
    "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Outlawz needs Python 3.11 or newer. Get it from https://www.python.org/downloads/ and run this again." >&2
  exit 1
fi

if [ ! -x "$PY_IN_VENV" ]; then
  echo "Setting up Outlawz (first run only, takes a minute)..."
  "$PY" -m venv "$VENV"
fi
"$PY_IN_VENV" -m pip --version >/dev/null 2>&1 || "$PY_IN_VENV" -m ensurepip --upgrade >/dev/null

STAMP="$VENV/.outlawz-installed"
if [ ! -f "$STAMP" ] || [ "$ROOT/backend/pyproject.toml" -nt "$STAMP" ]; then
  "$PY_IN_VENV" -m pip install --quiet --disable-pip-version-check -e "$ROOT/backend"
  touch "$STAMP"
fi

if [ ! -f "$ROOT/backend/outlawz/web/index.html" ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "Building the web app (first run only)..."
    "$PY_IN_VENV" "$ROOT/scripts/build_web.py"
  else
    echo "Node.js isn't installed, so only the API will be available." >&2
    echo "Install Node.js 20 or newer and run this again, or install a release with: pipx install outlawz" >&2
  fi
fi

exec "$VENV/bin/outlawz" start "$@"
