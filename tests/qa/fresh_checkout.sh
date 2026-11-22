#!/usr/bin/env bash
# Fresh-checkout smoke for 13 Labs: clone -> sync -> pytest -> web build ->
# boot uvicorn -> curl /api/health. Proves a cold clone is runnable end to end.
#
#   bash tests/qa/fresh_checkout.sh [branch] [repo-url]
#
# Defaults: branch=main, repo from this checkout's origin (fallback: GitHub).
# PORT=<n> overrides the port; by default a free loopback port is picked so a
# stale server can never answer health on the smoke port.
set -euo pipefail

BRANCH="${1:-main}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REPO_URL="${2:-$(git -C "${REPO_ROOT}" remote get-url origin 2>/dev/null || echo https://github.com/rayanali2/13-Labs.git)}"
PORT="${PORT:-$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')}"

TMP="$(mktemp -d -t thirteen-labs-fresh-checkout)"
UVICORN_PID=""
cleanup() {
  [ -n "${UVICORN_PID}" ] && kill "${UVICORN_PID}" 2>/dev/null || true
  # Backstop: never leave the smoke server running.
  pkill -f "uvicorn thirteen_labs.app:app --port ${PORT}" 2>/dev/null || true
  rm -rf "${TMP}" || { sleep 1; rm -rf "${TMP}" || true; }
}
trap cleanup EXIT

echo "==> clone ${BRANCH} from ${REPO_URL}"
git clone --quiet --branch "${BRANCH}" "${REPO_URL}" "${TMP}/repo"

echo "==> uv sync --frozen"
uv sync --frozen --project "${TMP}/repo"

# A shared uv cache can occasionally yield an incomplete install (observed:
# websockets missing utils.py / extensions/__init__.py). Sanity-check the env
# and rebuild once before calling it a failure.
if ! (cd "${TMP}/repo" && uv run --no-sync python -c "import uvicorn, websockets.frames" 2>/dev/null); then
  echo "==> env sanity check failed; rebuilding .venv once"
  rm -rf "${TMP}/repo/.venv"
  uv sync --frozen --project "${TMP}/repo"
  (cd "${TMP}/repo" && uv run --no-sync python -c "import uvicorn, websockets.frames")
fi

echo "==> uv run pytest -q"
(cd "${TMP}/repo" && uv run pytest -q)

echo "==> npm ci --prefix web"
# npm resolves --prefix relative to cwd for project discovery; run inside the clone.
(cd "${TMP}/repo" && npm ci --prefix web)

echo "==> npm run build --prefix web"
(cd "${TMP}/repo" && npm run build --prefix web)

echo "==> boot uvicorn on :${PORT}"
(cd "${TMP}/repo" && exec uv run uvicorn thirteen_labs.app:app --port "${PORT}") &
UVICORN_PID=$!

echo "==> wait for /api/health"
for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${PORT}/api/health" -o "${TMP}/health.json"; then
    break
  fi
  # The server died instead of coming up; surface its exit.
  kill -0 "${UVICORN_PID}" 2>/dev/null || { wait "${UVICORN_PID}" || true; echo "FAIL: uvicorn exited before /api/health"; exit 1; }
  sleep 1
done

test -s "${TMP}/health.json" || { echo "FAIL: /api/health never answered"; exit 1; }
cat "${TMP}/health.json"; echo
python3 - "${TMP}/health.json" <<'PY'
import json, sys
body = json.load(open(sys.argv[1]))
assert body["status"] == "ok", body
assert set(body["capabilities"]) == {"batch", "voice", "traces", "alter", "personas"}
PY

echo "PASS: fresh checkout installs, tests, builds and serves /api/health"
