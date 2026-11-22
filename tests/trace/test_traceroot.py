"""In-memory OTLP proof for the TraceRoot sink (T1).

Live TraceRoot proof additionally needs a real TRACEROOT_API_KEY; these tests
pin the span structure, attributes and fail-open contract against the durable
event stream the engine emits.
"""

import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from thirteen_labs.app import create_app
from thirteen_labs.config import Settings
from thirteen_labs.contracts import EvidenceField, RunEvent
from thirteen_labs.integrations.traceroot import TraceRootSink, sink_from_settings

SELECTION = {"candidateId": "monitor-support", "suiteId": "probation-v1"}
SEQ = [0]


def event(type, *, case="monitor-valid", version="baseline", correlation=None, tool=None,
          actor="defender", label="", summary="", evidence=None, trust="system", stage="baseline"):
    SEQ[0] += 1
    return RunEvent(
        id=f"ev-{SEQ[0]}", seq=SEQ[0], at=datetime.now(UTC).isoformat(), run_id="run-1",
        case_id=case, version=version, stage=stage, type=type, actor=actor,
        label=label or type, summary=summary or type, correlation_id=correlation,
        source_trust=trust, tool=tool,
        evidence=[EvidenceField(label=k, value=v) for k, v in (evidence or {}).items()] or None,
    )


def sink_and_spans(**kwargs):
    exporter = InMemorySpanExporter()
    return TraceRootSink(exporter=exporter, **kwargs), exporter


def spans_by_name(exporter, name):
    return [span for span in exporter.get_finished_spans() if span.name == name]


def parents(exporter):
    by_id = {span.get_span_context().span_id: span for span in exporter.get_finished_spans()}
    return {id(span): by_id.get(span.parent.span_id) for span in exporter.get_finished_spans() if span.parent}


def test_case_leg_exports_one_trace_with_child_spans():
    sink, exporter = sink_and_spans()
    order = {"orderId": "ORD-1302"}
    sink.observe(event("message", actor="customer", trust="customer_supplied"))
    sink.observe(event("tool_called", correlation="lookup", tool="lookup_order"))
    sink.observe(event("tool_returned", correlation="lookup", tool="lookup_order",
                       trust="authoritative", evidence=order))
    sink.observe(event("tool_called", correlation="carrier", tool="verify_carrier_report"))
    sink.observe(event("tool_returned", correlation="carrier", tool="verify_carrier_report",
                       trust="authoritative", evidence={"Carrier check": "matched"}))
    sink.observe(event("tool_called", correlation="refund", tool="request_refund"))
    sink.observe(event("refund_attempted", correlation="refund", label="Refund requested",
                       evidence={"Requested cents": "90000", "Action ID": "act-1"}))
    sink.observe(event("authorization_decided", correlation="refund", actor="authorization",
                       evidence={"Decision": "allowed", "Action ID": "act-1"}))
    sink.observe(event("ledger_written", correlation="refund", actor="authorization",
                       evidence={"Executed cents": "90000"}))
    sink.observe(event("tool_returned", correlation="refund", tool="request_refund",
                       trust="authoritative"))
    sink.observe(event("tool_called", correlation="resolution", tool="record_resolution"))
    sink.observe(event("tool_returned", correlation="resolution", tool="record_resolution"))
    sink.observe(event("case_scored", actor="evaluator", label="Case evaluated",
                       summary="The outcome met the case criteria."))

    finished = exporter.get_finished_spans()
    assert finished
    trace_ids = {span.get_span_context().trace_id for span in finished}
    assert len(trace_ids) == 1
    root = spans_by_name(exporter, "case/monitor-valid")[0]
    assert root.parent is None
    children = parents(exporter)
    for name in ("tool.lookup_order", "tool.verify_carrier_report", "tool.request_refund",
                 "tool.record_resolution", "case.outcome"):
        child = spans_by_name(exporter, name)[0]
        assert children[id(child)] is root, name
    tool_span = spans_by_name(exporter, "tool.request_refund")[0]
    for name in ("refund.authorization", "ledger.write"):
        child = spans_by_name(exporter, name)[0]
        assert children[id(child)] is tool_span, name
    authorization = spans_by_name(exporter, "refund.authorization")[0]
    assert authorization.attributes["status"] == "allowed"
    assert tool_span.attributes["thirteen.tool"] == "request_refund"
    for span in finished:
        assert span.attributes["run.id"] == "run-1"
        assert span.attributes["case.id"] == "monitor-valid"
        assert span.attributes["defender.version"] == "baseline"
        assert span.attributes["event.id"]
    ref = sink.trace_ref("run-1", "monitor-valid", "baseline")
    assert ref == {
        "provider": "traceroot",
        "traceId": format(root.get_span_context().trace_id, "032x"),
    }
    sink.close()


