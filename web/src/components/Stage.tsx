// Alibi staging and original character art by the Alibi team, rayanali2/HackPrinceton,
// web/src/sandbox-stage.tsx and web/src/assets at f109e4d5c6720ffa433aa65b0800994afbe416d7.
import { FileCheck2, Headphones, Monitor, PackageCheck, ReceiptText, Search, Truck } from "lucide-react";
import type { RunEvent, Stage as ShiftStage } from "../contract";
import customer from "../assets/rogue.png";
import defender from "../assets/worker.png";
import investigator from "../assets/investigator.png";
import { currency } from "./runLabels";

export function Stage({ events, stage, active }: {
  events: RunEvent[]; stage?: ShiftStage; active: boolean;
}) {
  const last = events.at(-1);
  // Keep the stage scoped to one case and leg so an unrelated ledger write
  // cannot appear to execute the disputed claim.
  const scene = [...events].reverse().find(event => event.caseId)?.caseId;
  const version = [...events].reverse().find(event => event.caseId === scene && event.version)?.version;
  const current = scene ? events.filter(event =>
    event.caseId === scene && (!event.version || !version || event.version === version)) : [];
  const order = current.find(event => event.type === "tool_returned" && event.tool === "lookup_order" && event.sourceTrust === "authoritative");
  const verification = current.find(event => event.type === "tool_returned" && event.tool === "verify_carrier_report" && event.sourceTrust === "authoritative");
  const attempt = current.find(event => event.type === "refund_attempted");
  const ledger = current.filter(event => event.type === "ledger_written");
  const executed = ledger.at(-1)?.evidence?.find(field => field.label === "Executed cents");
  const amount = executed && /^\d+$/.test(executed.value) ? currency(Number(executed.value)) : undefined;
  const check = verification?.evidence?.find(field => field.label === "Carrier check")?.value;
  const carrierResult = check === "matched" ? "Damage confirmed" : check === "conflicting" ? "Records conflict"
    : check === "unavailable" ? "Record unavailable" : verification ? "Record returned" : "Verification";
  const speaking = last?.type === "message" ? last.actor : last?.type === "finding_published" ? "thirteen" : "";
  const pulse = last?.id === attempt?.id && !!attempt;

  return (
    <section className="shift-stage" aria-label="Probation floor">
      <div className="stage-heading">
        <span><span className="status-dot" data-active={active} /> Probation floor</span>
        <span className="stage-floor-tag"><Monitor size={16} /> Refund support</span>
      </div>
      <div className="stage-room">
        <div className="room-wall" aria-hidden="true"><i /><i /><i /></div>
        <div className="room-floor" aria-hidden="true" />
        <div className="room-window" aria-hidden="true"><i /><i /><i /><i /></div>
        <div className="room-clock" aria-hidden="true" />
        <div className="actor-grid">
          <figure data-speaking={speaking === "customer"}>
            <div className="sprite"><img src={customer} alt="" draggable={false} /></div>
            <figcaption><Headphones size={15} /> Customer</figcaption>
          </figure>
          <figure data-speaking={speaking === "defender"} data-pulse={pulse}>
            <div className="sprite"><img src={defender} alt="" draggable={false} /></div>
            <figcaption><Monitor size={15} /> Defender</figcaption>
          </figure>
          <figure data-speaking={speaking === "thirteen" || stage === "investigation"}>
            <div className="sprite"><img src={investigator} alt="" draggable={false} /></div>
            <figcaption><Search size={15} /> 13</figcaption>
          </figure>
        </div>
      </div>
      <div className="station-grid" aria-label="Business tools">
        <div data-lit={!!order}><PackageCheck size={19} /><span><strong>Order desk</strong><small>{order ? "Record returned" : "Order lookup"}</small></span></div>
        <div data-lit={!!verification}><Truck size={19} /><span><strong>Carrier</strong><small>{carrierResult}</small></span></div>
        <div data-lit={!!ledger.length}>{ledger.length ? <FileCheck2 size={19} /> : <ReceiptText size={19} />}<span><strong>Fake ledger</strong><small>{ledger.length ? amount ?? "Ledger written" : attempt ? "Ledger unchanged" : currency(0)}</small></span></div>
      </div>
    </section>
  );
}
