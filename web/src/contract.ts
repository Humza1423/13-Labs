// Shared JSON contract (C0). Mirror of src/thirteen_labs/contracts.py.
// camelCase on the wire. Owned by Firstmate: propose changes through Firstmate.

export type RunStatus =
  | "queued" | "baseline_running" | "investigating" | "ready_to_retest"
  | "retest_running" | "complete" | "failed";
export type RunMode = "live" | "fixture" | "replay";
export type Version = "baseline" | "revised";
export type Outcome =
  | "refund_proposed" | "declined" | "needs_info" | "escalated" | "incomplete";
export type AuthDecision = "not_requested" | "allowed" | "denied" | "pending" | "unavailable";
export type Stage = "baseline" | "investigation" | "revision" | "retest" | "review";
export type EventType =
  | "phase_changed" | "message" | "tool_called" | "tool_returned"
  | "refund_attempted" | "authorization_decided" | "ledger_written"
  | "finding_published" | "revision_proposed" | "case_scored" | "run_failed";
export type Actor = "customer" | "defender" | "thirteen" | "evaluator" | "authorization";
export type SourceTrust = "customer_supplied" | "authoritative" | "system";
export type ToolName = "lookup_order" | "verify_carrier_report" | "request_refund" | "record_resolution";
export type CarrierCheck = "not_called" | "matched" | "conflicting" | "unavailable";
export type CaseId = "monitor-conflict" | "monitor-valid" | "monitor-incomplete" | "monitor-heldout";
export type PatchChange = "require_carrier_match" | "request_missing_evidence";

export interface RunEvent {
  id: string; seq: number; at: string; runId: string;
  caseId?: string; version?: Version; stage: Stage; type: EventType;
  actor: Actor;
  label: string; summary: string; correlationId?: string;
  sourceTrust?: SourceTrust;
  tool?: ToolName;
  evidence?: Array<{ label: string; value: string }>;
}

export interface CaseScore {
  expected: "approve" | "decline" | "needs_info"; passed: boolean;
  unauthorizedAttempt: boolean; unauthorizedExecution: boolean;
  legitimateCompleted: boolean; unnecessaryEscalationOrRefusal: boolean;
}

export interface CaseResult {
  caseId: string; label: string; cohort: "paired" | "held_out";
  version: Version; outcome: Outcome; authorization: AuthDecision;
  requestedCents: number; executedCents: number;
  carrierCheck: CarrierCheck;
  authorizationSource: "local" | "alter"; authorizationReceiptId?: string;
  source: "elevenlabs" | "model" | "fixture";
  conversationId?: string; versionHash: string;
  score?: CaseScore;
  trace?: { provider: "traceroot"; traceId: string; url?: string };
  evidenceIds: string[]; incompleteReason?: string;
}

export interface Finding { summary: string; observation: string; eventIds: string[] }

export interface Revision {
  id: string; fromVersionHash: string; versionHash: string;
  source: "model" | "rule"; before: string; after: string;
  changes: PatchChange[];
  eventIds: string[];
}

export interface ApiErrorBody { code: string; message: string; retryable: boolean }

export interface RunView {
  id: string; status: RunStatus; mode: RunMode; startedAt: string;
  updatedAt: string; candidate: { id: string; name: string };
  progress: { completed: number; total: number };
  actions: { canRetest: boolean; canReview: boolean; canStartVoice: boolean };
  cases: CaseResult[]; finding?: Finding; revision?: Revision;
  review?: { status: "awaiting_review" | "reviewed"; recommendation: string };
  error?: ApiErrorBody;
}

// ---- Route payloads -------------------------------------------------------

/** GET /api/health */
export interface HealthResponse {
  status: "ok"; commit: string;
  capabilities: { batch: boolean; voice: boolean; traces: boolean; alter: boolean; personas: boolean };
}

/** GET /api/scenarios */
export interface ScenariosResponse {
  candidate: { id: string; name: string };
  scenarios: Array<{ id: string; label: string; description: string }>;
  recordedRunId?: string;
}

/** POST /api/runs (header Idempotency-Key) -> 202 RunView */
export interface CreateRunRequest { candidateId: "monitor-support"; suiteId: "probation-v1" }

/** GET /api/runs/{id}/events?after=N -> up to 100 events, ID order */
export interface EventsPage { events: RunEvent[]; nextAfter: number }

/** POST /api/runs/{id}/retest (header Idempotency-Key) -> 202 RunView */
export interface RetestRequest { revisionId: string }

/** POST /api/runs/{id}/review -> RunView */
export interface ReviewRequest { decision: "reviewed" }

/** POST /api/runs/{id}/voice-sessions */
export interface VoiceSessionRequest { caseId: "monitor-valid" }
export interface VoiceSessionResponse { sessionId: string; signedUrl: string; expiresAt: string }

/** Every non-2xx API response. */
export interface ErrorResponse { error: ApiErrorBody }

/** Poll cadence for active phases. */
export const EVENT_POLL_MS = 750;
export const TERMINAL_STATUSES: readonly RunStatus[] = ["complete", "failed"];

/** Shape of src/fixtures/preview-run.json. Design preview only, never measured. */
export interface PreviewFixture { run: RunView; events: RunEvent[] }
