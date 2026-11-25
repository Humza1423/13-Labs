# Original hackathon development notes

This document preserves the team’s original README and development-lane coordination notes for historical reference. Branch ownership and planned paths describe the hackathon, not necessarily the current implementation.

---

# 13 Labs

**Before you hire your next AI coworker, send it to 13.**

13 is an AI reliability engineer. A manager hands 13 a candidate refund-support employee, and 13 runs it through **The AI Probation Shift**: a fictional damaged-monitor refund workplace with a disputed claim, a valid claim and a claim missing evidence. 13 watches the candidate's conversation and tool decisions, explains a failure from the recorded evidence, proposes a targeted workflow change, retests the revised candidate on the same cases plus an unseen case, and hands the manager a review to inspect.

All orders, customers, carrier records and money in this lab are synthetic.

## Run it

Requires Python 3.12 with [uv](https://docs.astral.sh/uv/) and Node 20+.

```bash
uv sync --frozen
npm ci --prefix web
cp .env.example .env            # fill in provider keys you have

uv run uvicorn thirteen_labs.app:app --port 8000   # API on :8000
npm run dev --prefix web                            # UI on :5173, proxies /api

uv run pytest -q
npm run build --prefix web      # FastAPI serves web/dist on the same origin
```

`GET /api/health` returns `{status, commit, capabilities}`.

## Shared contract

The JSON contract is camelCase and lives in two mirrored files:

- `web/src/contract.ts` (TypeScript)
- `src/thirteen_labs/contracts.py` (Pydantic)

Provider ports (`DefenderDriver`, `TraceSink`, `AuthorizationGate`) live in `src/thirteen_labs/ports.py`. The design-preview fixture is `web/src/fixtures/preview-run.json`; it is labeled `mode: "fixture"` and is never a measurement. Contract, manifest and lockfile changes go through one owner (Firstmate) so both mirrors stay in sync.

## Lanes and file ownership

| ID | Lane | Branch | Owns |
| --- | --- | --- | --- |
| C0 | Contract and scaffold | `main` | manifests and lockfiles, `contract.ts`, `contracts.py`, `ports.py`, `app.py`, `routes/__init__.py`, fixture, root config |
| E1 | Engine and runs API | `feat/shift-engine` | `src/thirteen_labs/{engine,scenarios,store}/`, `routes/runs.py`, `tests/engine/` |
| U1 | Probation UI | `feat/probation-ui` | `web/src/{App.tsx,api.ts,views,components,styles,assets}/`, UI tests |
| V1 | ElevenLabs voice | `feat/elevenlabs` | `integrations/elevenlabs.py`, `routes/voice.py`, `web/src/voice/`, `tests/voice/` |
| T1 | TraceRoot evidence | `feat/trace-evidence` | `integrations/traceroot.py`, `tests/trace/`, trace setup notes |
| P1 | Alter and Expected Parrot | `feat/partner-receipts` | `integrations/alter.py`, `routes/ledger.py`, `scripts/personas/`, `src/thirteen_labs/personas/`, `tests/partners/` |
| R1 | Demo release | `ops/demo-release` | `Dockerfile`, `.dockerignore`, `scripts/smoke.sh`, `scripts/export_replay.py`, `tests/e2e/`, `docs/demo-release.md` |

Lane routers register automatically: add `routes/runs.py`, `routes/voice.py` or `routes/ledger.py` exposing a FastAPI `router`, and `routes/__init__.py` picks it up.


