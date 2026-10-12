#!/usr/bin/env bash
# Create .venv and install the package with dev dependencies.
# Prefers uv; falls back to python -m venv + pip.
set -euo pipefail

cd "$(dirname "$0")/.."

if command -v uv >/dev/null 2>&1; then
  uv venv --allow-existing .venv
  uv pip install --python .venv/bin/python -e '.[dev]'
else
  echo "uv not found, falling back to venv + pip" >&2
  [ -d .venv ] || python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -e '.[dev]'
fi

echo
echo "Done. Next:"
echo "  make lint test"
echo "  .venv/bin/python -m wx_dlh --help"
