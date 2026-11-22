import { Activity, ArrowRight, Check, ClipboardList, Play, ShieldCheck } from "lucide-react";
import type { RunEvent, RunView, Stage as StageId } from "../contract";
import { EventRow } from "../components/EvidenceDrawer";
import type { OpenEvidence } from "../components/EvidenceDrawer";
import { PlaybackControls, useRecordedPlayback } from "../components/Playback";
import { Stage } from "../components/Stage";
import { VoiceSlot } from "../components/VoiceSlot";
import { authorization, currency, phase, provenance, stages, statusLabel } from "../components/runLabels";

export function Lab({
  run, events, start, startLocked, startLabel, evidenceHref, reportHref, open, recordedHref, candidateName, preview,
}: {
  run?: RunView; events: RunEvent[]; start: () => void; startLocked: boolean; startLabel: string;
  evidenceHref: string; reportHref: string; open: OpenEvidence; recordedHref?: string;
  candidateName: string; preview: boolean;
}) {
  const playback = useRecordedPlayback(events.length);
  const shown = run?.mode === "replay" ? events.slice(0, playback.shown) : events;
  const stage = run?.mode === "replay" && playback.shown < events.length
    ? shown.at(-1)?.stage ?? "baseline"
    : run?.status === "failed" ? shown.at(-1)?.stage : run ? phase[run.status] : undefined;
  const activeIndex = stages.findIndex(item => item.id === stage);
  const active = run?.status === "queued" || run?.status === "baseline_running"
    || run?.status === "investigating" || run?.status === "retest_running";
  const focusStage: StageId = run?.mode === "replay" ? shown.at(-1)?.stage ?? "baseline"
    : stage === "retest" || stage === "review" ? "retest" : "baseline";
  const floorEvents = shown.filter(event => event.stage === focusStage && (!event.caseId || event.caseId === "monitor-conflict"));
  const latest = (run?.status === "ready_to_retest" || run?.status === "investigating")
    ? [...shown].reverse().find(event => event.type === "finding_published") ?? floorEvents.at(-1)
    : floorEvents.at(-1) ?? shown.at(-1);
  const attempt = [...floorEvents].reverse().find(event => event.type === "refund_attempted");
  const auth = [...floorEvents].reverse().find(event => event.type === "authorization_decided");
  const write = [...floorEvents].reverse().find(event => event.type === "ledger_written");
  const flag = run?.cases.find(item => item.caseId === "monitor-conflict" &&
    item.version === (focusStage === "retest" ? "revised" : "baseline"));
  const showCaseResult = run?.mode !== "replay" || playback.shown === events.length;
  const requestLabel = attempt?.label ?? (showCaseResult && flag?.requestedCents
    ? `Refund requested · ${currency(flag.requestedCents)}` : undefined);
  const authLabel = auth?.label ?? (showCaseResult && flag ? authorization[flag.authorization] : "Awaiting authorization");
  const ledgerLabel = write?.label ?? (showCaseResult && flag?.executedCents
    ? `Ledger execution · ${currency(flag.executedCents)}` : "Ledger unchanged");

  return (
    <>
      <div className="view-heading">
        <div>
          <p className="eyebrow"><span className="eyebrow-mark" /> Refund support lab</p>
          <h1>The AI Probation Shift</h1>
          <p className="view-description">Put a candidate through a refund shift and review the evidence.</p>
        </div>
        <div className="assignment">
          <div className="assignment-icon"><ClipboardList size={23} /></div>
          <div><small>Current assignment</small><strong>$900 damaged-monitor claim</strong></div>
        </div>
      </div>
      <div className="lab-grid">
        <Stage events={floorEvents} stage={stage} active={active} />
        <section className="gd-card proof-panel" aria-labelledby="brief-title">
          <span className="proof-kicker"><Activity size={17} /> {run ? "Shift activity" : "Shift brief"}</span>
          <div className="proof-title">
            <h2 id="brief-title">{run?.candidate.name ?? candidateName}</h2>
            <p>{run ? provenance[run.mode] : preview ? "Design preview available" : "Synthetic orders and fake money"}</p>
          </div>
          {!run && <p className="brief-copy">The defender handles the claim. 13 checks the tool history and proposes a workflow revision for you to test.</p>}
          {run && latest && <ol className="event-list latest"><EventRow event={latest} open={open} /></ol>}
          {requestLabel && <div className="refund-tape" aria-label="Refund outcome">
            <strong>{requestLabel}</strong><span>{authLabel}</span><span>{ledgerLabel}</span>
          </div>}
          <div className="proof-action">
            {!run ? <button className="button primary" disabled={startLocked} onClick={start}><Play size={18} />{startLabel}<ArrowRight size={18} /></button>
              : run.status === "complete" ? <a className="button primary" href={reportHref}>Open readiness report<ArrowRight size={18} /></a>
              : run.status === "ready_to_retest" || run.status === "failed" ? <a className="button primary" href={evidenceHref}>Inspect the evidence<ArrowRight size={18} /></a>
              : <p className="activity-status"><span className="status-dot" data-active />{run.status === "investigating" ? "13 is reviewing the tool history." : statusLabel[run.status]}</p>}
            {!run && recordedHref && <a className="text-action" href={recordedHref}>Open recorded run <ArrowRight size={16} /></a>}
          </div>
        </section>
      </div>
      <ol className="stage-track" aria-label="Shift stages">
        {stages.map((item, index) => (
          <li key={item.id} aria-current={stage === item.id ? "step" : undefined} data-passed={index < activeIndex}>
            <span className="step-index">{index < activeIndex ? <Check size={15} /> : index + 1}</span><span>{item.name}</span>
          </li>
        ))}
      </ol>
      {run?.mode === "replay" && <PlaybackControls playback={playback} />}
      <div className="lab-footer">
        <p><ShieldCheck size={16} /> Refund authorization and fake ledger execution are tracked separately.</p>
      </div>
      <VoiceSlot run={run} />
    </>
  );
}
