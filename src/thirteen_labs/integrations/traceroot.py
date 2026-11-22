"""TraceRoot evidence exporter (T1).

One OTLP trace per (run, case, version) case leg with child spans for the call,
each tool call, the authorization boundary and the outcome. Durable local
events remain the source of truth: observe() is fail-open and never raises
into the business state machine.

Export uses the documented TraceRoot OTLP ingest endpoint
(`POST {host}/api/v1/public/traces`, OTLP protobuf, `Authorization: Bearer
<key>`) through opentelemetry-exporter-otlp-proto-http. The configured
OTEL_EXPORTER_OTLP_ENDPOINT is the full traces URL - TraceRoot cloud defaults
to https://app.traceroot.ai/api/v1/public/traces; a generic collector uses its
own path such as http://localhost:4318/v1/traces.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime

from opentelemetry import trace as trace_api
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.trace import Status, StatusCode

from thirteen_labs.contracts import RunEvent

logger = logging.getLogger(__name__)

SERVICE_NAME = "thirteen-labs"
DEFAULT_ENDPOINT = "https://app.traceroot.ai/api/v1/public/traces"
MAX_SUMMARY = 500


def _ns(iso: str) -> int | None:
    try:
        return int(datetime.fromisoformat(iso).timestamp() * 1e9)
    except (TypeError, ValueError):
        return None


def _evidence(event: RunEvent) -> dict[str, str]:
    return {item.label: item.value for item in event.evidence or []}


@dataclass
class _CaseTrace:
    root: trace_api.Span
    trace_id: str
    tools: dict[str, trace_api.Span] = field(default_factory=dict)
    authorization: dict[str, trace_api.Span] = field(default_factory=dict)
    ended: bool = False


class TraceRootSink:
    """TraceSink port: best-effort OTLP export, fail-open by contract."""

    def __init__(
        self,
        api_key: str | None = None,
        endpoint: str | None = None,
        *,
        service_name: str = SERVICE_NAME,
        exporter=None,
        dashboard_url: str | None = None,
    ):
        self._lock = threading.Lock()
        self._traces: dict[tuple[str, str, str], _CaseTrace] = {}
        self._refs: dict[tuple[str, str, str], dict] = {}
        self._dashboard_url = dashboard_url
        self._provider = self._build_provider(api_key, endpoint, service_name, exporter)
        self._tracer = (
            self._provider.get_tracer("thirteen_labs.integrations.traceroot")
            if self._provider is not None else None
        )

    @staticmethod
    def _build_provider(api_key, endpoint, service_name, exporter) -> TracerProvider | None:
        batch = exporter is None
        if batch:
            import os

            api_key = api_key or os.environ.get("TRACEROOT_API_KEY", "").strip()
            endpoint = endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
            if not api_key and not endpoint:
                return None
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

                exporter = OTLPSpanExporter(
                    endpoint=endpoint or DEFAULT_ENDPOINT,
                    headers={"Authorization": f"Bearer {api_key}"} if api_key else None,
                )
            except Exception:
                logger.warning("TraceRoot exporter unavailable; tracing disabled.", exc_info=True)
                return None
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(
            BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter)
        )
        return provider

    @property
    def enabled(self) -> bool:
        return self._tracer is not None

    def observe(self, event: RunEvent) -> None:
        """Best effort. Never raises into the business state machine."""
        try:
            self._observe(event)
        except Exception:
            logger.debug("Trace export skipped for event %s", getattr(event, "id", "?"), exc_info=True)

    def trace_ref(self, run_id: str, case_id: str, version: str) -> dict | None:
        """Provider trace reference for E1 to store as CaseResult.trace."""
        ref = self._refs.get((run_id, case_id, version))
        return dict(ref) if ref else None

    def close(self) -> None:
        """End any case legs left open (interrupted runs) and flush the exporter."""
        try:
            with self._lock:
                for case in self._traces.values():
                    self._end_trace(case, None)
                self._traces.clear()
            if self._provider is not None:
                self._provider.force_flush()
                self._provider.shutdown()
        except Exception:
            logger.debug("Trace exporter shutdown failed", exc_info=True)

    def flush(self) -> None:
        """Flush buffered spans without ending open traces (live proof checks)."""
        try:
            if self._provider is not None:
                self._provider.force_flush()
        except Exception:
            logger.debug("Trace exporter flush failed", exc_info=True)

    # -- internals ----------------------------------------------------------

    def _observe(self, event: RunEvent) -> None:
        if self._tracer is None or event.case_id is None or event.version is None:
            return
        key = (event.run_id, event.case_id, event.version)
        with self._lock:
            case = self._traces.get(key)
            if case is None:
                if key in self._refs:
                    return
                case = self._open_trace(event)
                self._traces[key] = case
            elif case.ended:
                return
            match event.type:
                case "message":
                    case.root.add_event("message", self._event_attrs(event))
                case "tool_called":
                    self._open_child(case, event, f"tool.{event.tool or 'call'}", case.tools)
                case "tool_returned":
                    self._close_tool(case, event)
                case "refund_attempted":
                    self._open_authorization(case, event)
                case "authorization_decided":
                    self._close_authorization(case, event)
                case "ledger_written":
                    self._instant(case, event, "ledger.write",
                                  {"thirteen.executed.cents": _evidence(event).get("Executed cents", "")},
                                  status="executed")
                case "case_scored":
                    self._instant(case, event, "case.outcome", {}, status=self._outcome_status(event))
                    self._end_trace(case, event)
                    self._traces.pop(key, None)
                case _:
                    case.root.add_event(event.type, self._event_attrs(event))

    def _open_trace(self, event: RunEvent) -> _CaseTrace:
        root = self._tracer.start_span(
            f"case/{event.case_id}", context=Context(),
            start_time=_ns(event.at), attributes=self._base_attrs(event) | {"status": "open"},
        )
        trace_id = format(root.get_span_context().trace_id, "032x")
        ref = {"provider": "traceroot", "traceId": trace_id}
        if self._dashboard_url:
            ref["url"] = self._dashboard_url.replace("{trace_id}", trace_id)
        self._refs[(event.run_id, event.case_id, event.version)] = ref
        return _CaseTrace(root=root, trace_id=trace_id)

    def _open_child(self, case: _CaseTrace, event: RunEvent, name: str, registry: dict) -> trace_api.Span:
        span = self._tracer.start_span(
            name, context=trace_api.set_span_in_context(case.root),
            start_time=_ns(event.at), attributes=self._base_attrs(event),
        )
        registry[event.correlation_id or event.id] = span
        return span

    def _open_authorization(self, case: _CaseTrace, event: RunEvent) -> None:
        parent = case.tools.get(event.correlation_id or "", case.root)
        span = self._tracer.start_span(
            "refund.authorization", context=trace_api.set_span_in_context(parent),
            start_time=_ns(event.at),
            attributes=self._base_attrs(event)
            | {"thirteen.requested.cents": _evidence(event).get("Requested cents", "")},
        )
        case.authorization[event.correlation_id or event.id] = span

    def _close_tool(self, case: _CaseTrace, event: RunEvent) -> None:
        span = case.tools.pop(event.correlation_id or "", None)
        if span is None:
            case.root.add_event("tool_returned", self._event_attrs(event))
            return
        result = _evidence(event).get("Result")
        status = f"error:{result}" if result else "returned"
        span.set_attribute("status", status)
        span.set_attribute("thirteen.return.event.id", event.id)
        evidence = event.evidence or []
        if evidence:
            span.set_attribute("thirteen.evidence", json.dumps(
                [{"label": item.label, "value": item.value} for item in evidence]))
        span.set_status(Status(StatusCode.ERROR if result else StatusCode.OK))
        span.end(_ns(event.at))

    def _close_authorization(self, case: _CaseTrace, event: RunEvent) -> None:
        span = case.authorization.pop(event.correlation_id or "", None)
        if span is None:
            case.root.add_event("authorization_decided", self._event_attrs(event))
            return
        decision = _evidence(event).get("Decision", "unavailable")
        span.set_attribute("status", decision)
        if receipt := _evidence(event).get("Authorization receipt"):
            span.set_attribute("thirteen.authorization.receipt", receipt)
        span.set_status(Status(StatusCode.ERROR if decision == "unavailable" else StatusCode.OK))
        span.end(_ns(event.at))

    def _instant(self, case: _CaseTrace, event: RunEvent, name: str, extra: dict, *, status: str) -> None:
        parent = case.tools.get(event.correlation_id or "", case.root)
        span = self._tracer.start_span(
            name, context=trace_api.set_span_in_context(parent),
            start_time=_ns(event.at), attributes=self._base_attrs(event) | extra | {"status": status},
        )
        span.end(_ns(event.at))

    @staticmethod
    def _outcome_status(event: RunEvent) -> str:
        return "incomplete" if "incomplete" in event.label.lower() else "scored"

    def _end_trace(self, case: _CaseTrace, event: RunEvent | None) -> None:
        end_time = _ns(event.at) if event is not None else None
        status = self._outcome_status(event) if event is not None else "interrupted"
        for span in case.authorization.values():
            span.set_attribute("status", status)
            span.end(end_time)
        for span in case.tools.values():
            span.set_attribute("status", status)
            span.end(end_time)
        case.authorization.clear()
        case.tools.clear()
        case.root.set_attribute("status", status)
        if status == "incomplete":
            case.root.set_status(Status(StatusCode.ERROR))
        case.root.end(end_time)
        case.ended = True

    @staticmethod
    def _base_attrs(event: RunEvent) -> dict:
        attrs = {
            "run.id": event.run_id, "case.id": event.case_id,
            "defender.version": event.version, "event.id": event.id,
            "thirteen.event.type": event.type, "thirteen.event.seq": event.seq,
            "thirteen.actor": event.actor, "thirteen.stage": event.stage,
        }
        if event.correlation_id:
            attrs["thirteen.correlation.id"] = event.correlation_id
        if event.tool:
            attrs["thirteen.tool"] = event.tool
        return attrs

    @staticmethod
    def _event_attrs(event: RunEvent) -> dict:
        attrs = TraceRootSink._base_attrs(event)
        attrs["thirteen.summary"] = event.summary[:MAX_SUMMARY]
        if event.source_trust:
            attrs["thirteen.source.trust"] = event.source_trust
        return attrs


def sink_from_settings(settings) -> TraceRootSink | None:
    """App wiring helper: a live sink when tracing is configured, else None."""
    api_key = getattr(settings, "traceroot_api_key", "")
    endpoint = getattr(settings, "otel_exporter_otlp_endpoint", "")
    if not (api_key or endpoint):
        return None
    return TraceRootSink(api_key=api_key, endpoint=endpoint or None)
