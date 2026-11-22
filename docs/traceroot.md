# TraceRoot evidence setup (T1)

`src/thirteen_labs/integrations/traceroot.py` implements the `TraceSink` port
from `ports.py`. The SQLite event log stays the source of truth; the sink only
exports. `observe()` never raises, and a run keeps complete local evidence when
export is unavailable.

## Configuration

| Name | Value |
| --- | --- |
| `TRACEROOT_API_KEY` | Project API key from traceroot Cloud or a self-hosted deployment. |
| `TRACEROOT_DASHBOARD_URL` | Optional public HTTP(S) dashboard link template containing `{trace_id}`. Use the canonical URL returned by the project's TraceRoot read API and substitute the trace ID. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Full OTLP traces URL. Default `https://app.traceroot.ai/api/v1/public/traces`. Self-hosted: `http://<host>:8000/api/v1/public/traces`. Any OTLP/HTTP protobuf receiver works, e.g. `http://localhost:4318/v1/traces` (key may then be empty). |

The sink sends `Authorization: Bearer <TRACEROOT_API_KEY>` and OTLP protobuf to
that URL, matching the documented ingest route
(`POST /api/v1/public/traces`). Export batches via `BatchSpanProcessor` and
flushes on `TraceRootSink.close()`.

## Span model

One trace per `(run.id, case.id, defender.version)` case leg. Children:
`tool.<name>` per accepted call, `refund.authorization` per refund action,
`ledger.write` on execution and `case.outcome` on scoring. Every span carries
`run.id`, `case.id`, `defender.version`, `event.id` and `status`; real event
timestamps are used, so the trace replays the recorded sequence. Run-level
events with no case (phase changes, finding, revision) stay local-only.

The engine persists each case's OpenTelemetry trace ID as `CaseResult.trace` and
includes the dashboard link when configured. Trace IDs correlate locally
recorded events with spans. Export is asynchronous and can fail. Confirm
delivery with the provider read API or UI before reporting a verified trace.

## Verification

- `uv run pytest tests/trace -q` — in-memory exporter pins span tree,
  attributes, version correlation and fail-open export.
- Live proof needs a real `TRACEROOT_API_KEY` and E1's engine running one
  baseline leg and one revised leg of the same case; compare the two exported
  traces' `case.id`/`defender.version` attributes and record both trace IDs
  next to the run evidence.
