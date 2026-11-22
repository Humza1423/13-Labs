import { ArrowRight, Check, Link2 } from "lucide-react";
import { useState } from "react";
import type { CaseResult, RunEvent, RunView } from "../contract";
import { EvidenceChips } from "../components/EvidenceDrawer";
import type { OpenEvidence } from "../components/EvidenceDrawer";
import { StageProgress } from "../components/StageProgress";
import { phase, authorization, carrier, currency, outcome, provenance } from "../components/runLabels";

const caseOrder = ["monitor-conflict", "monitor-valid", "monitor-incomplete", "monitor-heldout"];
const source = { fixture: "Scripted fixture", model: "Model", elevenlabs: "ElevenLabs" };

export function pairCases(cases: CaseResult[]) {
  return [...new Set(cases.map(item => item.caseId))]
    .sort((left, right) => caseOrder.indexOf(left) - caseOrder.indexOf(right))
    .map(id => ({
      id,
      baseline: cases.find(item => item.caseId === id && item.version === "baseline"),
      revised: cases.find(item => item.caseId === id && item.version === "revised"),
    }));
}

function scored(result: CaseResult | undefined): result is CaseResult & { score: NonNullable<CaseResult["score"]> } {
  return !!result?.score && result.outcome !== "incomplete";
}

function counts(results: CaseResult[]) {
  const eligible = results.filter(scored);
  const ineligible = eligible.filter(item => item.score.expected !== "approve");
  const valid = eligible.filter(item => item.score.expected === "approve");
  return [
    { name: "Unsupported attempts", n: ineligible.filter(item => item.score.unauthorizedAttempt).length, d: ineligible.length },
    { name: "Unauthorized executions", n: ineligible.filter(item => item.score.unauthorizedExecution).length, d: ineligible.length },
    { name: "Valid cases completed", n: valid.filter(item => item.score.legitimateCompleted).length, d: valid.length },
    { name: "Carrier checks performed", n: eligible.filter(item => item.carrierCheck !== "not_called").length, d: eligible.length },
    { name: "Unnecessary refusal or escalation", n: valid.filter(item => item.score.unnecessaryEscalationOrRefusal).length, d: valid.length },
  ];
}

const tally = (metric: { n: number; d: number }) => metric.d ? `${metric.n}/${metric.d}` : "—";

function Gauge({ completed, total }: RunView["progress"]) {
  const ticks = 57;
  return (
    <div className="gauge">
      <svg viewBox="0 0 260 144" aria-hidden="true">
        {Array.from({ length: ticks }, (_, i) => {
          const angle = Math.PI + i / (ticks - 1) * Math.PI;
          const active = total > 0 && i < Math.round(completed / total * ticks);
          const innerRadius = i % 7 === 0 ? 91 : 98;
          return <line key={i} x1={130 + 114 * Math.cos(angle)} y1={127 + 114 * Math.sin(angle)}
            x2={130 + innerRadius * Math.cos(angle)} y2={127 + innerRadius * Math.sin(angle)}
            stroke={active ? "#a9b2ff" : "#777985"} strokeWidth="2.8" strokeLinecap="round" />;
        })}
      </svg>
      <div className="gauge-label"><strong>{completed}<small> / {total}</small></strong><span>Case legs complete</span></div>
    </div>
  );
}

const glyphs: Record<string, string[]> = {
  "0": ["01110","10001","10011","10101","11001","10001","01110"],
  "1": ["00100","01100","00100","00100","00100","00100","01110"],
  "2": ["01110","10001","00001","00010","00100","01000","11111"],
  "3": ["11111","00010","00100","00010","00001","10001","01110"],
  "4": ["00010","00110","01010","10010","11111","00010","00010"],
  "5": ["11111","10000","11110","00001","00001","10001","01110"],
  "6": ["00110","01000","10000","11110","10001","10001","01110"],
  "7": ["11111","00001","00010","00100","01000","01000","01000"],
  "8": ["01110","10001","10001","01110","10001","10001","01110"],
  "9": ["01110","10001","10001","01111","00001","00010","01100"],
  "/": ["00001","00010","00010","00100","01000","01000","10000"],
  "—": ["00000","00000","00000","11111","00000","00000","00000"],
};

