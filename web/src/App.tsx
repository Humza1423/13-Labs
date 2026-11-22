// C0 placeholder. U1 (feat/probation-ui) owns and replaces this file.
import { previewFixture } from "./fixtures";

export default function App() {
  const { run, events } = previewFixture;
  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: 32 }}>
      <h1>13 Labs · The AI Probation Shift</h1>
      <p>Design preview · {run.cases.length} case legs · {events.length} events</p>
    </main>
  );
}