def test_versions_export_distinct_correlated_traces():
    sink, exporter = sink_and_spans()
    for version in ("baseline", "revised"):
        sink.observe(event("tool_called", version=version, correlation="lookup", tool="lookup_order"))
        sink.observe(event("tool_returned", version=version, correlation="lookup", tool="lookup_order"))
        sink.observe(event("case_scored", version=version, actor="evaluator", label="Case evaluated"))
    roots = spans_by_name(exporter, "case/monitor-valid")
    assert len(roots) == 2
    versions = {root.attributes["defender.version"] for root in roots}
    assert versions == {"baseline", "revised"}
    assert len({root.get_span_context().trace_id for root in roots}) == 2
    baseline_ref = sink.trace_ref("run-1", "monitor-valid", "baseline")
    revised_ref = sink.trace_ref("run-1", "monitor-valid", "revised")
    assert baseline_ref["traceId"] != revised_ref["traceId"]
    sink.close()


def test_contained_denied_attempt_is_still_recorded_as_an_attempt():
    sink, exporter = sink_and_spans()
    sink.observe(event("tool_called", case="monitor-conflict", correlation="refund",
                       tool="request_refund"))
    sink.observe(event("refund_attempted", case="monitor-conflict", correlation="refund",
                       evidence={"Requested cents": "90000"}))
    sink.observe(event("authorization_decided", case="monitor-conflict", correlation="refund",
                       actor="authorization", evidence={"Decision": "denied"}))
    sink.observe(event("tool_returned", case="monitor-conflict", correlation="refund",
                       tool="request_refund"))
    sink.observe(event("case_scored", case="monitor-conflict", actor="evaluator",
                       label="Case evaluated"))
    authorization = spans_by_name(exporter, "refund.authorization")[0]
    assert authorization.attributes["status"] == "denied"
    assert not spans_by_name(exporter, "ledger.write")
    sink.close()


def test_observe_never_raises_and_outlives_exporter_errors():
    class BrokenExporter(SpanExporter):
        def export(self, spans):
            raise RuntimeError("exporter-internal diagnostic")

        def shutdown(self):
            pass

    sink = TraceRootSink(exporter=BrokenExporter())
    for _ in range(3):
        sink.observe(event("tool_called", correlation="lookup", tool="lookup_order"))
        sink.observe(event("tool_returned", correlation="lookup", tool="lookup_order"))
        sink.observe(event("tool_returned", correlation="orphan", tool="lookup_order"))
        sink.observe(event("case_scored", actor="evaluator", label="Case incomplete"))
    sink.close()


