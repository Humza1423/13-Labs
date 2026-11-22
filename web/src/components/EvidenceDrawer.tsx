import { Dialog } from "./Dialog";
import { actors, clock, trust } from "./runLabels";
import type { RunEvent } from "../contract";

export type OpenEvidence = (id: string, trigger: HTMLElement) => void;

export function EvidenceChips({ ids, open }: { ids: string[]; open: OpenEvidence }) {
  return (
    <div className="evidence-chips">
      {[...new Set(ids)].map(id => (
        <button key={id} className="evidence-chip" onClick={event => open(id, event.currentTarget)}>
          <span aria-hidden="true">↗</span> {id}
        </button>
      ))}
    </div>
  );
}

export function EvidenceDrawer({ event, loading, onClose, returnFocus }: {
  event?: RunEvent; loading: boolean; onClose: () => void; returnFocus: HTMLElement | null;
}) {
  return (
    <Dialog title="Evidence record" onClose={onClose} returnFocus={returnFocus}>
      {!event ? <p role="status">{loading ? "Loading evidence…" : "This evidence is unavailable"}</p> : (
        <>
          <p className="eyebrow">{trust(event)} · {actors[event.actor]}</p>
          <h3>{event.label}</h3>
          <p className="event-summary">{event.summary}</p>
          <dl className="receipt-fields">
            <div><dt>Event ID</dt><dd>{event.id}</dd></div>
            <div><dt>Timestamp (UTC)</dt><dd><time dateTime={event.at}>{event.at}</time></dd></div>
            <div><dt>Run</dt><dd>{event.runId}</dd></div>
            <div><dt>Stage</dt><dd>{event.stage}</dd></div>
            {event.caseId && <div><dt>Case</dt><dd>{event.caseId}</dd></div>}
            {event.version && <div><dt>Version</dt><dd>{event.version}</dd></div>}
            {event.tool && <div><dt>Tool</dt><dd>{event.tool}</dd></div>}
            {event.correlationId && <div><dt>Correlation ID</dt><dd>{event.correlationId}</dd></div>}
            {event.evidence?.map((field, index) => <div key={index}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}
          </dl>
        </>
      )}
    </Dialog>
  );
}

export function EventRow({ event, selected, open }: {
  event: RunEvent; selected?: boolean; open: OpenEvidence;
}) {
  return (
    <li id={`event-${event.id}`}>
      <button className="event-row" aria-current={selected || undefined} onClick={click => open(event.id, click.currentTarget)}>
        <span className="event-time"><time dateTime={event.at}>{clock(event.at)}</time><small>{actors[event.actor]}</small></span>
        <span className="event-main"><small data-source={event.sourceTrust}>{trust(event)}</small><strong>{event.label}</strong><span>{event.summary}</span></span>
        <span aria-hidden="true">↗</span>
      </button>
    </li>
  );
}
