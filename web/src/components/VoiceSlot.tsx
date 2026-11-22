import { MicOff } from "lucide-react";
import { lazy, Suspense } from "react";
import type { RunView } from "../contract";

const VoiceCall = lazy(() => import("../voice/VoiceCall").then(({ VoiceCall }) => ({ default: VoiceCall })));

export function VoiceSlot({ run, preview }: { run?: RunView; preview: boolean }) {
  if (!preview && run?.mode === "live" && run.actions.canStartVoice) {
    return (
      <Suspense fallback={<p role="status">Loading microphone controls…</p>}>
        <VoiceCall runId={run.id} />
      </Suspense>
    );
  }
  return (
    <section className="gd-card voice-slot" aria-labelledby="voice-title">
      <div className="voice-icon"><MicOff size={21} /></div>
      <div>
        <h2 id="voice-title">Talk to the defender</h2>
        <p id="voice-reason">{run?.mode === "replay" ? "Recorded run · voice unavailable."
          : preview ? "Design preview · voice unavailable."
          : run?.mode === "fixture" ? "Scripted fixture · voice unavailable."
          : !run ? "Voice unavailable until a live shift starts."
          : "Voice unavailable for this run."}</p>
      </div>
      <button className="button secondary" disabled aria-describedby="voice-reason">Talk to the defender</button>
    </section>
  );
}
