#!/bin/sh
set -eu

if [ -z "${ELEVENLABS_API_KEY:-}" ] && [ -n "${THIRTEEN_LABS_KEY:-}" ]; then
    export ELEVENLABS_API_KEY="$THIRTEEN_LABS_KEY"
fi

if [ -n "${REPLAY_FILE:-}" ]; then
    RECORDED_RUN_ID=$(python scripts/export_replay.py import "$REPLAY_FILE" --db "$STATE_DB")
    export RECORDED_RUN_ID
fi

exec uvicorn thirteen_labs.app:app --host 0.0.0.0 --port "${PORT:-8080}" --workers 1
