"""Exercise the installed Messages SDK against a local HTTP transport."""

import asyncio
import json
import runpy
from pathlib import Path
from unittest.mock import AsyncMock

import httpx2 as httpx
import pytest
from anthropic import AsyncAnthropic
from fastapi.testclient import TestClient

from thirteen_labs.app import create_app
from thirteen_labs.config import Settings
from thirteen_labs.engine.fixture import FixtureDriver
from thirteen_labs.engine.tools import TOOL_SCHEMAS
from thirteen_labs.integrations import anthropic
from thirteen_labs.ports import DriverSpec


def mock_messages(monkeypatch, handler):
    requests = []

    async def handle(request):
        assert request.url == httpx.URL("https://api.anthropic.com/v1/messages")
        payload = json.loads(request.content)
        requests.append(payload)
        response = await handler(payload, len(requests))
        return httpx.Response(200, json=response)

    def make_client(**kwargs):
        return AsyncAnthropic(
            **kwargs, base_url="https://api.anthropic.com",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        )

    monkeypatch.setattr(anthropic, "AsyncAnthropic", make_client)
    return requests


def message(index, blocks, reason):
    return {
        "id": f"msg_{index}", "type": "message", "role": "assistant", "model": "lab-model",
        "content": blocks, "stop_reason": reason, "usage": {"input_tokens": 50, "output_tokens": 10},
    }


def tool(index, name, args):
    return {"type": "tool_use", "id": f"toolu_{index}", "name": name, "input": args}


def spec(**overrides):
    return DriverSpec("private-run", "private-case", "baseline", "locked workflow", "hash", "my order", **overrides)


def adapter():
    return anthropic.AnthropicDefenderDriver("mock-key", "lab-model")


async def test_provider_tool_ids_results_and_transcript(monkeypatch):
    order = {"orderId": "ORD-1"}
    blocks = [
        tool(1, "lookup_order", order), tool(2, "verify_carrier_report", order),
        tool(3, "request_refund", order), tool(4, "record_resolution", {"outcome": "refund_proposed"}),
    ]

    async def handler(payload, index):
        if index == 1:
            return message(index, [{"type": "text", "text": "Order number?"}], "end_turn")
        if index == 2:
            return message(index, blocks, "tool_use")
        return message(index, [{"type": "text", "text": "The lab recorded your request."}], "end_turn")

    requests = mock_messages(monkeypatch, handler)
    followup = AsyncMock(return_value="ORD-1")
    outputs = [{"amountCents": 90000}, {"status": "matched"}, {"authorization": "allowed"}, {"outcome": "refund_proposed"}]
    dispatch = AsyncMock(side_effect=outputs)
    emit = AsyncMock()
    result = await adapter().run_case(spec(), followup, dispatch, emit)
    assert result.completed and result.source == "model" and result.conversation_id == "msg_1"
    assert len(requests) == 3
    for payload in requests:
        assert payload["model"] == "lab-model" and payload["system"] == "locked workflow"
        assert {row["name"] for row in payload["tools"]} == set(TOOL_SCHEMAS)
        assert "private-case" not in json.dumps(payload) and "private-run" not in json.dumps(payload)
    assert [call.args for call in dispatch.await_args_list] == [
        (block["id"], block["name"], block["input"]) for block in blocks
    ]
    assert requests[-1]["messages"][-1] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": block["id"], "content": json.dumps(output), "is_error": False}
        for block, output in zip(blocks, outputs)
    ]}
    followup.assert_awaited_once_with("Order number?")
    assert emit.await_args_list[0].args == ("provider_message", {"id": "msg_1"})
    assert emit.await_count == 3


async def test_over_budget_parallel_batch_returns_individual_errors(monkeypatch):
    async def handler(payload, index):
        if index == 1:
            return message(index, [tool(i, "lookup_order", {"orderId": "O"}) for i in range(9)], "tool_use")
        assert payload["tool_choice"] == {"type": "none"}
        return message(index, [{"type": "text", "text": "Done"}], "end_turn")

    requests = mock_messages(monkeypatch, handler)
    dispatch = AsyncMock(return_value={"error": "order_not_found"})
    result = await adapter().run_case(spec(), AsyncMock(), dispatch, AsyncMock())
    assert not result.completed and dispatch.await_count == 8
    answers = requests[1]["messages"][-1]["content"]
    assert len(answers) == 9 and all(reply["is_error"] for reply in answers)
    assert json.loads(answers[-1]["content"]) == {"error": "tool_limit_reached"}


