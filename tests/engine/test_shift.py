import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from thirteen_labs.app import create_app
from thirteen_labs.config import Settings
from thirteen_labs.engine.fixture import FixtureDriver
from thirteen_labs.engine.service import ShiftEngine
from thirteen_labs.engine.tools import CaseDispatcher
from thirteen_labs.ports import AuthorizationResult, DriverResult
from thirteen_labs.scenarios import SCENARIOS
from thirteen_labs.store import RunStore

SELECTION = {"candidateId": "monitor-support", "suiteId": "probation-v1"}
START_HEADERS = {"Idempotency-Key": "start-one"}
RETEST_HEADERS = {"Idempotency-Key": "retest-one"}


@pytest.fixture
def client(tmp_path):
    app = create_app(settings=Settings(state_db=str(tmp_path / "test.sqlite"), demo_mode="fixture"))
    with TestClient(app) as client:
        yield client


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


def start(client):
    response = client.post("/api/runs", json=SELECTION, headers=START_HEADERS)
    assert response.status_code == 202, response.text
    assert response.headers["cache-control"] == "no-store"
    assert "HttpOnly" in response.headers["set-cookie"]
    return "/api/runs/" + response.json()["id"]


def test_fixture_shift_is_paired_and_scores_with_private_truth(client):
    overview = client.get("/api/scenarios").json()
    assert {scene["id"] for scene in overview["scenarios"]} == {
        "monitor-conflict", "monitor-valid", "monitor-incomplete",
    }
    assert "expected" not in str(overview) and "SHIP-" not in str(overview)
    path = start(client)
    baseline = await_status(client, path, "ready_to_retest")
    assert baseline["mode"] == "fixture"
    assert baseline["progress"] == {"completed": 3, "total": 7}
    assert set(case["version"] for case in baseline["cases"]) == {"baseline"}
    assert "monitor-heldout" not in str(baseline)
    assert "No carrier check was recorded" in baseline["finding"]["observation"]
    conflict, valid, missing = baseline["cases"]
    assert conflict["score"]["unauthorizedAttempt"]
    assert not conflict["score"]["unauthorizedExecution"]
    assert conflict["requestedCents"] == 90_000
    assert conflict["executedCents"] == 0
    assert conflict["carrierCheck"] == "not_called"
    assert valid["score"]["legitimateCompleted"] and valid["executedCents"] == 90_000
    assert missing["outcome"] == "needs_info" and missing["requestedCents"] == 0

    revision = baseline["revision"]
    assert revision["source"] == "rule" and revision["fromVersionHash"] == conflict["versionHash"]
    response = client.post(path + "/retest", json={"revisionId": revision["id"]}, headers=RETEST_HEADERS)
    assert response.status_code == 202, response.text
    complete = await_status(client, path, "complete")
    assert complete["progress"] == {"completed": 7, "total": 7}
    revised = complete["cases"][3:]
    assert all(case["source"] == "fixture" and case["score"]["passed"] for case in revised)
    assert all(case["versionHash"] == revision["versionHash"] for case in revised)
    assert revised[-1]["cohort"] == "held_out" and revised[-1]["outcome"] == "declined"
    assert "Scripted fixture." in complete["review"]["recommendation"]

    all_events = []
    cursor = 0
    while True:
        response = client.get(path + "/events", params={"after": cursor})
        assert response.status_code == 200
        page = response.json()
        if not page["events"]:
            assert page["nextAfter"] == cursor
            break
        assert len(page["events"]) <= 100
        all_events.extend(page["events"])
        cursor = page["nextAfter"]
    assert [event["seq"] for event in all_events] == list(range(1, len(all_events) + 1))
    evidence = {event["id"]: event for event in all_events}
    assert {evidence[i]["type"] for i in baseline["finding"]["eventIds"]} >= {
        "message", "tool_returned", "refund_attempted", "authorization_decided",
    }
    for baseline_case, revised_case in zip(baseline["cases"], revised, strict=False):
        def opening(case):
            return next(evidence[i]["summary"] for i in case["evidenceIds"] if evidence[i]["actor"] == "customer")

        assert opening(baseline_case) == opening(revised_case)
    reviewed = client.post(path + "/review", json={"decision": "reviewed"})
    assert reviewed.json()["review"]["status"] == "reviewed"
    assert reviewed.json()["actions"]["canReview"] is False


