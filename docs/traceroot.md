# TraceRoot evidence setup (T1)

`src/thirteen_labs/integrations/traceroot.py` implements the `TraceSink` port
from `ports.py`. The SQLite event log stays the source of truth; the sink only
exports. `observe()` never raises, and a run keeps complete local evidence when
export is unavailable.

## Configuration

| Name | Value |
| --- | --- |
| `TRACEROOT_API_KEY` | Project API key from traceroot Cloud or a self-hosted deployment. |
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

`sink.trace_ref(run_id, case_id, version)` returns
`{"provider": "traceroot", "traceId": <32-hex>, "url"?}` for E1 to store in
`CaseResult.trace`. `url` appears only when a `dashboard_url` template
containing `{trace_id}` is supplied — TraceRoot does not publish a fixed
per-trace URL format.

## Wiring (Firstmate lands in app.py)

```python
from .integrations.traceroot import sink_from_settings

trace_sink=trace_sink or sink_from_settings(settings),   # inside create_app
```

and in the lifespan shutdown, after `await engine.close()`:
`trace_sink.close()` — ends interrupted legs and flushes the exporter.

## Verification

- `uv run pytest tests/trace -q` — in-memory exporter pins span tree,
  attributes, version correlation and fail-open export.
- Live proof needs a real `TRACEROOT_API_KEY` and E1's engine running one
  baseline leg and one revised leg of the same case; compare the two exported
  traces' `case.id`/`defender.version` attributes and record both trace IDs
  next to the run evidence.