async def test_customer_turns_include_opening_message(monkeypatch):
    async def handler(payload, index):
        return message(index, [{"type": "text", "text": "More information?"}], "end_turn")

    requests = mock_messages(monkeypatch, handler)
    followup = AsyncMock(return_value="An authored follow-up.")
    result = await adapter().run_case(spec(), followup, AsyncMock(), AsyncMock())
    assert not result.completed and len(requests) == 4 and followup.await_count == 3


async def test_deadline_cancels_outstanding_request(monkeypatch):
    async def handler(payload, index):
        await asyncio.sleep(10)
        raise AssertionError("request survived its deadline")

    mock_messages(monkeypatch, handler)
    result = await asyncio.wait_for(
        adapter().run_case(spec(deadline_s=.05), AsyncMock(), AsyncMock(), AsyncMock()), timeout=1,
    )
    assert not result.completed and result.conversation_id is None


@pytest.mark.parametrize(("mode", "key", "kind"), [
    ("live", "configured", anthropic.AnthropicDefenderDriver),
    ("fixture", "configured", FixtureDriver),
    ("live", "", type(None)),
])
def test_app_selects_driver(tmp_path, mode, key, kind):
    settings = Settings(state_db=str(tmp_path / "lab.db"), demo_mode=mode, anthropic_api_key=key)
    app = create_app(settings=settings)
    with TestClient(app) as client:
        assert isinstance(app.state.shift_engine.driver, kind)
        assert client.get("/api/health").json()["capabilities"]["batch"] is (kind is not type(None))
        if mode == "live" and key:
            assert app.state.shift_engine.driver.model == settings.lab_model
    injected = FixtureDriver()
    with TestClient(create_app(settings=Settings(state_db=str(tmp_path / "injected.db"),
                                                demo_mode="live", anthropic_api_key="key"),
                               defender_driver=injected)) as client:
        assert client.app.state.shift_engine.driver is injected


def test_http_shift_preserves_provider_ids_and_revised_workflow(tmp_path, monkeypatch, capsys):
    from thirteen_labs.engine.evaluation import BASELINE_WORKFLOW, REVISED_WORKFLOW

    async def handler(payload, index):
        if len(payload["messages"]) == 1:
            return message(index, [tool(index, "record_resolution", {"outcome": "needs_info"})], "tool_use")
        return message(index, [{"type": "text", "text": "Could you send more information?"}], "end_turn")

    requests = mock_messages(monkeypatch, handler)
    settings = Settings(state_db=str(tmp_path / "mock-provider.db"), demo_mode="live",
                        anthropic_api_key="mock-key", lab_model="lab-model")
    with TestClient(create_app(settings=settings)) as client:
        script = Path(__file__).resolve().parents[2] / 'scripts' / 'measure_live_shift.py'
        runpy.run_path(str(script))['measure'](client)
    _, output = capsys.readouterr().out.split("\n", 1)
    transcript = json.loads(output)
    assert len(transcript["cases"]) == 7
    assert transcript["baselineRefundEventIds"] == []
    assert transcript["baselineEvidence"]
    assert len({case["conversationId"] for case in transcript["cases"]}) == 7
    assert all(case["source"] == "model" and case["outcome"] == "needs_info" for case in transcript["cases"])
    assert all(case["score"]["passed"] is False for case in transcript["cases"])
    assert [request["system"] for request in requests] == [BASELINE_WORKFLOW] * 6 + [REVISED_WORKFLOW] * 8
    assert {request["model"] for request in requests} == {"lab-model"}


async def test_engine_timeout_retains_observed_provider_receipt(tmp_path, monkeypatch):
    from thirteen_labs.engine import service
    from thirteen_labs.engine.evaluation import BASELINE_WORKFLOW
    from thirteen_labs.scenarios import SCENARIOS
    from thirteen_labs.store import RunStore
    from dataclasses import asdict

    async def handler(payload, index):
        if index == 1:
            return message(index, [tool(1, "lookup_order", {"orderId": SCENARIOS[0].order_id})], "tool_use")
        await asyncio.sleep(10)
        raise AssertionError("request survived its deadline")

    mock_messages(monkeypatch, handler)
    engine = service.ShiftEngine(RunStore(tmp_path / "interrupted.db"), driver=adapter())
    try:
        run_id, _ = engine.store.start(
            "owner", "start", {"candidateId": "monitor-support", "suiteId": "probation-v1"}, "live",
            [asdict(case) for case in SCENARIOS], "hash", engine.configuration, available=True, capacity=True,
        )
        monkeypatch.setattr(service, "CASE_SECONDS", .3)
        await engine._case(run_id, SCENARIOS[0], "baseline", BASELINE_WORKFLOW)
        record = engine.store.cases(run_id)[0]
        assert record["outcome"] == "incomplete" and record["conversationId"] == "msg_1"
        assert "score" not in record and record["evidenceIds"]
    finally:
        await engine.close()