function DotNumber({ value }: { value: string }) {
  return (
    <span className="dot-number">
      <span className="sr-only">{value === "—" ? "No eligible legs" : value}</span>
      <svg viewBox={`0 0 ${value.length * 30 - 3} 35`} aria-hidden="true">
        {[...value].flatMap((char, index) => glyphs[char].flatMap((row, y) => [...row].map((dot, x) =>
          <circle key={`${index}-${y}-${x}`} cx={index * 30 + x * 5.2 + 3} cy={y * 5 + 2.5} r={dot === "1" ? 1.8 : 1}
            fill="currentColor" opacity={dot === "1" ? 1 : .1} />)))}
      </svg>
    </span>
  );
}

function OutcomeTrack({ pairs, held }: { pairs: ReturnType<typeof pairCases>; held: boolean }) {
  return <div className="report-chart" aria-label="Independent case scores">
    <div className="score-chart-head"><span>Case decision</span>{!held && <span>Baseline</span>}<span>Revised</span></div>
    {pairs.map(pair => <div className="score-chart-row" key={pair.id}>
      <span>{(pair.revised ?? pair.baseline)?.label}</span>
      {(held ? [pair.revised] : [pair.baseline, pair.revised]).map((leg, index) => {
        const label = scored(leg) ? (leg.score.passed ? "Passed" : "Failed")
          : leg?.outcome === "incomplete" ? "Incomplete" : "Awaiting";
        return <span className="score-cell" data-result={label} key={index}>{label}</span>;
      })}
    </div>)}
    {!pairs.length && <p>Awaiting case scores.</p>}
  </div>;
}

function CaseLeg({ result, label, absent, events, open, preview }: {
  result?: CaseResult; label: string; absent: string; events: RunEvent[]; open: OpenEvidence; preview: boolean;
}) {
  if (!result) return <div className="case-leg"><p className="report-label">{label}</p><strong aria-label={absent}>—</strong><p>{absent}</p></div>;
  const decision = result.outcome === "refund_proposed" && result.executedCents ? "Refund executed" : outcome[result.outcome];
  return (
    <div className="case-leg">
      <p className="report-label">{label}</p>
      <h4>{decision}</h4>
      {result.incompleteReason && <p>{result.incompleteReason}</p>}
      <p>{result.requestedCents ? `Refund requested · ${currency(result.requestedCents)}` : "No refund requested"}</p>
      <p>{authorization[result.authorization]} · {result.authorizationSource === "alter" ? "Alter" : "Local policy"}</p>
      <p>{result.executedCents ? `Ledger execution · ${currency(result.executedCents)}` : "Ledger unchanged"}</p>
      <p>{carrier[result.carrierCheck]}</p>
      <p>{preview ? "Design preview" : source[result.source]} · <code title={result.versionHash}>{result.versionHash.slice(0, 12)}</code></p>
      {result.conversationId && <p>Conversation · {result.conversationId}</p>}
      {result.authorizationReceiptId && <p>Authorization receipt · {result.authorizationReceiptId}</p>}
      {result.trace && <p>TraceRoot · {result.trace.url?.startsWith("https://")
        ? <a href={result.trace.url} target="_blank" rel="noreferrer">{result.trace.traceId}</a> : result.trace.traceId}</p>}
      {result.evidenceIds.length > 0 && <details className="case-receipts"><summary>Evidence records ({result.evidenceIds.length})</summary>
        <EvidenceChips events={events} ids={result.evidenceIds} open={open} />
      </details>}
    </div>
  );
}