def test_sink_disabled_without_configuration(monkeypatch):
    monkeypatch.delenv("TRACEROOT_API_KEY", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    sink = TraceRootSink()
    assert not sink.enabled
    sink.observe(event("tool_called", correlation="lookup", tool="lookup_order"))
    assert sink.trace_ref("run-1", "monitor-valid", "baseline") is None
    settings = Settings()
    assert sink_from_settings(settings) is None
    sink.close()


def test_dashboard_url_template():
    sink, _ = sink_and_spans(dashboard_url="https://app.traceroot.ai/traces/{trace_id}")
    sink.observe(event("case_scored", actor="evaluator", label="Case evaluated"))
    ref = sink.trace_ref("run-1", "monitor-valid", "baseline")
    assert ref["url"].startswith("https://app.traceroot.ai/traces/")
    assert ref["url"].endswith(ref["traceId"])
    sink.close()


@pytest.mark.parametrize("template", [
    "https://example.test/",
    "javascript:open('{trace_id}')",
    "https://reader:secret@example.test/traces/{trace_id}",
])
def test_dashboard_reference_omits_unusable_templates(template):
    sink, _ = sink_and_spans(dashboard_url=template)
    sink.observe(event("case_scored", actor="evaluator", label="Case evaluated"))
    ref = sink.trace_ref("run-1", "monitor-valid", "baseline")
    assert len(ref["traceId"]) == 32 and "url" not in ref
    sink.close()


def await_status(client, path, status):
    for _ in range(150):
        response = client.get(path)
        assert response.status_code == 200, response.text
        data = response.json()
        if data["status"] in {status, "failed"}:
            assert data["status"] == status, data
            return data
        time.sleep(0.01)
    pytest.fail(f"Did not reach {status}: {data!r}")


def complete_fixture_shift(client):
    response = client.post(
        "/api/runs", json=SELECTION, headers={"Idempotency-Key": "trace-shift"})
    assert response.status_code == 202, response.text
    path = "/api/runs/" + response.json()["id"]
    baseline = await_status(client, path, "ready_to_retest")
    response = client.post(
        path + "/retest", json={"revisionId": baseline["revision"]["id"]},
        headers={"Idempotency-Key": "trace-retest"})
    assert response.status_code == 202, response.text
    complete = await_status(client, path, "complete")
    events = []
    cursor = 0
    while True:
        page = client.get(path + "/events", params={"after": cursor}).json()
        events.extend(page["events"])
        if page["nextAfter"] == cursor:
            break
        cursor = page["nextAfter"]
    return complete, events


def test_fixture_shift_instruments_actual_tool_calls_per_version(tmp_path):
    template = "https://example.test/projects/test-project/traces?traceId={trace_id}"
    sink, exporter = sink_and_spans(dashboard_url=template)
    settings = Settings(state_db=str(tmp_path / "traced.sqlite"), demo_mode="fixture")
    with TestClient(create_app(settings=settings, trace_sink=sink)) as client:
        complete, events = complete_fixture_shift(client)

    with TestClient(create_app(settings=settings)) as restarted:
        persisted_cases = restarted.get("/api/runs/" + complete["id"]).json()["cases"]
    assert persisted_cases == complete["cases"]
    roots = [span for span in exporter.get_finished_spans() if span.parent is None]
    legs = {(root.attributes["case.id"], root.attributes["defender.version"]) for root in roots}
    assert legs == {
        ("monitor-conflict", "baseline"), ("monitor-valid", "baseline"),
        ("monitor-incomplete", "baseline"),
        ("monitor-conflict", "revised"), ("monitor-valid", "revised"),
        ("monitor-incomplete", "revised"), ("monitor-heldout", "revised"),
    }
    run_id = complete["id"]
    for root in roots:
        assert root.attributes["run.id"] == run_id
    for case in complete["cases"]:
        ref = sink.trace_ref(run_id, case["caseId"], case["version"])
        assert case["trace"] == ref
        assert ref["provider"] == "traceroot" and len(ref["traceId"]) == 32
        assert ref["url"] == template.replace("{trace_id}", ref["traceId"])
        if case["caseId"] == "monitor-valid":
            refund = next(span for span in exporter.get_finished_spans()
                          if span.name == "tool.request_refund"
                          and format(span.context.trace_id, "032x") == ref["traceId"])
            call = next(event for event in events if event["id"] == refund.attributes["event.id"])
            assert call["type"] == "tool_called" and call["tool"] == "request_refund"
            assert call["id"] in case["evidenceIds"]
            assert call["runId"] == refund.attributes["run.id"] == run_id
            assert call["caseId"] == refund.attributes["case.id"] == "monitor-valid"
            assert call["version"] == refund.attributes["defender.version"] == case["version"]

    by_case = {}
    for span in exporter.get_finished_spans():
        by_case.setdefault((span.attributes["case.id"], span.attributes["defender.version"]), {})[
            span.name] = span
    conflict = by_case[("monitor-conflict", "baseline")]
    assert conflict["refund.authorization"].attributes["status"] == "denied"
    assert "ledger.write" not in conflict
    valid = by_case[("monitor-valid", "revised")]
    assert valid["refund.authorization"].attributes["status"] == "allowed"
    assert valid["ledger.write"].attributes["status"] == "executed"
    held_out = by_case[("monitor-heldout", "revised")]
    assert "refund.authorization" not in held_out
    assert held_out["case.outcome"].attributes["status"] == "scored"
    sink.close()


def test_export_failure_preserves_seven_legs_and_the_local_timeline(tmp_path):
    class UnavailableExporter(SpanExporter):
        attempts = 0

        def export(self, spans):
            self.attempts += 1
            return SpanExportResult.FAILURE

    exporter = UnavailableExporter()
    sink = TraceRootSink(exporter=exporter)
    settings = Settings(state_db=str(tmp_path / "outage.sqlite"), demo_mode="fixture")
    with TestClient(create_app(settings=settings, trace_sink=sink)) as client:
        complete, events = complete_fixture_shift(client)
    assert exporter.attempts > 0
    assert len(complete["cases"]) == 7
    assert all(case["outcome"] != "incomplete" for case in complete["cases"])
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert events[-1]["stage"] == "review"
    for case in complete["cases"]:
        if case["caseId"] == "monitor-valid":
            assert case["executedCents"] == 90_000
            assert any(event["id"] in case["evidenceIds"] and event["type"] == "ledger_written"
                       for event in events)


def test_app_wires_sink_only_when_tracing_is_configured(tmp_path, monkeypatch):
    configured = create_app(settings=Settings(
        state_db=str(tmp_path / "traced.sqlite"), demo_mode="fixture", traceroot_api_key="key",
        traceroot_dashboard_url="https://example.test/traces/{trace_id}"))
    with TestClient(configured) as client:
        engine = configured.state.shift_engine
        assert isinstance(engine.trace_sink, TraceRootSink)
        assert client.get("/api/health").json()["capabilities"]["traces"] is True
    injected, _ = sink_and_spans()
    overridden = create_app(
        settings=Settings(state_db=str(tmp_path / "injected.sqlite"), demo_mode="fixture",
                          traceroot_api_key="key"),
        trace_sink=injected)
    with TestClient(overridden):
        assert overridden.state.shift_engine.trace_sink is injected
    bare = create_app(settings=Settings(
        state_db=str(tmp_path / "bare.sqlite"), demo_mode="fixture",
        traceroot_api_key="", otel_exporter_otlp_endpoint=""))
    with TestClient(bare) as client:
        assert bare.state.shift_engine.trace_sink is None
        assert client.get("/api/health").json()["capabilities"]["traces"] is False
    monkeypatch.delenv("TRACEROOT_API_KEY", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    disabled = TraceRootSink()
    with TestClient(create_app(settings=configured.state.settings, trace_sink=disabled)) as client:
        assert client.get("/api/health").json()["capabilities"]["traces"] is False
    injected.close()
