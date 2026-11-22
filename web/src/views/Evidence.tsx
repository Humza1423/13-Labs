import { ArrowRight, FileSearch, GitPullRequest, ListFilter } from "lucide-react";
import { useState } from "react";
import type { RunEvent, RunView } from "../contract";
import { EventRow, EvidenceChips } from "../components/EvidenceDrawer";
import type { OpenEvidence } from "../components/EvidenceDrawer";
import { provenance } from "../components/runLabels";

const filters = [
  { id: "all", label: "All events" },
  { id: "transcript", label: "Transcript" },
  { id: "tools", label: "Tool activity" },
] as const;
type Filter = typeof filters[number]["id"];

export function Evidence({ run, preview, events, selected, open, retest, actionLocked, recordedHref, labHref, reportHref }: {
  run?: RunView; preview: boolean; events: RunEvent[]; selected?: string; open: OpenEvidence; retest: () => void;
  actionLocked: boolean; recordedHref?: string; labHref: string; reportHref: string;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const visible = events.filter(event => event.id === selected || filter === "all"
    || (filter === "transcript" && event.type === "message")
    || (filter === "tools" && !!event.tool));
  const finding = run?.finding;
  const revision = run?.revision;
  return (
    <>
      <div className="view-heading">
        <div>
          <p className="eyebrow"><span className="eyebrow-mark" /> {run ? provenance(run, preview) : "Probation shift"}</p>
          <h1>Evidence</h1>
          <p className="view-description">Follow the conversation, tool activity, and authorization decisions.</p>
        </div>
      </div>
      <section className="gd-card finding-panel" aria-labelledby="finding-title">
        <p className="proof-kicker"><FileSearch size={18} /> Investigator finding</p>
        <h2 id="finding-title">{finding?.summary ?? "The finding is unavailable"}</h2>
        <p>{finding?.observation ?? (!run ? "Start a shift in the lab to collect evidence."
          : run.status === "failed" ? "The shift ended before 13 published a finding."
          : "13 publishes a finding after reviewing the baseline tool history.")}</p>
        {finding && <EvidenceChips events={events} ids={finding.eventIds} open={open} />}
        {!run && <a className="button secondary" href={labHref}>Open lab<ArrowRight size={17} /></a>}
        {run?.status === "failed" && recordedHref && <a className="text-action" href={recordedHref}>Open recorded run <ArrowRight size={16} /></a>}
      </section>
      {revision && <section className="gd-card revision-panel" aria-labelledby="revision-title">
        <div className="section-heading">
          <div><p className="proof-kicker"><GitPullRequest size={17} /> Workflow revision</p><h2 id="revision-title">Instructions for the next shift</h2></div>
          {run.status === "ready_to_retest" && (run.actions.canRetest && run.mode !== "replay"
            ? <button className="button primary" onClick={retest} disabled={actionLocked}>Apply in lab and retest<ArrowRight size={18} /></button>
            : <p>Retest is available to the run owner.</p>)}
          {(run.status === "retest_running" || run.status === "complete") &&
            <a className="button secondary" href={reportHref}>{run.status === "complete" ? "Open readiness report" : "Follow the retest"}<ArrowRight size={18} /></a>}
        </div>
        <div className="workflow-diff">
          <div><h3>Baseline</h3><p>{revision.before}</p></div>
          <div><h3>Revised</h3><p>{revision.after}</p></div>
        </div>
        <div className="revision-meta">
          <p>{revision.source === "rule" ? "Rule-proposed" : "Model-proposed"} · {revision.fromVersionHash} → {revision.versionHash}</p>
          <EvidenceChips events={events} ids={revision.eventIds} open={open} />
        </div>
      </section>}
      <section className="gd-card timeline-panel" aria-labelledby="timeline-title">
        <div className="section-heading">
          <div><p className="proof-kicker"><ListFilter size={17} /> Event record</p><h2 id="timeline-title">Evidence timeline</h2></div>
          <div className="gd-period" role="group" aria-label="Filter evidence">
            {filters.map(option => <button key={option.id} aria-pressed={filter === option.id} onClick={() => setFilter(option.id)}>{option.label}</button>)}
          </div>
        </div>
        {visible.length > 0 ? <ol className="event-list">{visible.map(event =>
          <EventRow key={event.id} event={event} selected={event.id === selected} open={open} />)}</ol>
          : <p className="empty-state" role="status">{events.length ? "No events match this filter." : "No evidence has arrived yet."}</p>}
      </section>
    </>
  );
}
