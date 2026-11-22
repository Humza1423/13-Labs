"""Route-level acceptance tests for the runs API.

Every test in this module is skipped while /api/runs is not registered, so the
suite is green on main today and activates automatically when E1 merges.

Contract under test (implementation guide, "Contract Firstmate publishes first"
and "Release checks and judging"):

- POST /api/runs: 202 RunView, Idempotency-Key replay returns the same run,
  same key with a different input returns 409, header required.
- GET /api/runs/{id}: 404 + {error:{code,message,retryable}} for unknown ids.
- GET /api/runs/{id}/events?after=N: <=100 events per page, seq > after,
  strictly increasing seq, unique event ids, integer nextAfter cursor.
- POST /api/runs/{id}/retest: only from ready_to_retest, idempotent replay,
  stale revisionId -> 409.
- POST /api/runs/{id}/review: only after completion.
- Cache-Control: no-store on session-bearing responses; session cookies
  HTTP-only when set.
- Public projection never leaks answer keys, holdout inputs, evaluator truth,
  or signed voice URLs; held-out cases appear only in the revised version.
- Attempts and executions are reported separately: a denied attempt leaves
  executedCents at 0, an allowed valid refund executes exactly once.
"""

from __future__ import annotations

import re
from collections import Counter

import pytest

from tests.qa.qa_support import (
    RUNS_REGISTERED,
    SCENARIOS_REGISTERED,
    assert_error_shape,
    create_run,
    drain_events,
    fetch_events_page,
    get_run,
    make_client,
    new_key,
    post_retest,
    wait_for_status,
)

pytestmark = pytest.mark.skipif(
    not RUNS_REGISTERED, reason="/api/runs not registered yet (E1 not merged)"
)

RUN_STATUSES = {
    "queued",
    "baseline_running",
    "investigating",
    "ready_to_retest",
    "retest_running",
    "complete",
    "failed",
}

# Keys that must never appear anywhere in a public response payload.
FORBIDDEN_KEY = re.compile(
    r"(signed.?url|secret|api.?key|token|password|answer.?key|"
    r"expected.?outcome|holdout.?input|held.?out.?input|evaluator.?truth)",
    re.IGNORECASE,
)
# Values that would leak provider-side voice material.
FORBIDDEN_VALUE = re.compile(r"(elevenlabs\.io|signed_url|signature=|x-amz-signature)", re.IGNORECASE)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    c = make_client(tmp_path, monkeypatch)
    with c:  # run lifespan so engine workers start if the app uses them
        yield c


def _created_run_view(client) -> dict:
    resp = create_run(client, key=new_key())
    assert resp.status_code == 202, resp.text[:300]
    return resp.json()


