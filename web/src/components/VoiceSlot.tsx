import { MicOff } from "lucide-react";
import { lazy, Suspense } from "react";
import type { RunView } from "../contract";

const VoiceCall = lazy(() => import("../voice/VoiceCall").then(({ VoiceCall }) => ({ default: VoiceCall })));

export function VoiceSlot({ run }: { run?: RunView }) {
  if (run?.mode === "live" && run.actions.canStartVoice) {
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
        <p>{run?.mode === "replay" ? "Recorded run · voice unavailable."
          : run?.mode === "fixture" ? "Design preview · voice unavailable."
          : !run ? "Start a live shift to make a call."
          : "Voice unavailable for this run."}</p>
      </div>
      <button className="button secondary" disabled>Talk to the defender</button>
    </section>
  );
}
