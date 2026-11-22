import type { AuthDecision, CaseResult, Outcome, RunEvent, RunMode, RunStatus, Stage } from "../contract";

export const stages: { id: Stage; name: string }[] = [
  { id: "baseline", name: "Baseline" },
  { id: "investigation", name: "Investigation" },
  { id: "revision", name: "Revision" },
  { id: "retest", name: "Retest" },
  { id: "review", name: "Review" },
];

export const phase: Record<RunStatus, Stage> = {
  queued: "baseline",
  baseline_running: "baseline",
  investigating: "investigation",
  ready_to_retest: "revision",
  retest_running: "retest",
  complete: "review",
  failed: "baseline",
};

export const statusLabel: Record<RunStatus, string> = {
  queued: "Queued",
  baseline_running: "Baseline running",
  investigating: "Investigating",
  ready_to_retest: "Ready to retest",
  retest_running: "Retest running",
  complete: "Ready for review",
  failed: "Shift interrupted",
};

export const provenance: Record<RunMode, string> = {
  live: "Live run", fixture: "Design preview", replay: "Recorded run",
};

export const actors = {
  customer: "Customer", defender: "Defender", thirteen: "13",
  evaluator: "Evaluator", authorization: "Authorization",
};

export function trust(event: RunEvent) {
  if (event.sourceTrust === "customer_supplied" || event.actor === "customer") return "Customer statement";
  return event.sourceTrust === "authoritative" ? "Authoritative record" : "System action";
}

export const outcome: Record<Outcome, string> = {
  refund_proposed: "Refund proposed", declined: "Refund declined", needs_info: "More information requested",
  escalated: "Escalated", incomplete: "Incomplete",
};

export const authorization: Record<AuthDecision, string> = {
  allowed: "Execution allowed", denied: "Execution denied", pending: "Approval pending",
  unavailable: "Authorization unavailable", not_requested: "Authorization not requested",
};

export const carrier: Record<CaseResult["carrierCheck"], string> = {
  not_called: "No carrier check recorded", matched: "Carrier record matched",
  conflicting: "Carrier record conflicts", unavailable: "Carrier record unavailable",
};

export const currency = (cents: number) => new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", minimumFractionDigits: cents % 100 ? 2 : 0,
}).format(cents / 100);

export const clock = (value: string) => value.match(/T(\d\d:\d\d:\d\d)/)?.[1] ?? value;