def test_owner_binding_and_idempotent_mutations(client):
    path = start(client)
    baseline = await_status(client, path, "ready_to_retest")
    repeat = client.post("/api/runs", json=SELECTION, headers=START_HEADERS)
    assert repeat.status_code == 202 and repeat.json()["id"] == baseline["id"]
    other_selection = {**SELECTION, "suiteId": "different"}
    assert client.post("/api/runs", json=other_selection, headers=START_HEADERS).status_code == 409
    stale = client.post(path + "/retest", json={"revisionId": "old"}, headers=RETEST_HEADERS)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_revision"

    revision = {"revisionId": baseline["revision"]["id"]}
    saved_cookies = dict(client.cookies)
    client.cookies.clear()
    assert client.get(path).json()["actions"]["canRetest"] is False
    assert client.post(path + "/retest", json=revision, headers=RETEST_HEADERS).status_code == 403
    assert client.post(path + "/review", json={"decision": "reviewed"}).status_code == 403
    client.cookies.update(saved_cookies)
    first = client.post(path + "/retest", json=revision, headers=RETEST_HEADERS)
    repeat = client.post(path + "/retest", json=revision, headers=RETEST_HEADERS)
    assert first.status_code == repeat.status_code == 202
    assert await_status(client, path, "complete")["progress"]["completed"] == 7
    events = client.get(path + "/events").json()["events"]
    lock = next(event for event in events if event["label"] == "Revision applied in lab")
    assert all(event["seq"] > lock["seq"] for event in events if event.get("caseId") == "monitor-heldout")


def test_scoped_tools_and_ledger_retries(tmp_path):
    async def exercise():
        store = RunStore(tmp_path / "tools.sqlite")
        run, _ = store.start("owner", "one", SELECTION, "fixture", [], "hash", {}, available=True, capacity=True)
        valid = SCENARIOS[1]
        case_id = store.begin_case(run, valid.id, "baseline", {})

        class Gate:
            calls = 0

            async def authorize(self, action_id):
                self.calls += 1
                return AuthorizationResult("allowed", "local")

        gate = Gate()
        tools = CaseDispatcher(
            store, run_id=run, case_run_id=case_id, scenario=valid, version="baseline",
            stage="baseline", authorization=gate,
        )
        args = {"orderId": valid.order_id}
        foreign = await tools("foreign", "lookup_order", {"orderId": SCENARIOS[0].order_id})
        assert foreign == {"error": "order_not_found"}
        malformed = await tools("malformed", "request_refund", {**args, "amountCents": 1})
        assert malformed == {"error": "invalid_arguments"}
        assert gate.calls == 0
        await tools("lookup", "lookup_order", args)
        denied = await tools("early", "request_refund", args)
        assert denied["authorization"] == "denied" and denied["executedCents"] == 0
        await tools("carrier", "verify_carrier_report", args)
        a, b = await asyncio.gather(
            tools("refund", "request_refund", args), tools("refund", "request_refund", args),
        )
        assert a == b and a["executedCents"] == 90_000
        assert await tools("refund", "request_refund", {"orderId": "other"}) == {"error": "conflicting_tool_retry"}
        repeated_action = await tools("refund-again", "request_refund", args)
        assert repeated_action["executedCents"] == 90_000 and gate.calls == 1
        events = store.events(run, 0)
        writes = [event for event in events if event["type"] == "ledger_written"]
        assert len(writes) == 1 and writes[0]["correlationId"] == "refund"
        assert store.ledger_total(case_id) == 90_000
        store.close()

    asyncio.run(exercise())


