"""Contract-level acceptance checks that run on main today.

Covers the C0 contract surface: health shape, the shared error body, the
preview fixture round-trip, and the TypeScript/Python literal mirror.
"""

from __future__ import annotations

import json
import re
import typing
from pathlib import Path

from fastapi.testclient import TestClient

from thirteen_labs.app import create_app
from thirteen_labs import contracts
from thirteen_labs.contracts import PreviewFixture

from tests.qa.qa_support import ERROR_KEYS, assert_error_shape

ROOT = Path(__file__).resolve().parents[2]


def test_health_shape():
    client = TestClient(create_app())
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["commit"], str)
    assert set(body["capabilities"]) == {"batch", "voice", "traces", "alter", "personas"}
    assert all(isinstance(v, bool) for v in body["capabilities"].values())


def test_unknown_api_route_uses_error_shape():
    client = TestClient(create_app())
    resp = client.get("/api/definitely-not-a-route")
    assert resp.status_code == 404
    err = assert_error_shape(resp)
    assert err["retryable"] is False


def test_preview_fixture_matches_contract_and_round_trips():
    raw = json.loads((ROOT / "web/src/fixtures/preview-run.json").read_text())
    fixture = PreviewFixture.model_validate(raw)
    seqs = [e.seq for e in fixture.events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    ids = {e.id for e in fixture.events}
    for case in fixture.run.cases:
        assert set(case.evidence_ids) <= ids
    assert fixture.dump() == raw


_TS_UNION_NAMES = [
    "RunStatus",
    "RunMode",
    "Version",
    "Outcome",
    "AuthDecision",
    "Stage",
    "EventType",
    "Actor",
    "SourceTrust",
    "ToolName",
    "CarrierCheck",
    "CaseId",
    "PatchChange",
]


def _ts_union_literals(source: str, name: str) -> set[str]:
    """Pull the quoted members out of `export type Name = "a" | "b" | ...;`."""
    match = re.search(rf"export type {name}\s*=\s*(.*?);", source, re.S)
    assert match, f"{name} not found as a type alias in contract.ts"
    literals = set(re.findall(r'"([^"]+)"', match.group(1)))
    assert literals, f"{name} parsed to no string literals"
    return literals


def test_typescript_contract_mirrors_python_literals():
    """Every enum-like Literal in contracts.py matches its contract.ts twin."""
    source = (ROOT / "web/src/contract.ts").read_text()
    for name in _TS_UNION_NAMES:
        py_literals = set(typing.get_args(getattr(contracts, name)))
        ts_literals = _ts_union_literals(source, name)
        assert py_literals == ts_literals, f"{name} drifted: py={sorted(py_literals)} ts={sorted(ts_literals)}"


def test_error_body_contract_model():
    """ErrorResponse stays {error:{code,message,retryable}} end to end."""
    body = contracts.ErrorResponse(
        error=contracts.ApiErrorBody(code="x", message="y", retryable=True)
    ).dump()
    assert set(body) == {"error"}
    assert set(body["error"]) == ERROR_KEYS
