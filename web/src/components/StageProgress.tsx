import { Check } from "lucide-react";
import type { Stage } from "../contract";
import { stages } from "./runLabels";

export function StageProgress({ stage }: { stage?: Stage }) {
  const activeIndex = stages.findIndex(item => item.id === stage);
  return (
    <ol className="stage-track" aria-label="Shift stages">
      {stages.map((item, index) => (
        <li key={item.id} aria-current={stage === item.id ? "step" : undefined} data-passed={index < activeIndex}>
          <span className="step-index">{index < activeIndex ? <Check size={15} /> : index + 1}</span><span>{item.name}</span>
        </li>
      ))}
    </ol>
  );
}