export function Report({ run, events, actionLocked, review, open, labHref, evidenceHref, shareUrl, preview }: {
  run?: RunView; events: RunEvent[]; actionLocked: boolean; review: () => void; open: OpenEvidence;
  labHref: string; evidenceHref: string; shareUrl?: string; preview: boolean;
}) {
  const [cohort, setCohort] = useState<"paired" | "held_out">("paired");
  const [copyStatus, setCopyStatus] = useState("");
  const [showLink, setShowLink] = useState(false);
  const cases = run?.cases ?? [];
  const pairs = pairCases(cases);
  const held = pairs.filter(pair => pair.revised?.cohort === "held_out");
  const paired = pairs.filter(pair => (pair.revised ?? pair.baseline)?.cohort === "paired");
  const comparable = paired.filter(pair => {
    const { baseline, revised } = pair;
    return scored(baseline) && scored(revised)
      && baseline.source === revised.source
      && baseline.authorizationSource === revised.authorizationSource
      && baseline.score.expected === revised.score.expected;
  });
  const viewingHeld = cohort === "held_out" && held.length > 0;
  const before = counts(viewingHeld ? [] : comparable.flatMap(pair => pair.baseline ?? []));
  const after = counts((viewingHeld ? held : comparable).flatMap(pair => pair.revised ?? []));
  const progress = run?.progress ?? { completed: 0, total: 0 };
  const reviewed = run?.review?.status === "reviewed";
  const reviewReason = reviewed ? "This run has been reviewed."
    : run?.mode === "replay" ? "Recorded runs are read-only."
    : run?.status === "failed" ? "The shift was interrupted."
    : run?.status !== "complete" ? "Available when the retest finishes."
    : !run.actions.canReview ? "Review is available to the run owner."
    : actionLocked ? "Waiting for the shift service." : undefined;
  const incomplete = cases.filter(item => item.outcome === "incomplete").length;
  const version = run?.revision?.versionHash ?? cases.find(item => item.version === "revised")?.versionHash;
  const valid = cases.find(item => item.caseId === "monitor-valid" && item.version === "revised");

  async function copy() {
    if (!shareUrl) return;
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopyStatus("Run link copied.");
    } catch {
      setShowLink(true);
      setCopyStatus("Select and copy the run link.");
    }
  }

  return (
    <div className="fpb">
      <div className="report-top">
        <div><p className="report-label">{run ? provenance(run, preview) : "Probation shift"}</p><h1>Readiness report</h1></div>
        {held.length > 0 && <div className="report-toggle" role="group" aria-label="Case cohort">
          <button aria-pressed={!viewingHeld} onClick={() => setCohort("paired")}>Paired cases</button>
          <button aria-pressed={viewingHeld} onClick={() => setCohort("held_out")}>Held out</button>
        </div>}
      </div>
      <StageProgress stage={run?.status === "failed" ? events.at(-1)?.stage : run ? phase[run.status] : undefined} />
      {!run && <p className="report-note">Start a shift in the lab to collect results. <a href={labHref}>Open lab <ArrowRight size={16} /></a></p>}
      {preview && <p className="report-note">Example outcomes from the design preview.</p>}
      {!preview && run?.mode === "fixture" && <p className="report-note">These results come from a scripted defender.</p>}
      {run?.mode === "replay" && <p className="report-note">Saved results from a previous run.</p>}
      {run?.status === "ready_to_retest" && <p className="report-note">Apply the proposed revision to test the candidate. <a href={evidenceHref}>Open evidence <ArrowRight size={16} /></a></p>}
      {run?.status === "retest_running" && <p className="report-note">Retest running · Results appear as case pairs finish.</p>}
      <div className="report-grid">
        <section className="report-card outcome-panel" aria-labelledby="outcomes-title">
          <h2 id="outcomes-title">{viewingHeld ? "Held-out outcome" : "Paired outcomes"}</h2>
          <p>Unsupported refund attempts</p>
          <div className="outcome-numbers">
            {!viewingHeld && <div><span>Baseline</span><DotNumber value={tally(before[0])} /></div>}
            {!viewingHeld && <ArrowRight size={24} aria-hidden="true" />}
            <div><span>Revised</span><DotNumber value={tally(after[0])} /></div>
          </div>
          <OutcomeTrack pairs={viewingHeld ? held : paired} held={viewingHeld} />
          <p className="chart-caption">{viewingHeld ? "Revised-only case legs" : `${comparable.length} comparable case ${comparable.length === 1 ? "pair" : "pairs"} · same adapter and authorization mode`}{incomplete ? ` · ${incomplete} incomplete` : ""}</p>
        </section>
        <section className="report-card readiness-panel" aria-labelledby="readiness-title">
          <h2 id="readiness-title">Readiness review</h2>
          <Gauge {...progress} />
          <dl className="review-facts">
            <div><dt>Revision</dt><dd>{version ? <code title={version}>{version.slice(0, 12)}</code> : "Awaiting revision"}</dd></div>
            <div><dt>Valid claim</dt><dd>{valid ? valid.outcome === "incomplete" ? "Incomplete" : valid.executedCents ? "Refund executed" : outcome[valid.outcome] : "Awaiting retest"}</dd></div>
            <div><dt>Review status</dt><dd>{reviewed ? "Reviewed" : run?.review ? "Awaiting review" : "Awaiting results"}</dd></div>
          </dl>
          {run?.review ? <details className="review-recommendation"><summary>Evaluator recommendation</summary>
            <p>{run.review.recommendation}</p></details>
            : <p>{incomplete ? "Open the incomplete case for its recorded reason." : "The evaluator publishes a recommendation when the shift finishes."}</p>}
          {run?.status === "complete" && <p>Review records your decision for this lab run.</p>}
          <div className="report-actions">
            <button className="button report-action primary" onClick={review} disabled={!!reviewReason} aria-describedby={reviewReason ? "review-reason" : undefined}><Check size={17} />{preview ? "Mark reviewed in preview" : "Mark reviewed"}</button>
            {shareUrl && <button className="button report-action" onClick={copy}><Link2 size={17} />Copy run link</button>}
          </div>
          {reviewReason && <p id="review-reason" role="status">{reviewReason}</p>}
          <p role="status">{copyStatus}</p>
          {showLink && <input aria-label="Run link" readOnly value={shareUrl} onFocus={event => event.currentTarget.select()} />}
        </section>
        <section className="metric-strip" aria-label="Outcome metrics">
          {after.slice(1).map((metric, index) => {
            const previous = before[index + 1];
            return <div className="report-card metric-tile" key={metric.name}>
              <h3>{metric.name}</h3>
              <p>{!viewingHeld && <><span aria-label="Baseline">{tally(previous)}</span><ArrowRight size={16} /></>}<strong aria-label="Revised">{tally(metric)}</strong></p>
              <div className="comparison-bars" aria-hidden="true">
                {!viewingHeld && <span><i style={{ width: previous.d ? `${100 * previous.n / previous.d}%` : 0 }} /></span>}
                <span><i style={{ width: metric.d ? `${100 * metric.n / metric.d}%` : 0 }} /></span>
              </div>
            </div>;
          })}
        </section>
        <section className="report-card case-matrix" aria-labelledby="case-title">
          <h2 id="case-title">{viewingHeld ? "Held-out case" : "Case comparison"}</h2>
          <p>{viewingHeld ? "The held-out case ran against the revised workflow." : "A denied execution contains the attempt. An unsupported request still counts as a decision failure."}</p>
          {(viewingHeld ? held : paired).map(pair => (
            <article key={pair.id} className="case-pair">
              <h3>{(pair.revised ?? pair.baseline)?.label}</h3>
              <div className="case-legs">
                <CaseLeg preview={preview} events={events} label="Baseline" result={pair.baseline} absent={viewingHeld ? "No baseline for the held-out case." : "Baseline result unavailable."} open={open} />
                <CaseLeg preview={preview} events={events} label="Revised" result={pair.revised} absent="Revised result unavailable." open={open} />
              </div>
            </article>
          ))}
          {!(viewingHeld ? held : paired).length && <p className="empty-state">Case results are unavailable.</p>}
        </section>
      </div>
    </div>
  );
}
