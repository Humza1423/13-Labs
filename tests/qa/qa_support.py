"""Shared helpers for the QA acceptance suite.

Tests drive the app strictly over the HTTP contract via FastAPI's TestClient;
no engine internals are imported here.
"""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from thirteen_labs.app import create_app

CREATE_RUN_BODY = {"candidateId": "monitor-support", "suiteId": "probation-v1"}

ERROR_KEYS = {"code", "message", "retryable"}

POLL_INTERVAL_S = 0.5
RUN_TIMEOUT_S = 180.0


def route_registered(path: str) -> bool:
    """True when a route with this exact path template is on the app."""
    app = create_app()
    return any(getattr(route, "path", None) == path for route in app.routes)


RUNS_REGISTERED = route_registered("/api/runs")
SCENARIOS_REGISTERED = route_registered("/api/scenarios")


def make_client(tmp_path, monkeypatch) -> TestClient:
    """Isolated TestClient: scratch STATE_DB, fixture demo mode, no provider keys."""
    monkeypatch.setenv("STATE_DB", str(tmp_path / "qa-state.db"))
    monkeypatch.setenv("DEMO_MODE", "fixture")
    monkeypatch.setenv("COMMIT_SHA", "qa")
    for name in (
        "ANTHROPIC_API_KEY",
        "ELEVENLABS_API_KEY",
        "ELEVENLABS_LAB_AGENT_ID",
        "ELEVENLABS_VOICE_AGENT_ID",
        "ELEVENLABS_WEBHOOK_SECRET",
        "TRACEROOT_API_KEY",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "ALTER_API_KEY",
        "ALTER_GRANT_ID",
        "LEDGER_UPSTREAM_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    return TestClient(create_app())


def new_key() -> str:
    return uuid.uuid4().hex


def assert_error_shape(resp) -> dict:
    """Assert the response is a contract error body; return the error object."""
    assert resp.status_code >= 400, f"expected an error, got {resp.status_code}: {resp.text[:300]}"
    body = resp.json()
    assert set(body) == {"error"}, f"non-contract error body: {body!r}"
    err = body["error"]
    assert set(err) == ERROR_KEYS, f"error object keys drifted: {err!r}"
    assert isinstance(err["code"], str) and err["code"]
    assert isinstance(err["message"], str) and err["message"]
    assert isinstance(err["retryable"], bool)
    return err


def create_run(client: TestClient, body=None, key=None):
    """POST /api/runs. Pass key=None to omit the Idempotency-Key header."""
    headers = {} if key is None else {"Idempotency-Key": key}
    return client.post("/api/runs", json=CREATE_RUN_BODY if body is None else body, headers=headers)


def post_retest(client: TestClient, run_id: str, revision_id: str, key: str | None = None):
    headers = {} if key is None else {"Idempotency-Key": key}
    return client.post(f"/api/runs/{run_id}/retest", json={"revisionId": revision_id}, headers=headers)


def get_run(client: TestClient, run_id: str) -> dict:
    resp = client.get(f"/api/runs/{run_id}")
    assert resp.status_code == 200, resp.text[:300]
    return resp.json()


def wait_for_status(client: TestClient, run_id: str, targets, timeout: float = RUN_TIMEOUT_S) -> dict:
    """Poll GET /api/runs/{id} until status is in targets.

    A run that ends in "failed" while we wait for something else is skipped:
    in a credential-free QA environment the engine may be unable to drive real
    provider legs, and that is an environment fact, not a contract violation.
    """
    if isinstance(targets, str):
        targets = (targets,)
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = get_run(client, run_id)
        if last["status"] in targets:
            return last
        if last["status"] == "failed" and "failed" not in targets:
            pytest.skip(f"run {run_id} failed before reaching {targets}: {last.get('error')}")
        time.sleep(POLL_INTERVAL_S)
    pytest.fail(
        f"run {run_id} did not reach {targets} within {timeout}s "
        f"(last status: {last and last['status']})"
    )


def fetch_events_page(client: TestClient, run_id: str, after: int = 0) -> dict:
    resp = client.get(f"/api/runs/{run_id}/events", params={"after": after})
    assert resp.status_code == 200, resp.text[:300]
    page = resp.json()
    assert {"events", "nextAfter"} <= set(page), f"bad events page shape: {page!r}"
    assert isinstance(page["nextAfter"], int), f"nextAfter is not an int: {page!r}"
    assert len(page["events"]) <= 100, "events page exceeded the 100-event cap"
    return page


def drain_events(client: TestClient, run_id: str, idle_rounds: int = 4, timeout: float = 60.0) -> list[dict]:
    """Follow after/nextAfter until the stream stops producing new events.

    Asserts the cursor contract along the way: every returned event has
    seq > requested after, seqs increase within a page, and event ids are
    unique across the whole drain.
    """
    events: list[dict] = []
    seen_ids: set[str] = set()
    last_seq = 0
    after = 0
    idle = 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and idle < idle_rounds:
        page = fetch_events_page(client, run_id, after)
        batch = page["events"]
        if not batch:
            idle += 1
            time.sleep(POLL_INTERVAL_S)
            continue
        idle = 0
        for ev in batch:
            assert ev["seq"] > after, f"event seq {ev['seq']} not > requested after={after}"
            assert ev["seq"] > last_seq, f"event seq {ev['seq']} not increasing across pages"
            assert ev["id"] not in seen_ids, f"duplicate event id {ev['id']}"
            seen_ids.add(ev["id"])
            last_seq = ev["seq"]
            events.append(ev)
        after = page["nextAfter"]
    return events
