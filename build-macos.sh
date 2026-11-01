#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "This script builds a native macOS app. Run it on a Mac." >&2
    exit 1
fi
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
if [[ ! -x .venv/bin/python ]]; then
    "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -m pip install -r dependency-lock-macos.txt
.venv/bin/python scripts/build.py
