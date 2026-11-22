"""Shared JSON contract (C0). Mirror of web/src/contract.ts.

JSON is camelCase on the wire; Python attributes are snake_case. Owned by
Firstmate: propose changes through Firstmate so both mirrors move together.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

RunStatus = Literal[
    "queued",
    "baseline_running",
    "investigating",
    "ready_to_retest",
    "retest_running",
    "complete",
    "failed",
]
RunMode = Literal["live", "fixture", "replay"]
Version = Literal["baseline", "revised"]
Outcome = Literal["refund_proposed", "declined", "needs_info", "escalated", "incomplete"]
AuthDecision = Literal["not_requested", "allowed", "denied", "pending", "unavailable"]
Stage = Literal["baseline", "investigation", "revision", "retest", "review"]
EventType = Literal[
    "phase_changed",
    "message",
    "tool_called",
    "tool_returned",
    "refund_attempted",
    "authorization_decided",
    "ledger_written",
    "finding_published",
    "revision_proposed",
    "case_scored",
    "run_failed",
]
Actor = Literal["customer", "defender", "thirteen", "evaluator", "authorization"]
SourceTrust = Literal["customer_supplied", "authoritative", "system"]
ToolName = Literal["lookup_order", "verify_carrier_report", "request_refund", "record_resolution"]
CarrierCheck = Literal["not_called", "matched", "conflicting", "unavailable"]
CaseId = Literal["monitor-conflict", "monitor-valid", "monitor-incomplete", "monitor-heldout"]
PatchChange = Literal["require_carrier_match", "request_missing_evidence"]


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    def dump(self) -> dict:
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")


class EvidenceField(Model):
    label: str
    value: str


class RunEvent(Model):
    id: str
    seq: int
    at: str
    run_id: str
    case_id: str | None = None
    version: Version | None = None
    stage: Stage
    type: EventType
    actor: Actor
    label: str
    summary: str
    correlation_id: str | None = None
    source_trust: SourceTrust | None = None
    tool: ToolName | None = None
    evidence: list[EvidenceField] | None = None


class CaseScore(Model):
    expected: Literal["approve", "decline", "needs_info"]
    passed: bool
    unauthorized_attempt: bool
    unauthorized_execution: bool
    legitimate_completed: bool
    unnecessary_escalation_or_refusal: bool


class TraceRef(Model):
    provider: Literal["traceroot"]
    trace_id: str
    url: str | None = None


class CaseResult(Model):
    case_id: str
    label: str
    cohort: Literal["paired", "held_out"]
    version: Version
    outcome: Outcome
    authorization: AuthDecision
    requested_cents: int
    executed_cents: int
    carrier_check: CarrierCheck
    authorization_source: Literal["local", "alter"]
    authorization_receipt_id: str | None = None
    source: Literal["elevenlabs", "model", "fixture"]
    conversation_id: str | None = None
    version_hash: str
    score: CaseScore | None = None
    trace: TraceRef | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    incomplete_reason: str | None = None


class Finding(Model):
    summary: str
    observation: str
    event_ids: list[str]


class Revision(Model):
    id: str
    from_version_hash: str
    version_hash: str
    source: Literal["model", "rule"]
    before: str
    after: str
    changes: list[PatchChange]
    event_ids: list[str]


class Candidate(Model):
    id: str
    name: str


class Progress(Model):
    completed: int
    total: int


class RunActions(Model):
    can_retest: bool
    can_review: bool
    can_start_voice: bool


class Review(Model):
    status: Literal["awaiting_review", "reviewed"]
    recommendation: str


class ApiErrorBody(Model):
    code: str
    message: str
    retryable: bool


class RunView(Model):
    id: str
    status: RunStatus
    mode: RunMode
    started_at: str
    updated_at: str
    candidate: Candidate
    progress: Progress
    actions: RunActions
    cases: list[CaseResult]
    finding: Finding | None = None
    revision: Revision | None = None
    review: Review | None = None
    error: ApiErrorBody | None = None


# ---- Route payloads -------------------------------------------------------


class Capabilities(Model):
    batch: bool
    voice: bool
    traces: bool
    alter: bool
    personas: bool


class HealthResponse(Model):
    status: Literal["ok"]
    commit: str
    capabilities: Capabilities


class ScenarioSummary(Model):
    id: str
    label: str
    description: str


class ScenariosResponse(Model):
    candidate: Candidate
    scenarios: list[ScenarioSummary]
    recorded_run_id: str | None = None


class CreateRunRequest(Model):
    candidate_id: Literal["monitor-support"]
    suite_id: Literal["probation-v1"]


class EventsPage(Model):
    events: list[RunEvent]
    next_after: int


class RetestRequest(Model):
    revision_id: str


class ReviewRequest(Model):
    decision: Literal["reviewed"]


class VoiceSessionRequest(Model):
    case_id: Literal["monitor-valid"]


class VoiceSessionResponse(Model):
    session_id: str
    signed_url: str
    expires_at: str


class ErrorResponse(Model):
    error: ApiErrorBody


class PreviewFixture(Model):
    """Shape of web/src/fixtures/preview-run.json (design preview only)."""

    run: RunView
    events: list[RunEvent]