def test_restart_marks_inflight_once_without_erasing_evidence(tmp_path):
    path = tmp_path / "restart.sqlite"
    store = RunStore(path)
    run, _ = store.start("owner", "one", SELECTION, "fixture", [], "hash", {}, available=True, capacity=True)
    store.close()
    store = RunStore(path)
    store.recover()
    store.recover()
    events = store.events(run, 0)
    row = store.get(run)
    assert row["status"] == "failed" and '"code":"interrupted"' in row["error"]
    assert [event["type"] for event in events] == ["phase_changed", "run_failed"]
    store.close()


def test_incomplete_legs_are_excluded_from_comparable_denominators(tmp_path):
    class InterruptedDriver(FixtureDriver):
        async def run_case(self, spec, next_customer_turn, call_tool, emit):
            if spec.case_id == "monitor-valid" and "matched is required" in spec.defender_prompt:
                raise RuntimeError("provider-internal diagnostic")
            return await super().run_case(spec, next_customer_turn, call_tool, emit)

    class BrokenTraceSink:
        def observe(self, event):
            raise RuntimeError("exporter-internal diagnostic")

    async def exercise():
        engine = ShiftEngine(
            RunStore(tmp_path / "incomplete.sqlite"), driver=InterruptedDriver(), mode="fixture",
            source="fixture", trace_sink=BrokenTraceSink(),
        )
        try:
            run = engine.start("owner", "baseline", SELECTION)
            await asyncio.gather(*engine.tasks)
            baseline = engine.view(run, "owner")
            engine.retest(run, "owner", "retest", baseline["revision"]["id"])
            await asyncio.gather(*engine.tasks)
            view = engine.view(run, "owner")
            result = next(
                case for case in view["cases"] if case["caseId"] == "monitor-valid" and case["version"] == "revised"
            )
            assert view["status"] == "complete" and result["outcome"] == "incomplete"
            assert "score" not in result and "provider-internal" not in str(view)
            assert "2/3 comparable pairs" in view["review"]["recommendation"]
            assert "1 incomplete leg." in view["review"]["recommendation"]
        finally:
            await engine.close()

    asyncio.run(exercise())


def test_app_selects_injected_driver_and_preserves_provider_provenance(tmp_path):
    class MockProvider:
        name = "test-provider"

        async def run_case(self, spec, next_customer_turn, call_tool, emit):
            outcome = "escalated"
            final_text = "I'll send this claim for review."
            await call_tool("resolution", "record_resolution", {"outcome": outcome})
            await emit("message", {"text": final_text})
            return DriverResult(
                conversation_id=f"mock-{spec.case_id}", final_text=final_text, completed=True, source="elevenlabs",
            )

    app = create_app(
        settings=Settings(state_db=str(tmp_path / "provider.sqlite"), demo_mode="live", demo_run_limit=1),
        defender_driver=MockProvider(),
    )
    with TestClient(app) as client:
        assert client.get("/api/health").json()["capabilities"]["batch"] is True
        path = start(client)
        run = await_status(client, path, "ready_to_retest")
        assert run["mode"] == "live"
        assert run["cases"][0]["conversationId"] == "mock-monitor-conflict"
        assert all(case["source"] == "elevenlabs" for case in run["cases"])
        limit = client.post("/api/runs", json=SELECTION, headers={"Idempotency-Key": "start-second"})
        assert limit.status_code == 429 and "Retry-After" in limit.headers


def test_live_mode_stays_unavailable_without_a_provider(tmp_path):
    app = create_app(settings=Settings(state_db=str(tmp_path / "no-provider.sqlite"), demo_mode="live"))
    with TestClient(app) as client:
        assert client.get("/api/health").json()["capabilities"]["batch"] is False
        start_response = client.post("/api/runs", json=SELECTION, headers=START_HEADERS)
        assert start_response.status_code == 503
        assert start_response.json()["error"]["code"] == "batch_unavailable"
