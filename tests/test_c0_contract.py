"""C0 contract checks: health route, fixture validates against the Pydantic mirror."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from thirteen_labs.app import create_app
from thirteen_labs.contracts import PreviewFixture
from thirteen_labs.ports import AuthorizationGate, LocalAuthorizationGate, NullTraceSink, TraceSink

ROOT = Path(__file__).resolve().parents[1]


def test_health_shape():
    client = TestClient(create_app())
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert set(body["capabilities"]) == {"batch", "voice", "traces", "alter", "personas"}
    assert all(isinstance(v, bool) for v in body["capabilities"].values())


def test_unknown_api_route_uses_error_shape():
    client = TestClient(create_app())
    resp = client.get("/api/does-not-exist")
    assert resp.status_code == 404
    assert set(resp.json()["error"]) == {"code", "message", "retryable"}


def test_preview_fixture_matches_contract():
    raw = json.loads((ROOT / "web/src/fixtures/preview-run.json").read_text())
    fixture = PreviewFixture.model_validate(raw)
    assert fixture.run.mode == "fixture"
    assert all(c.source == "fixture" for c in fixture.run.cases)
    seqs = [e.seq for e in fixture.events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    ids = {e.id for e in fixture.events}
    for case in fixture.run.cases:
        assert set(case.evidence_ids) <= ids
    # Round-trips back to identical camelCase JSON.
    assert fixture.dump() == raw


def test_local_port_defaults_satisfy_protocols():
    assert isinstance(NullTraceSink(), TraceSink)
    assert isinstance(LocalAuthorizationGate(), AuthorizationGate)
