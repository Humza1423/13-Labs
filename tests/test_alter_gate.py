import base64
import json
from uuid import UUID

import pytest
from alter_sdk.exceptions import ApprovalDeniedError, ApprovalTimeoutError
from alter_sdk.models import ApprovalResult, PendingApproval

from thirteen_labs.integrations import alter
from thirteen_labs.ports import AuthorizationResult


@pytest.mark.asyncio
async def test_alter_keeps_the_approval_receipt_on_each_decision(monkeypatch):
    action_id = "a" * 32
    approval_id = UUID("8802622f-1b10-41d9-a7b4-cf760668c665")
    outcome = "allowed"

    class Proxy:
        def __init__(self, *, api_key, timeout):
            assert api_key == "test-key" and timeout == 2

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def proxy_request(self, method, url, **kwargs):
            assert (method, url) == ("POST", "https://lab.example/internal/execute-refund")
            assert kwargs == {
                "grant_id": "test-grant", "json": {"actionId": action_id},
                "context": {"action_id": action_id},
            }
            return PendingApproval.model_construct(approval_id=approval_id)

        async def await_approval(self, receipt, **_kwargs):
            assert receipt == str(approval_id)
            if outcome == "denied":
                raise ApprovalDeniedError(message="Denied by an approver")
            if outcome == "pending":
                raise ApprovalTimeoutError(message="The approval is still pending")
            body = {"actionId": action_id, "status": "executed"}
            return ApprovalResult(
                approval_id=approval_id, status_code=200,
                body_b64=base64.b64encode(json.dumps(body).encode()).decode(),
            )

    monkeypatch.setattr(alter, "App", Proxy)
    gate = alter.AlterGate("test-key", "test-grant", "https://lab.example/")
    for outcome in ("allowed", "denied", "pending"):
        assert await gate.authorize(action_id) == AuthorizationResult(outcome, "alter", str(approval_id))
