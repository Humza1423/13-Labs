# tests/qa — acceptance suite

Black-box pytest suite for the 13 Labs HTTP contract, owned by the QA lane.
It drives the app through FastAPI's `TestClient` on
`thirteen_labs.app:create_app()` and never imports engine internals.

## Run it

```bash
uv sync --frozen
uv run pytest -q tests/qa
```

`uv run pytest -q` also picks it up via the repo-wide `testpaths`.

## What runs where

- `test_contract_static.py` — always on. Health shape, `{error:{code,message,retryable}}`
  on unknown API routes, preview-fixture contract round-trip, and the
  `contract.ts` ↔ `contracts.py` literal mirror.
- `test_runs_api.py` — gated: the whole module is skipped while `/api/runs`
  is not registered (pre-E1 main), and activates automatically once the runs
  router lands. `/api/scenarios` coverage gates on that route separately.
  Covers idempotent `POST /api/runs` (same key+body → same run, same key
  different body → 409, header required), 404s, events paging
  (`after`, ≤100/page, increasing `seq`, unique ids, `nextAfter`), retest
  gating + stale-revision 409 + idempotent replay, review-after-completion,
  `Cache-Control: no-store` and HTTP-only session cookies, public-projection
  leak checks (no answer keys, holdout inputs, evaluator truth, or signed
  voice URLs), held-out-case versioning, and attempts-vs-executions
  accounting (denied → `executedCents` 0, valid refund executed once).

## Environment assumptions

- Each test points `STATE_DB` at a per-test temp file, sets `DEMO_MODE=fixture`,
  and clears provider credentials. If the engine cannot drive legs in this
  environment (e.g. fixture mode unimplemented and no keys), deep-flow tests
  **skip** with the run's error rather than fail; read the skip reasons.
- Contract violations (wrong status code, drifted error shape, leaked fields,
  broken cursor semantics) fail regardless of provider availability.

## Fresh-checkout smoke

```bash
bash tests/qa/fresh_checkout.sh [branch] [repo-url]
```

Clones the branch into a temp dir, runs `uv sync --frozen`, `uv run pytest -q`,
`npm ci --prefix web`, `npm run build --prefix web`, boots uvicorn on
`$PORT` (default 8931) and asserts `/api/health` answers `{status:"ok"}`.
