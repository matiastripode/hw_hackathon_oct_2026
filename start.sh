#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
    PYTHON=.venv/bin/python
else
    PYTHON=python3
fi
exec "$PYTHON" -m uvicorn handoff.app:app --host "${HOST:-127.0.0.1}" --port "${PORT:-3000}"
