"use client";

import { useState } from "react";
import { X } from "lucide-react";

export interface ConceptOption {
  /** 图谱概念 id；自由文本概念为 ""。 */
  id: string;
  name: string;
}

type Tr = (key: string, fallback?: string) => string;

const keyOf = (o: ConceptOption) => o.id || o.name;

/** 概念多选器：计划概念下拉 + 自由文本回车，chips 展示已选并可移除。
 * 供「添加里程碑」「添加一周」复用。 */
export function ConceptMultiPick({
  options,
  selected,
  onChange,
  tr,
}: {
  options: ConceptOption[];
  selected: ConceptOption[];
  onChange: (next: ConceptOption[]) => void;
  tr: Tr;
}) {
  const [free, setFree] = useState("");
  const has = (o: ConceptOption) =>
    selected.some((s) => keyOf(s) === keyOf(o));
  const add = (o: ConceptOption) => {
    if (keyOf(o) && !has(o)) onChange([...selected, o]);
  };
  const addFree = () => {
    const name = free.trim();
    if (name) add({ id: "", name });
    setFree("");
  };
  return (
    <div>
      {selected.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {selected.map((o) => (
            <span
              key={keyOf(o)}
              className="inline-flex items-center gap-1 rounded-[7px] border border-border-light bg-bg px-2 py-0.5 text-[0.7rem] text-fg-secondary"
            >
              {o.name}
              <button
                type="button"
                onClick={() => onChange(selected.filter((s) => keyOf(s) !== keyOf(o)))}
                className="cursor-pointer text-muted transition-colors hover:text-danger"
              >
                <X size={10} />
              </button>
            </span>
          ))}
        </div>
      )}
      {options.length > 0 && (
        <select
          value=""
          onChange={(e) => {
            const o = options.find((x) => keyOf(x) === e.target.value);
            if (o) add(o);
          }}
          className="mb-2 h-8 w-full cursor-pointer rounded-[8px] border border-border bg-surface px-2 text-xs text-fg-secondary outline-none focus:border-accent"
        >
          <option value="" disabled>
            {tr("concept.pick")}
          </option>
          {options.map((o) => (
            <option key={keyOf(o)} value={keyOf(o)} disabled={has(o)}>
              {o.name}
            </option>
          ))}
        </select>
      )}
      <input
        value={free}
        onChange={(e) => setFree(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            addFree();
          }
        }}
        placeholder={tr("concept.custom.ph")}
        className="h-8 w-full rounded-[8px] border border-border bg-surface px-2 text-xs text-fg outline-none placeholder:text-muted focus:border-accent"
      />
    </div>
  );
}
