"use client";
import { useEffect, useState } from "react";
import { Check, Sparkles } from "lucide-react";
import type { InlineSpan, SlideBlock, SlideSpec } from "@/lib/types-classroom.generated";
import { useUIStore } from "@/lib/store";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Input";

export function BlockEditor({ slide, busy, onSave, onOptimize, onSelect, onDirtyChange }: {
  slide: SlideSpec; busy: boolean;
  onSave: (block: SlideBlock) => void;
  onOptimize: (blockId: string, instruction: string) => void;
  onSelect: (blockId: string) => void;
  /** 未保存修改信号（助手导航保护 §5.4）；切换组件/页面时复位。 */
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const { lang } = useUIStore();
  const en = lang === "en";
  const blocks = slide.blocks.filter((block) => block.kind !== "checkpoint");
  const [selected, setSelected] = useState(blocks[0]?.id ?? "");
  const block = blocks.find((item) => item.id === selected) ?? blocks[0];
  const names: Record<string, string> = en ? {
    paragraph: "Text", bullets: "Key points", callout: "Callout", formula: "Formula",
    code: "Code", steps: "Steps", table: "Table", image: "Image", diagram: "Diagram",
  } : { code: "代码", paragraph: "文字", bullets: "要点", callout: "提示", formula: "公式", steps: "步骤", table: "表格", image: "图片说明", diagram: "图示" };
  return <div className="space-y-5">
    <div><h2 className="text-sm font-semibold text-fg">{en ? "Edit a component" : "编辑课件组件"}</h2><p className="mt-2 text-xs leading-6 text-muted">{en ? "Select a component to edit its text or ask AI to refine it." : "选择一个组件，直接修改文字，或让 AI 定向优化。"}</p></div>
    <div className="flex flex-wrap gap-2" aria-label={en ? "Components" : "组件列表"}>
      {blocks.map((item, index) => <button key={item.id} disabled={busy} aria-pressed={item.id === block?.id} onClick={() => { setSelected(item.id); onSelect(item.id); }} className={`rounded-lg border px-3 py-2 text-xs transition-colors disabled:opacity-50 ${item.id === block?.id ? "border-accent/40 bg-accent-soft text-accent-strong" : "border-border text-muted hover:bg-surface-hover"}`}>{index + 1} · {names[item.kind ?? "paragraph"]}</button>)}
    </div>
    {block ? <BlockForm key={block.id} block={block} busy={busy} onSave={onSave} onOptimize={onOptimize} onDirtyChange={onDirtyChange} /> : <p className="text-xs text-muted">{en ? "This page contains a protected checkpoint." : "本页为随堂题，请在讲稿或页面设置中调整。"}</p>}
  </div>;
}

function BlockForm({ block, busy, onSave, onOptimize, onDirtyChange }: {
  block: SlideBlock; busy: boolean; onSave: (block: SlideBlock) => void;
  onOptimize: (id: string, instruction: string) => void;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const { lang } = useUIStore();
  const en = lang === "en";
  const [draft, setDraft] = useState<SlideBlock>(() => structuredClone(block));
  const [instruction, setInstruction] = useState("");
  const dirty = JSON.stringify(draft) !== JSON.stringify(block);
  // 未保存信号上抛；组件卸载（切换组件/翻页/保存后重载）时复位。
  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);
  const fields: { path: (string | number)[]; label: string; value: string; limit: number }[] = [];
  const add = (path: (string | number)[], label: string, value: string, limit = 600) => fields.push({ path, label, value, limit });
  const spans = (items: InlineSpan[], path: (string | number)[], label: string) => items.forEach((span, index) => {
    if (span.kind === "math") {
      add([...path, index, "latex"], `${label} · LaTeX`, span.latex, 2000);
      add([...path, index, "spoken"], `${label} · ${en ? "Reading" : "公式读法"}`, span.spoken, 240);
    } else if ("text" in span) add([...path, index, "text"], `${label} ${index + 1}`, span.text);
  });
  if (draft.kind === "paragraph" || draft.kind === "callout") spans(draft.spans, ["spans"], en ? "Text" : "文字");
  if (draft.kind === "bullets") draft.items.forEach((item, i) => spans(item, ["items", i], `${en ? "Point" : "要点"} ${i + 1} ·`));
  if (draft.kind === "steps") draft.steps.forEach((step, i) => {
    add(["steps", i, "label"], `${en ? "Step" : "步骤"} ${i + 1}`, step.label, 40);
    spans(step.spans, ["steps", i, "spans"], en ? "Explanation" : "说明");
  });
  if (draft.kind === "formula") {
    add(["latex"], "LaTeX", draft.latex, 2000);
    add(["spoken"], en ? "Reading" : "公式读法", draft.spoken, 240);
    if (draft.label != null) add(["label"], en ? "Label" : "标签", draft.label, 40);
  }
  if (draft.kind === "code") {
    add(["code"], en ? "Source code" : "代码", draft.code, 12000);
    add(["language"], en ? "Language" : "语言", draft.language ?? "text", 30);
  }
  if (draft.kind === "image") {
    add(["caption"], en ? "Caption" : "图片说明", draft.caption, 300);
    add(["alt"], en ? "Description" : "图片描述", draft.alt, 500);
  }
  if (draft.kind === "table") {
    draft.headers.forEach((value, i) => add(["headers", i], `${en ? "Column" : "表头"} ${i + 1}`, value, 60));
    draft.rows.forEach((row, i) => row.forEach((value, j) => add(["rows", i, j], `${en ? "Cell" : "单元格"} ${i + 1} · ${j + 1}`, value, 120)));
  }
  if (draft.kind === "diagram") {
    add(["diagram", "alt"], en ? "Description" : "图示说明", draft.diagram.alt, 500);
    if (draft.diagram.type === "flow") draft.diagram.nodes.forEach((node, i) => add(["diagram", "nodes", i, "label"], `${en ? "Node" : "节点"} ${i + 1}`, node.label, 40));
    if (draft.diagram.type === "cartesian_plot") {
      add(["diagram", "x_label"], en ? "X axis" : "横轴", draft.diagram.x_label, 40);
      add(["diagram", "y_label"], en ? "Y axis" : "纵轴", draft.diagram.y_label, 40);
    }
  }
  const update = (path: (string | number)[], value: string) => {
    const copy = structuredClone(draft);
    let target = copy as unknown as Record<string | number, unknown>;
    for (const key of path.slice(0, -1)) target = target[key] as Record<string | number, unknown>;
    target[path[path.length - 1]] = value;
    setDraft(copy);
  };
  return <>
    <div className="space-y-3">
      {fields.map((field) => <Field key={field.path.join(".")} label={field.label}>
        <Textarea aria-label={field.label} rows={field.limit > 100 ? 3 : 1} value={field.value} maxLength={field.limit} disabled={busy} onChange={(e) => update(field.path, e.target.value)} className="text-xs leading-6" />
      </Field>)}
      <Button size="sm" icon={<Check size={13} />} disabled={busy || !dirty || fields.some((f) => !f.value.trim())} onClick={() => onSave(draft)}>{en ? "Save component" : "保存组件"}</Button>
    </div>
    <div className="space-y-3 border-t border-border pt-5">
      <h3 className="flex items-center gap-2 text-xs font-semibold text-fg"><Sparkles size={14} className="text-accent-strong" />{en ? "Refine with AI" : "AI 优化此组件"}</h3>
      <div className="flex flex-wrap gap-2">{(en ? ["Make it clearer", "Make it shorter", "Use an intuitive example"] : ["解释更清楚", "精简文字", "换个直观例子"]).map((text) => <button key={text} disabled={busy || dirty} onClick={() => setInstruction(text)} className="rounded-full border border-border px-2.5 py-1.5 text-[11px] text-muted hover:text-accent-strong disabled:opacity-40">{text}</button>)}</div>
      <Input aria-label={en ? "Optimization request" : "优化要求"} value={instruction} maxLength={500} disabled={busy || dirty} onChange={(e) => setInstruction(e.target.value)} placeholder={en ? "Describe your changes…" : "例如：用生活中的例子解释这个概念"} className="text-xs" />
      <Button demoWrite size="sm" variant="outline" icon={<Sparkles size={13} />} disabled={busy || dirty || !instruction.trim()} onClick={() => onOptimize(block.id, instruction.trim())}>{en ? "Optimize component" : "优化当前组件"}</Button>
      <p className="text-[11px] leading-5 text-muted">{dirty ? (en ? "Save your text changes before using AI." : "请先保存文字修改，再使用 AI 优化。") : (en ? "Only this component and related context are sent to AI. Edit narration separately in Script." : "仅处理所选组件和必要上下文；讲稿可在「讲稿」中单独修改。")}</p>
    </div>
  </>;
}
