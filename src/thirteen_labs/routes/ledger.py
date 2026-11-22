"""Execution endpoint for the scoped Alter grant's injected credential."""

from datetime import UTC, datetime
from secrets import compare_digest

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from thirteen_labs.routes.runs import error_reply, input_body, reply
from thirteen_labs.store import RunError

router = APIRouter()


class ExecuteRefundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actionId: str = Field(pattern=r"^[a-f0-9]{32}$")


@router.post("/internal/execute-refund", include_in_schema=False)
async def execute_refund(request: Request):
    try:
        engine = request.app.state.shift_engine
        secret = request.app.state.settings.ledger_upstream_secret
        if engine.authorization_source != "alter" or not secret:
            raise RunError(404, "ledger_unavailable", "The protected ledger is unavailable.")
        credential = request.headers.get("X-13-Labs-Ledger-Secret", "")
        if not compare_digest(credential.encode(), secret.encode()):
            raise RunError(403, "ledger_authentication_required", "The ledger credential is required.")

        payload = await input_body(request, ExecuteRefundRequest)
        action_id = payload.actionId
        store = engine.store
        sessions = request.app.state.voice_sessions
        if sessions is not None and sessions.store.db.execute(
            "SELECT 1 FROM refund_actions a JOIN voice_sessions s ON s.case_run_id = a.case_run_id "
            "WHERE a.id = ? AND s.expires_at > ?",
            (action_id, datetime.now(UTC).isoformat()),
        ).fetchone():
            store = sessions.store
        store.execute_refund(action_id, authorization_source="alter")
        return reply({"actionId": action_id, "status": "executed"})
    except RunError as error:
        return error_reply(error)