def _assert_projection_clean(node, path: str = "$") -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            assert not FORBIDDEN_KEY.search(str(key)), f"forbidden key {key!r} at {path}"
            _assert_projection_clean(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _assert_projection_clean(value, f"{path}[{i}]")
    elif isinstance(node, str):
        assert not FORBIDDEN_VALUE.search(node), f"forbidden value at {path}: {node[:120]!r}"


# -- create / idempotency ----------------------------------------------------


def test_create_run_returns_202_runview(client):
    view = _created_run_view(client)
    for field in (
        "id",
        "status",
        "mode",
        "startedAt",
        "updatedAt",
        "candidate",
        "progress",
        "actions",
        "cases",
    ):
        assert field in view, view
    assert view["status"] in RUN_STATUSES
    assert set(view["actions"]) == {"canRetest", "canReview", "canStartVoice"}


def test_create_run_idempotent_same_key_same_body(client):
    key = new_key()
    first = create_run(client, key=key)
    second = create_run(client, key=key)
    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["id"] == second.json()["id"], "same key+body must replay the same run"


def test_create_run_same_key_different_body_conflicts(client):
    key = new_key()
    assert create_run(client, key=key).status_code == 202
    conflict = create_run(
        client, key=key, body={"candidateId": "someone-else", "suiteId": "probation-v1"}
    )
    assert conflict.status_code == 409, f"expected 409, got {conflict.status_code}: {conflict.text[:300]}"
    assert_error_shape(conflict)


def test_create_run_requires_idempotency_key(client):
    resp = create_run(client)  # no header
    assert_error_shape(resp)


def test_create_run_rejects_bad_body_with_error_shape(client):
    resp = create_run(client, body={"candidateId": "nope", "suiteId": "probation-v1"}, key=new_key())
    assert_error_shape(resp)


# -- get / unknown ids --------------------------------------------------------


def test_get_unknown_run_is_404(client):
    resp = client.get("/api/runs/qa-no-such-run")
    assert resp.status_code == 404
    assert_error_shape(resp)


def test_events_unknown_run_is_404(client):
    resp = client.get("/api/runs/qa-no-such-run/events", params={"after": 0})
    assert resp.status_code == 404
    assert_error_shape(resp)


def test_retest_unknown_run_is_404(client):
    resp = post_retest(client, "qa-no-such-run", "rev-x", key=new_key())
    assert resp.status_code == 404
    assert_error_shape(resp)


# -- events paging ------------------------------------------------------------


def test_events_paging_invariants(client):
    run = _created_run_view(client)
    events = drain_events(client, run["id"])
    for ev in events:
        assert ev["runId"] == run["id"]
    if events:
        mid = events[len(events) // 2]["seq"]
        page = fetch_events_page(client, run["id"], after=mid)
        assert all(e["seq"] > mid for e in page["events"]), "after cursor not honored"
        first = fetch_events_page(client, run["id"], after=0)
        if first["events"]:
            assert first["events"][0]["seq"] == events[0]["seq"], "after=0 must start at the beginning"


# -- retest -------------------------------------------------------------------


def test_retest_rejected_before_ready_to_retest(client):
    run = _created_run_view(client)
    view = get_run(client, run["id"])
    if view["status"] not in {"queued", "baseline_running", "investigating"}:
        pytest.skip(f"run advanced to {view['status']} before the early-reject check")
    assert view["actions"]["canRetest"] is False
    resp = post_retest(client, run["id"], "rev-anything", key=new_key())
    assert_error_shape(resp)


def test_retest_accepts_then_rejects_stale_revision(client):
    run = _created_run_view(client)
    view = wait_for_status(client, run["id"], "ready_to_retest")
    revision = view.get("revision")
    assert revision and revision.get("id"), "ready_to_retest without a revision"

    bogus = post_retest(client, run["id"], "rev-does-not-exist", key=new_key())
    assert bogus.status_code == 409, f"expected 409 for bogus revision, got {bogus.status_code}"
    assert_error_shape(bogus)

    key = new_key()
    ok = post_retest(client, run["id"], revision["id"], key=key)
    assert ok.status_code == 202, ok.text[:300]
    replay = post_retest(client, run["id"], revision["id"], key=key)
    assert replay.status_code == 202 and replay.json()["id"] == run["id"], (
        "same idempotency key must replay the retest result"
    )

    wait_for_status(client, run["id"], "complete")
    stale = post_retest(client, run["id"], revision["id"], key=new_key())
    assert stale.status_code == 409, (
        f"consumed revision must be stale after retest, got {stale.status_code}"
    )
    assert_error_shape(stale)


# -- review -------------------------------------------------------------------


def test_review_only_after_completion(client):
    run = _created_run_view(client)
    early = client.post(f"/api/runs/{run['id']}/review", json={"decision": "reviewed"})
    assert_error_shape(early)

    view = wait_for_status(client, run["id"], "ready_to_retest")
    revision = view.get("revision")
    assert revision and revision.get("id"), "ready_to_retest without a revision"
    assert post_retest(client, run["id"], revision["id"], key=new_key()).status_code == 202
    wait_for_status(client, run["id"], "complete")

    done = client.post(f"/api/runs/{run['id']}/review", json={"decision": "reviewed"})
    assert done.status_code in (200, 202), done.text[:300]
    assert done.json()["review"]["status"] == "reviewed"


# -- transport hygiene ---------------------------------------------------------


def test_session_responses_are_no_store(client):
    created = create_run(client, key=new_key())
    assert created.status_code == 202
    run_id = created.json()["id"]
    for resp in (created, client.get(f"/api/runs/{run_id}")):
        cc = resp.headers.get("cache-control", "")
        assert "no-store" in cc.lower(), f"missing no-store on {resp.request.url.path}: {cc!r}"
    cookie = created.headers.get("set-cookie", "")
    if cookie:
        assert "httponly" in cookie.lower(), f"session cookie not HTTP-only: {cookie!r}"


# -- public projection ---------------------------------------------------------


def test_run_view_and_events_leak_nothing_hidden(client):
    run = _created_run_view(client)
    view = wait_for_status(client, run["id"], ("ready_to_retest", "complete"))
    _assert_projection_clean(view, path="$.run")
    events = drain_events(client, run["id"])
    _assert_projection_clean(events, path="$.events")


@pytest.mark.skipif(not SCENARIOS_REGISTERED, reason="/api/scenarios not registered yet")
def test_scenarios_projection_omits_answer_key(client):
    resp = client.get("/api/scenarios")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["candidate"]) <= {"id", "name"}
    for scenario in body["scenarios"]:
        assert set(scenario) <= {"id", "label", "description"}, (
            f"scenario leaks non-public fields: {sorted(scenario)}"
        )
    _assert_projection_clean(body, path="$.scenarios")


def test_held_out_case_only_in_revised_version(client):
    run = _created_run_view(client)
    view = wait_for_status(client, run["id"], "ready_to_retest")
    baseline_ids = {c["caseId"] for c in view["cases"] if c["version"] == "baseline"}
    assert "monitor-heldout" not in baseline_ids, "held-out case leaked into baseline"
    for case in view["cases"]:
        if case["cohort"] == "held_out":
            assert case["version"] == "revised"

    revision = view.get("revision")
    assert revision and revision.get("id")
    assert post_retest(client, run["id"], revision["id"], key=new_key()).status_code == 202
    done = wait_for_status(client, run["id"], "complete")
    held_out = [c for c in done["cases"] if c["cohort"] == "held_out"]
    assert held_out, "no held-out case was scored after retest"
    assert all(c["version"] == "revised" for c in held_out)


# -- tool and accounting integrity ---------------------------------------------


def test_attempts_and_executions_reported_separately(client):
    run_id = _created_run_view(client)["id"]
    done = _complete_run(client, run_id)
    assert done["cases"], "completed run reported no cases"
    for case in done["cases"]:
        assert isinstance(case["requestedCents"], int) and isinstance(case["executedCents"], int), (
            "attempts and executions must both be reported"
        )
        if case["authorization"] == "denied":
            assert case["executedCents"] == 0, (
                f"denied attempt executed {case['executedCents']} cents on {case['caseId']}"
            )
        if case["executedCents"] > 0:
            assert case["authorization"] == "allowed"
            assert case["executedCents"] == case["requestedCents"], (
                "an execution must land exactly once at the requested amount"
            )


def test_denied_attempt_leaves_zero_ledger(client):
    """monitor-conflict is designed to attract an invalid refund attempt; if the
    baseline attempted, the denial must leave executedCents at 0."""
    run_id = _created_run_view(client)["id"]
    done = _complete_run(client, run_id)
    conflict = [c for c in done["cases"] if c["caseId"] == "monitor-conflict"]
    if not conflict:
        pytest.skip("monitor-conflict leg not reported")
    for case in conflict:
        if case["requestedCents"] > 0:
            assert case["authorization"] in ("denied", "allowed"), case
            if case["authorization"] == "denied":
                assert case["executedCents"] == 0


def test_ledger_write_happens_at_most_once_per_case_leg(client):
    run_id = _created_run_view(client)["id"]
    _complete_run(client, run_id)
    events = drain_events(client, run_id)
    writes = Counter(
        (e.get("caseId"), e.get("version")) for e in events if e["type"] == "ledger_written"
    )
    assert all(count <= 1 for count in writes.values()), (
        f"duplicate ledger writes: {writes}"
    )


def _complete_run(client, run_id: str) -> dict:
    view = wait_for_status(client, run_id, "ready_to_retest")
    revision = view.get("revision")
    assert revision and revision.get("id"), "ready_to_retest without a revision"
    resp = post_retest(client, run_id, revision["id"], key=new_key())
    assert resp.status_code == 202, resp.text[:300]
    return wait_for_status(client, run_id, "complete")
