"use client";

// 候选选择卡（A06 视觉）。
import { useAssistantStore } from "@/lib/assistant/store";
import { stringsFor } from "../strings";
import type { ChoiceOption } from "@next-tutor/contracts/assistant";

export function ChoiceCard({
  blockId, prompt, options,
}: { blockId: string; prompt: string; options: ChoiceOption[] }) {
  const lang = useAssistantStore((s) => s.lang);
  const send = useAssistantStore((s) => s.send);
  const t = stringsFor(lang);
  return (
    <div className="assistant-card assistant-choice-card">
      <div className="assistant-card-title">{prompt || t.choicesPrompt}</div>
      <div className="assistant-choice-list">
        {options.map((option) => (
          <button
            key={option.option_id}
            type="button"
            className="assistant-choice-btn"
            onClick={() => void send(option.label, {
              block_id: blockId,
              option_id: option.option_id,
            })}
            data-block={blockId}
            data-option={option.option_id}
          >
            <span className="assistant-choice-label">{option.label}</span>
            {option.description && (
              <span className="assistant-choice-desc">{option.description}</span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
