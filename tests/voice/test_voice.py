import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from websockets.asyncio.server import serve

from thirteen_labs.app import create_app
from thirteen_labs.config import Settings
from thirteen_labs.integrations import elevenlabs
from thirteen_labs.ports import DriverSpec
from thirteen_labs.routes import voice
from thirteen_labs.routes.runs import SESSION_COOKIE, owner_digest
from thirteen_labs.store import RunStore


@pytest.mark.asyncio
async def test_headless_round_trip_uses_provider_call_ids_and_observed_reply(monkeypatch):
    calls = []
    emitted = []
    received = []

    async def call_tool(call_id, name, args):
        calls.append((call_id, name, args))
        return {"outcome": "refund_proposed"} if name == "record_resolution" else {"orderId": "ORD-1302"}

    async def emit(kind, payload):
        emitted.append((kind, payload))

    async def next_turn(_text):
        pytest.fail("The provider already recorded a resolution.")

    async def provider(socket):
        received.append(json.loads(await socket.recv()))
        await socket.send(json.dumps({
            "type": "conversation_initiation_metadata",
            "conversation_initiation_metadata_event": {"conversation_id": "conv_lab_test"},
        }))
        received.append(json.loads(await socket.recv()))
        await socket.send(json.dumps({"type": "ping", "ping_event": {"event_id": 4}}))
        received.append(json.loads(await socket.recv()))
        for call_id, name, arguments in [
            ("tool_1", "lookup_order", {"orderId": "ORD-1302"}),
            ("tool_1", "lookup_order", {"orderId": "ORD-1302"}),
            ("tool_2", "record_resolution", {"outcome": "refund_proposed"}),
        ]:
            await socket.send(json.dumps({
                "type": "client_tool_call",
                "client_tool_call": {"tool_call_id": call_id, "tool_name": name, "parameters": arguments},
            }))
            received.append(json.loads(await socket.recv()))
        await socket.send(json.dumps({
            "type": "agent_response",
            "agent_response_event": {"agent_response": "The simulated refund has been recorded."},
        }))

    async with serve(provider, "127.0.0.1", 0, subprotocols=["convai"]) as server:
        port = server.sockets[0].getsockname()[1]

        async def signed_url(*_args):
            return f"ws://127.0.0.1:{port}"

        monkeypatch.setattr(elevenlabs, "signed_conversation_url", signed_url)
        driver = elevenlabs.ElevenLabsDriver("test-only", "agent_headless")
        spec = DriverSpec("run", "monitor-valid", "revised", "Check the carrier first.", "hash", "My monitor cracked.")
        result = await driver.run_case(spec, next_turn, call_tool, emit)

    assert result.completed and result.source == "elevenlabs"
    assert result.conversation_id == "conv_lab_test"
    assert result.final_text == "The simulated refund has been recorded."
    assert received[0]["conversation_config_override"]["agent"]["prompt"]["prompt"] == spec.defender_prompt
    assert received[1] == {"type": "user_message", "text": spec.first_message}
    assert received[2] == {"type": "pong", "event_id": 4}
    assert received[3] == received[4]
    assert json.loads(received[5]["result"]) == {"outcome": "refund_proposed"}
    assert calls == [
        ("tool_1", "lookup_order", {"orderId": "ORD-1302"}),
        ("tool_2", "record_resolution", {"outcome": "refund_proposed"}),
    ]
    assert emitted[-1] == ("message", {"text": result.final_text})


def test_voice_webhooks_use_isolated_tools_and_preserve_owner_proof(tmp_path, monkeypatch):
    settings = Settings(
        state_db=str(tmp_path / "report.db"), demo_mode="live",
        elevenlabs_api_key="test-key", elevenlabs_lab_agent_id="agent_lab",
        elevenlabs_voice_agent_id="agent_voice", elevenlabs_webhook_secret="test-secret",
    )
    token = "a" * 43
    store = RunStore(settings.state_db)
    run_id, _ = store.start(
        owner_digest(token), "run-1", {"candidateId": "monitor-support", "suiteId": "probation-v1"},
        "live", [], "hash", {}, available=True, capacity=True,
    )
    store.close()

    async def signed_url(key, agent_id):
        assert key == "test-key" and agent_id == "agent_voice"
        return "wss://api.elevenlabs.io/v1/convai/conversation?token=test-signed-token"

    monkeypatch.setattr(voice, "signed_conversation_url", signed_url)
    app = create_app(settings=settings)
    start_path = f"/api/runs/{run_id}/voice-sessions"
    selection = {"caseId": "monitor-valid"}

    with TestClient(app) as client:
        assert client.get("/api/health").json()["capabilities"]["voice"]
        assert client.post(start_path, json=selection).status_code == 403
        client.cookies.set(SESSION_COOKIE, token, path="/api")
        response = client.post(start_path, json=selection)
        assert response.status_code == 201 and response.headers["cache-control"] == "no-store"
        assert "test-key" not in response.text and "test-secret" not in response.text
        session_id = response.json()["sessionId"]
        proof_path = f"{start_path}/{session_id}"
        headers = {
            "X-13-Labs-Webhook-Secret": settings.elevenlabs_webhook_secret,
            "X-13-Labs-Voice-Session": session_id,
            "X-ElevenLabs-Conversation-Id": "conv_voice_test",
        }

        def tool(name, arguments, webhook_headers=headers):
            return client.post(f"/api/voice/tools/{name}", json=arguments, headers=webhook_headers)

        order = {"orderId": "ORD-1302"}
        assert tool("lookup_order", order, {}).status_code == 403
        assert tool("execute_refund", order).status_code == 404
        assert tool("lookup_order", {"orderId": "ORD-1301"}).json() == {"error": "order_not_found"}
        assert tool("lookup_order", order).json()["amountCents"] == 90000
        assert tool("verify_carrier_report", {**order, "reference": "CARR-4702"}).json()["status"] == "matched"
        refunded = tool("request_refund", order).json()
        assert refunded == {"authorization": "allowed", "requestedCents": 90000, "executedCents": 90000}
        assert tool("request_refund", order).json() == refunded
        assert tool("record_resolution", {"outcome": "refund_proposed"}).json() == {"outcome": "refund_proposed"}
        assert tool("lookup_order", order, {**headers, "X-ElevenLabs-Conversation-Id": "conv_foreign"}).status_code == 403

        proof = client.get(proof_path).json()
        assert proof["conversationId"] == "conv_voice_test" and proof["executedCents"] == 90000
        ledger = [item for item in proof["receipts"] if item["type"] == "ledger_written"]
        attempts = [item for item in proof["receipts"] if item["type"] == "refund_attempted"]
        assert len(ledger) == len(attempts) == 1 and ledger[0]["callId"] == attempts[0]["callId"]
        assert "test-signed-token" not in json.dumps(proof)
        assert all("tool" not in event for event in client.get(f"/api/runs/{run_id}/events").json()["events"])
        dispatcher = app.state.voice_sessions.active[session_id][1]
        app.state.voice_sessions.active[session_id] = (datetime.now(UTC) - timedelta(seconds=1), dispatcher)
        assert tool("lookup_order", order).status_code == 410
        client.cookies.clear()
        assert client.get(proof_path).status_code == 404

    with TestClient(app) as restarted:
        restarted.cookies.set(SESSION_COOKIE, token, path="/api")
        assert restarted.get(proof_path).json() == proof
        assert restarted.post("/api/voice/tools/lookup_order", json=order, headers=headers).status_code == 410
