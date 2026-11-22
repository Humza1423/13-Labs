import { MicOff } from "lucide-react";
import type { RunView } from "../contract";

/** Devin's voice widget mounts here with the current run and its scoped actions. */
export function VoiceSlot({ run }: { run?: RunView }) {
  return (
    <section className="gd-card voice-slot" aria-labelledby="voice-title">
      <div className="voice-icon"><MicOff size={21} /></div>
      <div>
        <h2 id="voice-title">Talk to the defender</h2>
        <p>{run?.mode === "replay" ? "Recorded run · voice unavailable."
          : run?.mode === "fixture" ? "Design preview · voice unavailable."
          : !run ? "Voice unavailable until a live shift starts."
          : !run.actions.canStartVoice ? "Voice unavailable for this run."
          : "Voice unavailable in this build."}</p>
      </div>
      <button className="button secondary" disabled>Talk to the defender</button>
    </section>
  );
}
