#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv && .venv/bin/python -m pip install -q -r requirements.txt
fi
command -v ffmpeg >/dev/null || echo "WARNING: ffmpeg not found on PATH"
exec .venv/bin/python -m app
