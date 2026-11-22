"""Owner-scoped microphone sessions and authenticated ElevenLabs callbacks."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Request

from thirteen_labs.contracts import VoiceSessionRequest, VoiceSessionResponse
from thirteen_labs.engine.evaluation import REVISED_WORKFLOW, workflow_hash
from thirteen_labs.engine.tools import TOOL_SCHEMAS, CaseDispatcher, public_id
from thirteen_labs.integrations.elevenlabs import ElevenLabsUnavailable, signed_conversation_url
from thirteen_labs.routes.runs import error_reply, input_body, owner, reply
from thirteen_labs.scenarios import SCENARIOS
from thirteen_labs.store import RunError, RunStore, encode

router = APIRouter(prefix="/api")
VOICE_CASE = next(case for case in SCENARIOS if case.id == "monitor-valid")
SESSION_DURATION = timedelta(minutes=3)
SESSION_LIMIT = 4


class VoiceSessions:
    """Use the same typed tools and accounting rules in a private SQLite store."""

    def __init__(self, state_db: str):
        path = ":memory:" if state_db == ":memory:" else Path(f"{state_db}.voice")
        self.store = RunStore(path)
        self.store.db.executescript("""
            CREATE TABLE IF NOT EXISTS voice_sessions (
                id TEXT PRIMARY KEY,
                parent_run TEXT NOT NULL,
                owner TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(id),
                case_run_id TEXT NOT NULL REFERENCES case_runs(id),
                started_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                conversation_id TEXT UNIQUE
            );
            CREATE INDEX IF NOT EXISTS voice_sessions_started ON voice_sessions(started_at);
        """)
        self.active: dict[str, tuple[datetime, CaseDispatcher]] = {}

    def start(self, run_id: str, request_owner: str, engine) -> tuple[str, datetime]:
        now = datetime.now(UTC)
        self.active = {key: item for key, item in self.active.items() if item[0] > now}
        since = (now - timedelta(hours=1)).isoformat()
        counts = self.store.db.execute(
            "SELECT COUNT(*) AS total, COUNT(CASE WHEN owner=? THEN 1 END) AS owned "
            "FROM voice_sessions WHERE started_at>=?", (request_owner, since),
        ).fetchone()
        if counts["total"] >= engine.live_run_limit or counts["owned"] >= SESSION_LIMIT:
            raise RunError(429, "voice_start_limit", "The lab reached its hourly limit for voice calls.", True)

        session_id = secrets.token_urlsafe(24)
        expires_at = now + SESSION_DURATION
        configuration = {"provider": "elevenlabs", "versionHash": workflow_hash(REVISED_WORKFLOW)}
        private_run, _ = self.store.start(
            request_owner, session_id, {"candidateId": "monitor-support", "suiteId": "probation-v1"},
            "voice", [asdict(VOICE_CASE)], configuration["versionHash"], configuration,
            available=True, capacity=True,
        )
        case_run = self.store.begin_case(private_run, VOICE_CASE.id, "revised", configuration)
        self.store.db.execute(
            "INSERT INTO voice_sessions (id, parent_run, owner, run_id, case_run_id, started_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, run_id, request_owner, private_run, case_run, now.isoformat(), expires_at.isoformat()),
        )
        self.active[session_id] = (expires_at, CaseDispatcher(
            self.store, run_id=private_run, case_run_id=case_run, scenario=VOICE_CASE, version="revised", stage="retest",
            authorization=engine.authorization, authorization_source=engine.authorization_source,
        ))
        return session_id, expires_at

    def authorize_callback(self, session_id: str, conversation_id: str) -> CaseDispatcher:
        active = self.active.get(session_id)
        if not active or active[0] <= datetime.now(UTC):
            self.active.pop(session_id, None)
            raise RunError(410, "voice_session_expired", "The voice session has ended.")
        if not public_id(conversation_id) or not conversation_id.startswith("conv_"):
            raise RunError(403, "voice_binding_required", "A provider conversation binding is required.")
        row = self.store.db.execute(
            "SELECT conversation_id FROM voice_sessions WHERE id=?", (session_id,),
        ).fetchone()
        if row["conversation_id"] is None:
            try:
                self.store.db.execute(
                    "UPDATE voice_sessions SET conversation_id=? WHERE id=?", (conversation_id, session_id),
                )
            except sqlite3.IntegrityError:
                raise RunError(403, "voice_binding_mismatch", "The conversation is bound to another session.") from None
        elif row["conversation_id"] != conversation_id:
            raise RunError(403, "voice_binding_mismatch", "The conversation does not match this session.")
        return active[1]

    def proof(self, run_id: str, request_owner: str, session_id: str) -> dict:
        session = self.store.db.execute(
            "SELECT * FROM voice_sessions WHERE id=? AND parent_run=? AND owner=?",
            (session_id, run_id, request_owner),
        ).fetchone()
        if session is None:
            raise RunError(404, "voice_session_not_found", "This voice session could not be found.")
        events = self.store.events(session["run_id"], 0)
        return {
            "sessionId": session_id, "conversationId": session["conversation_id"], "caseId": VOICE_CASE.id,
            "expiresAt": session["expires_at"], "executedCents": self.store.ledger_total(session["case_run_id"]),
            "receipts": [
                {
                    "eventId": event["id"], "at": event["at"], "tool": event["tool"],
                    "callId": event["correlationId"], "type": event["type"], "evidence": event.get("evidence", []),
                }
                for event in events
                if event.get("tool") and event["type"] in {
                    "tool_returned", "refund_attempted", "authorization_decided", "ledger_written",
                }
            ],
        }


def get_sessions(request: Request) -> VoiceSessions:
    sessions = request.app.state.voice_sessions
    if sessions is None:
        raise RunError(503, "voice_unavailable", "The voice integration is unavailable.", True)
    return sessions


@router.post("/runs/{run_id}/voice-sessions", status_code=201, response_model=VoiceSessionResponse)
async def start_voice_session(run_id: str, request: Request):
    try:
        engine = request.app.state.shift_engine
        run = engine.store.get(run_id)
        request_owner = owner(request)
        if not request_owner or run["owner"] != request_owner:
            raise RunError(403, "owner_required", "Only the shift owner can start a call.")
        if run["mode"] != "live":
            raise RunError(409, "voice_unavailable_for_run", "Start a live shift to make a call.")
        await input_body(request, VoiceSessionRequest)
        sessions = get_sessions(request)
        session_id, expires_at = sessions.start(run_id, request_owner, engine)
        settings = request.app.state.settings
        try:
            signed_url = await signed_conversation_url(settings.elevenlabs_api_key, settings.elevenlabs_voice_agent_id)
        except ElevenLabsUnavailable:
            sessions.active.pop(session_id, None)
            raise RunError(503, "voice_provider_unavailable", "ElevenLabs could not start the call. Try again.", True) from None
        return reply(VoiceSessionResponse(session_id=session_id, signed_url=signed_url, expires_at=expires_at.isoformat()).dump(), 201)
    except RunError as error:
        return error_reply(error)


@router.get("/runs/{run_id}/voice-sessions/{session_id}")
async def get_voice_proof(run_id: str, session_id: str, request: Request):
    try:
        return reply(get_sessions(request).proof(run_id, owner(request), session_id))
    except RunError as error:
        return error_reply(error)


@router.post("/voice/tools/{tool}")
async def voice_tool(tool: str, request: Request):
    try:
        settings = request.app.state.settings
        credential = request.headers.get("X-13-Labs-Webhook-Secret", "")
        if not settings.elevenlabs_webhook_secret or not secrets.compare_digest(
            credential.encode(), settings.elevenlabs_webhook_secret.encode(),
        ):
            raise RunError(403, "webhook_authentication_required", "A provider webhook credential is required.")
        if tool not in TOOL_SCHEMAS:
            raise RunError(404, "unknown_tool", "This voice tool is unavailable.")
        session_id = request.headers.get("X-13-Labs-Voice-Session", "")
        conversation_id = request.headers.get("X-ElevenLabs-Conversation-Id", "")
        dispatcher = get_sessions(request).authorize_callback(session_id, conversation_id)
        try:
            arguments = await request.json()
        except ValueError:
            raise RunError(422, "invalid_request", "The tool expects a JSON object.") from None
        if not isinstance(arguments, dict):
            raise RunError(422, "invalid_request", "The tool expects a JSON object.")
        # HTTP tool callbacks do not carry the model's tool call ID. An exact
        # retry of a tool for this case returns the original dispatcher result.
        call_id = "voice-" + hashlib.sha256(encode([conversation_id, tool, arguments]).encode()).hexdigest()
        return reply(await dispatcher(call_id, tool, arguments))
    except RunError as error:
        return error_reply(error)
