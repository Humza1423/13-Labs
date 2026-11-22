"""Authorize synthetic ledger actions through the Alter proxy."""

from asyncio import timeout

from alter_sdk import Agent, App
from alter_sdk.exceptions import (
    AlterSDKError,
    ApprovalDeniedError,
    ApprovalExecutionFailedError,
    ApprovalExpiredError,
    PolicyViolationError,
)
from alter_sdk.models import PendingApproval

from thirteen_labs.ports import AuthorizationResult


class AlterGate:
    def __init__(self, api_key: str, grant_id: str, public_origin: str):
        self._api_key = api_key
        self._grant_id = grant_id
        self._endpoint = public_origin.rstrip("/") + "/internal/execute-refund"

    async def authorize(self, action_id: str) -> AuthorizationResult:
        client_type = Agent if self._api_key.startswith(("alter_ak_", "alter_key_")) else App
        receipt = None
        try:
            async with timeout(4), client_type(api_key=self._api_key, timeout=2) as client:
                result = await client.proxy_request(
                    "POST", self._endpoint, grant_id=self._grant_id,
                    json={"actionId": action_id}, context={"action_id": action_id},
                )
                if isinstance(result, PendingApproval):
                    receipt = str(result.approval_id)
                    result = await client.await_approval(receipt, timeout=1, poll_interval=0.25)
        except ApprovalDeniedError:
            return AuthorizationResult("denied", "alter", receipt)
        except PolicyViolationError as error:
            receipt = receipt or (error.details or {}).get("request_id")
            return AuthorizationResult("denied", "alter", receipt if isinstance(receipt, str) else None)
        except (ApprovalExecutionFailedError, ApprovalExpiredError):
            return AuthorizationResult("unavailable", "alter", receipt)
        except (AlterSDKError, TimeoutError):
            return AuthorizationResult("pending" if receipt else "unavailable", "alter", receipt)

        if result.approval_id is not None:
            receipt = str(result.approval_id)
        if result.status_code == 200:
            try:
                body = result.body_json()
            except ValueError:
                body = None
            if body == {"actionId": action_id, "status": "executed"}:
                return AuthorizationResult("allowed", "alter", receipt)
        return AuthorizationResult("unavailable", "alter", receipt)
