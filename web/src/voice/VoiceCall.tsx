import { ConversationProvider, useConversation } from "@elevenlabs/react";
import { Mic } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ToolName, VoiceSessionResponse } from "../contract";
import "./voice.css";

type Utterance = { id: number; role: "agent" | "user"; text: string };
type Receipt = { eventId: string; tool: ToolName; callId: string; type: string };
type VoiceProof = { conversationId: string | null; executedCents: number; receipts: Receipt[] };

export function VoiceCall({ runId }: { runId: string }) {
  return (
    <ConversationProvider key={runId}>
      <VoiceControls runId={runId} />
    </ConversationProvider>
  );
}

function VoiceControls({ runId }: { runId: string }) {
  const base = `/api/runs/${encodeURIComponent(runId)}/voice-sessions`;
  const pending = useRef<AbortController | null>(null);
  const [starting, setStarting] = useState(false);
  const [sessionId, setSessionId] = useState<string>();
  const [conversationId, setConversationId] = useState<string>();
  const [utterances, setUtterances] = useState<Utterance[]>([]);
  const [error, setError] = useState<string>();
  const [proof, setProof] = useState<VoiceProof>();
  const [proofStatus, setProofStatus] = useState("");
  const conversation = useConversation({
    onConnect: ({ conversationId }) => setConversationId(conversationId),
    onMessage: ({ event_id, role, message }) => {
      setUtterances((current) => [...current.slice(-7), { id: event_id, role, text: message }]);
    },
    onError: (_message, cause) => {
      setError(cause instanceof DOMException && cause.name === "NotAllowedError"
        ? "Allow microphone access in your browser, then try again."
        : "The call could not connect. Check your microphone and try again.");
    },
  });

  useEffect(() => () => pending.current?.abort(), []);

  const connected = conversation.status === "connected";
  const connecting = starting || conversation.status === "connecting";
  const status = connecting ? "Connecting…"
    : connected ? (conversation.isSpeaking ? "Defender is speaking" : "Listening")
    : conversationId ? "Call ended" : "Ready to call";

  async function start() {
    if (pending.current || connected || connecting) return;
    setError(undefined);
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("Microphone access requires HTTPS and a supported browser.");
      return;
    }
    setUtterances([]);
    setSessionId(undefined);
    setConversationId(undefined);
    setProof(undefined);
    setProofStatus("");
    const controller = new AbortController();
    pending.current = controller;
    setStarting(true);
    try {
      const response = await fetch(base, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ caseId: "monitor-valid" }),
        signal: controller.signal,
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setError(body?.error?.message ?? "The lab could not start a call.");
        return;
      }
      const session: VoiceSessionResponse = await response.json();
      if (controller.signal.aborted) return;
      setSessionId(session.sessionId);
      conversation.startSession({
        signedUrl: session.signedUrl,
        connectionType: "websocket",
        dynamicVariables: { secret__lab_voice_session: session.sessionId },
      });
    } catch {
      if (!controller.signal.aborted) setError("The lab could not start a call. Try again.");
    } finally {
      pending.current = null;
      if (!controller.signal.aborted) setStarting(false);
    }
  }

  function end() {
    pending.current?.abort();
    setStarting(false);
    conversation.endSession();
  }

  async function loadProof() {
    if (!sessionId) return;
    setProofStatus("Checking tool receipts…");
    try {
      const response = await fetch(`${base}/${encodeURIComponent(sessionId)}`, { cache: "no-store" });
      if (!response.ok) throw new Error();
      const result: VoiceProof = await response.json();
      if (result.conversationId !== conversationId || result.receipts.length === 0) {
        setProofStatus("No tool receipts for this call yet.");
        return;
      }
      setProof(result);
      setProofStatus("");
    } catch {
      setProofStatus("Tool receipts are temporarily unavailable.");
    }
  }

  return (
    <section className="gd-card voice-slot" aria-label="Talk to the defender">
      <div className="voice-icon"><Mic aria-hidden="true" size={21} /></div>
      <div>
        <h2>Talk to the defender</h2>
        <p role="status">{status}</p>
        <p>Try cracked monitor order ORD-1302 with carrier reference CARR-4702.</p>
        {error && <p role="alert">{error}</p>}
      </div>
      <button type="button" className="button secondary" onClick={connected || connecting ? end : start}>
        {connected ? "End call" : connecting ? "Cancel call" : "Start microphone call"}
      </button>
      {utterances.length > 0 && (
        <ol className="voice-transcript" aria-label="Call transcript" aria-live="polite">
          {utterances.map(({ role, id, text }) => (
            <li key={`${role}-${id}`}><strong>{role === "user" ? "You" : "Defender"}</strong> {text}</li>
          ))}
        </ol>
      )}
      {conversationId && !connected && (
        <div className="voice-evidence">
          <button type="button" className="button secondary" onClick={loadProof}>Check call evidence</button>
          {proofStatus && <p role="status">{proofStatus}</p>}
          {proof && (
            <details open>
              <summary>{proof.receipts.length} tool receipts</summary>
              <p>ElevenLabs conversation {proof.conversationId}</p>
              <p>Simulated refund {(proof.executedCents / 100).toLocaleString("en-US", { style: "currency", currency: "USD" })}</p>
              <ul>{proof.receipts.map((receipt) => (
                <li key={receipt.eventId}><code>{receipt.tool}</code> · {receipt.type.replaceAll("_", " ")} · <code>{receipt.eventId}</code></li>
              ))}</ul>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
