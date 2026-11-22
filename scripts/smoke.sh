#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 2 || ! "$2" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Usage: $0 <https://public-host> <full-commit-sha>" >&2
  exit 2
fi

base="${1%/}"
if [[ "$base" != https://* && "${ALLOW_LOCAL_HTTP:-0}:$base" != 1:http://127.0.0.1:* ]]; then
  echo "Use public HTTPS, or opt in to local 127.0.0.1 testing with ALLOW_LOCAL_HTTP=1." >&2
  exit 2
fi

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
curl --fail --silent --show-error --max-time 20 --retry 3 --retry-connrefused "$base/api/health" > "$scratch/health.json"
curl --fail --silent --show-error --max-time 15 "$base/api/scenarios" > "$scratch/scenarios.json"
curl --fail --silent --show-error --max-time 15 "$base/" > "$scratch/index.html"
if [[ -n "${RUN_ID:-}" ]]; then
  if [[ ! "$RUN_ID" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    echo "Invalid run ID." >&2
    exit 2
  fi
  curl --fail --silent --show-error --max-time 15 "$base/api/runs/$RUN_ID" > "$scratch/run.json"
  curl --fail --silent --show-error --max-time 15 "$base/api/runs/$RUN_ID/events?after=0" > "$scratch/events.json"
fi

python3 - "$scratch" "$2" <<'PY'
import json
import os
import sys
from pathlib import Path

root, commit = Path(sys.argv[1]), sys.argv[2]

def required(condition, message):
    if not condition:
        raise SystemExit(message)

health = json.loads((root / "health.json").read_text())
required(health["status"] == "ok" and health["commit"] == commit, "Health commit mismatch.")
capabilities = health["capabilities"]
if os.environ.get("REQUIRE_LIVE_PROVIDERS") == "1":
    required(capabilities.get("batch") and capabilities.get("voice"), "Live batch or voice is unavailable.")
scenarios = json.loads((root / "scenarios.json").read_text())
required(any(case["id"] == "monitor-valid" for case in scenarios["scenarios"]), "The valid demo case is missing.")
required("<html" in (root / "index.html").read_text().lower(), "The app did not serve an HTML shell.")
if (root / "run.json").exists():
    run = json.loads((root / "run.json").read_text())
    events = json.loads((root / "events.json").read_text())["events"]
    required(run["id"] == os.environ["RUN_ID"] and run["status"] == "complete", "The saved run is incomplete.")
    required(bool(run["cases"]) and bool(events), "The saved report is missing case or event evidence.")
    required(not any(run["actions"].values()), "The signed-out view exposed a write action.")
    print(f"Saved run {run['id']} is readable without signing in ({run['mode']}).")
print(f"Health and app shell passed for {commit}.")
PY
