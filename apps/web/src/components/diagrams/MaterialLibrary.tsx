"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Image from "next/image";
import { Plus, Upload, X, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input, Textarea, Field, FIELD_CLS } from "@/components/ui/Input";
import { Modal, ConfirmModal } from "@/components/ui/Modal";
import { Pager } from "@/components/ui/Pager";
import { useAuthStore } from "@/lib/auth-store";
import { useUIStore } from "@/lib/store";
import { deleteMaterial, generateMaterial, getMaterial, listMaterials, materialTemplates, previewMaterial, saveMaterial,
  STATIC_PARAMETERIZATION, type MaterialParameterization, type DiagramMaterial, type MaterialScope, type MaterialSource, type MaterialTemplate } from "@/lib/api-diagram-materials";
import type { QuestionIllustrationData } from "@/lib/types";

const SUBJECT_LABELS: Record<string, [string, string]> = {
  general: ["综合", "General"], physics: ["物理", "Physics"], chemistry: ["化学", "Chemistry"],
  mathematics: ["数学", "Mathematics"], biology: ["生物", "Biology"], geography: ["地理", "Geography"],
  engineering: ["工程", "Engineering"], language: ["语言", "Language"], history: ["历史", "History"],
  economics: ["经济", "Economics"], music: ["音乐", "Music"], visual_art: ["美术", "Visual art"],
  sports: ["体育", "Sports"], astronomy: ["天文", "Astronomy"], agriculture: ["农业", "Agriculture"],
  environment: ["环境", "Environment"], statistics: ["统计", "Statistics"],
};

function Art({ image }: { image: QuestionIllustrationData }) {
  return <Image unoptimized src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(image.svg)}`}
    alt={image.alt} width={image.width} height={image.height} className="h-full w-full object-contain p-4" />;
}

export function MaterialLibrary({ scope }: { scope: MaterialScope }) {
  const user = useAuthStore(s => s.user);
  // Remount on account changes; no private records or draft carryover.
  return <OwnedLibrary key={`${user?.id ?? "guest"}:${scope}`} scope={scope} admin={user?.role === "admin"} />;
}

function OwnedLibrary({ scope, admin }: { scope: MaterialScope; admin: boolean }) {
  const en = useUIStore(s => s.lang) === "en";
  const text = (zh: string, eng: string) => en ? eng : zh;
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [data, setData] = useState<{ items: DiagramMaterial[]; total: number } | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [editor, setEditor] = useState<DiagramMaterial | "new" | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void listMaterials(scope, query, page, controller.signal).then(value => {
        if (!controller.signal.aborted) { setData(value); setError(""); }
      }).catch(() => { if (!controller.signal.aborted) setError(en ? "Could not load materials." : "素材加载失败。"); });
    }, 180);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [scope, query, page, refresh, en]);
  return <section className="space-y-4" data-testid="custom-material-library">
    <div className="flex items-center justify-between gap-4">
      <div><h2 className="text-lg font-medium text-fg">{scope === "public" ? text("新增公有素材", "Added public materials") : text("我的素材", "My materials")}</h2>
        <p className="mt-1 text-xs text-muted">{text("上传 SVG、按模板设计，或生成 AI 草稿后继续修改。保存并启用的素材可用于出题。", "Upload an SVG, design from a template, or edit an AI draft. Saved and enabled materials can be used in questions.")}</p></div>
      {(scope === "private" || admin) && <Button icon={<Plus size={15} />} onClick={() => setEditor("new")} data-testid="new-material">{text("新增素材", "New material")}</Button>}
    </div>
    <Input aria-label={text("搜索自建素材", "Search custom materials")} placeholder={text("搜索标题、说明或别名", "Search title, description or aliases")} className="max-w-xl" value={query} onChange={e => { setQuery(e.target.value); setPage(0); }} />
    {error ? <div role="alert" className="text-sm text-danger">{error} <Button variant="outline" onClick={() => setRefresh(v => v+1)}>{text("重试", "Retry")}</Button></div>
      : !data ? <p className="text-sm text-muted">{text("加载中…", "Loading…")}</p>
      : !data.total ? <div className="rounded-xl border border-dashed border-border p-7 text-sm text-muted">{text("还没有素材。选择模板、上传 SVG 或填写要求创建第一个素材。", "No materials yet. Start with a template, SVG upload or a description.")}</div>
      : <div className="grid grid-cols-2 gap-4 xl:grid-cols-3">
        {data.items.map(material => <button key={material.id} className="overflow-hidden rounded-xl border border-border bg-surface text-left hover:border-accent" onClick={() => setEditor(material)} data-testid="custom-material">
          <div className="h-44 bg-white"><Art image={material.illustration} /></div>
          <div className="p-4"><p className="truncate font-medium text-fg">{material.title}</p><p className="mt-1 text-xs text-muted">v{material.revision} · {material.enabled ? text("已启用", "Enabled") : text("未启用", "Disabled")} · {SUBJECT_LABELS[material.subject]?.[en ? 1 : 0] ?? material.subject}</p></div>
        </button>)}
      </div>}
    {data && <Pager page={page} total={data.total} per={12} onPage={setPage} />}
    {editor && <MaterialEditor material={editor === "new" ? null : editor} scope={scope} admin={admin}
      onClose={() => setEditor(null)} onSaved={() => { setEditor(null); setRefresh(v => v+1); }} />}
  </section>;
}

function parseSvg(svg: string): XMLDocument {
  const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
  if (doc.querySelector("parsererror") || doc.documentElement.localName !== "svg") throw new Error("material_svg_invalid");
  return doc;
}
const EDITABLE = "rect,circle,ellipse,line,text,path,polygon,polyline";
const ATTRIBUTES: Record<string, string[]> = {
  rect: ["x", "y", "width", "height", "rx"], circle: ["cx", "cy", "r"], ellipse: ["cx", "cy", "rx", "ry"],
  line: ["x1", "y1", "x2", "y2"], text: ["x", "y", "font-size"], path: ["d"], polygon: ["points"], polyline: ["points"],
};
function MaterialEditor({ material, scope: initialScope, admin, onClose, onSaved }: {
  material: DiagramMaterial | null; scope: MaterialScope; admin: boolean; onClose: () => void; onSaved: () => void;
}) {
  const en = useUIStore(s => s.lang) === "en";
  const text = (zh: string, eng: string) => en ? eng : zh;
  const [title, setTitle] = useState(material?.title ?? "");
  const [description, setDescription] = useState(material?.description ?? "");
  const [subject, setSubject] = useState(material?.subject ?? "general");
  const [aliases, setAliases] = useState(material?.aliases.join(", ") ?? "");
  const [guidanceNote, setGuidanceNote] = useState(material?.guidance_note ?? "");
  const [scope, setScope] = useState<MaterialScope>(material?.scope ?? (admin ? initialScope : "private"));
  const [enabled, setEnabled] = useState(material?.enabled ?? false);
  const [source, setSource] = useState<MaterialSource>(material?.source ?? "manual");
  const [svg, setSvg] = useState(material?.svg ?? '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400"><rect x="220" y="140" width="200" height="120" fill="none" stroke="#26364a" stroke-width="2"/></svg>');
  const [parameterJson, setParameterJson] = useState(JSON.stringify(material?.parameterization ?? STATIC_PARAMETERIZATION, null, 2));
  const [previewParams, setPreviewParams] = useState<Record<string, string | number>>({});
  const [validatedParameters, setValidatedParameters] = useState(parameterJson);
  const [validatedPreviewParams, setValidatedPreviewParams] = useState("{}");
  const [validated, setValidated] = useState(material?.svg ?? "");
  const [image, setImage] = useState(material?.illustration ?? null);
  const [templates, setTemplates] = useState<MaterialTemplate[]>([]);
  const [guide, setGuide] = useState<string[]>([]);
  const [requirement, setRequirement] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(material?.revision ?? 0);
  const [revisions, setRevisions] = useState<number[]>([]);
  const [selected, setSelected] = useState(0);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  const canEdit = !material || material.scope === "private" || admin;
  const conflict = error === "material_revision_conflict";
  useEffect(() => {
    const ctl = new AbortController(); controller.current = ctl;
    void materialTemplates(ctl.signal).then(result => { if (!ctl.signal.aborted) { setTemplates(result.templates); setGuide(result.guide); } }).catch(() => { if (!ctl.signal.aborted) setError("templates_unavailable"); });
    if (material) void getMaterial(material.id, undefined, ctl.signal).then(result => {
      if (!ctl.signal.aborted) { setRevisions(result.revisions ?? []); setRevision(result.latest_revision ?? result.revision); }
    }).catch(() => { if (!ctl.signal.aborted) setError("material_missing"); });
    return () => ctl.abort();
  }, [material]);
  const elements = useMemo(() => {
    try { return Array.from(parseSvg(svg).querySelectorAll(EDITABLE)).map(node => ({ tag: node.localName, attrs: Object.fromEntries(Array.from(node.attributes).map(a => [a.name, a.value])), content: node.textContent ?? "" })); }
    catch { return []; }
  }, [svg]);
  const element = elements[selected];
  const accept = useCallback((result: { svg: string; illustration: QuestionIllustrationData; parameterization?: MaterialParameterization }, values: Record<string, string | number> = {}) => {
    setSvg(result.svg); setValidated(result.svg); setImage(result.illustration);
    const source = JSON.stringify(result.parameterization ?? STATIC_PARAMETERIZATION, null, 2);
    setParameterJson(source); setValidatedParameters(source);
    setPreviewParams(values); setValidatedPreviewParams(JSON.stringify(values));
  }, []);
  function parameterization(): MaterialParameterization {
    try { return JSON.parse(parameterJson) as MaterialParameterization; }
    catch { throw new Error("material_parameterization_invalid"); }
  }
  const controls = useMemo(() => {
    try { return Object.entries((JSON.parse(parameterJson) as MaterialParameterization).parameters ?? {}); }
    catch { return []; }
  }, [parameterJson]);
  async function run(action: string, operation: () => Promise<void>) {
    if (busy) return;
    setBusy(action); setError("");
    try { await operation(); } catch (e) { if (!controller.current?.signal.aborted) setError(e instanceof Error ? e.message : "material_operation_failed"); }
    finally { if (!controller.current?.signal.aborted) setBusy(""); }
  }
  function modify(key: string, value: string) {
    try {
      const doc = parseSvg(svg); const node = doc.querySelectorAll(EDITABLE)[selected];
      if (!node) return;
      if (key === "text") node.textContent = value; else if (value === "") node.removeAttribute(key); else node.setAttribute(key, value);
      setSvg(new XMLSerializer().serializeToString(doc)); setSource("manual");
    } catch { setError("material_svg_invalid"); }
  }
  function add(tag: string) {
    try {
      const doc = parseSvg(svg); const node = doc.createElementNS("http://www.w3.org/2000/svg", tag);
      const attrs: Record<string, string> = tag === "rect" ? { x: "240", y: "140", width: "160", height: "100" }
        : tag === "circle" ? { cx: "320", cy: "200", r: "65" } : tag === "line" ? { x1: "200", y1: "200", x2: "440", y2: "200" }
        : { x: "320", y: "340", "font-size": "22", "text-anchor": "middle" };
      Object.entries({ ...attrs, fill: tag === "text" ? "#26364a" : "none", stroke: tag === "text" ? "none" : "#26364a", "stroke-width": "2" }).forEach(([k, v]) => node.setAttribute(k, v));
      if (tag === "text") node.textContent = en ? "Label" : "标签";
      doc.documentElement.appendChild(node); setSvg(new XMLSerializer().serializeToString(doc)); setSelected(elements.length); setSource("manual");
    } catch { setError("material_svg_invalid"); }
  }
  async function upload(file?: File) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".svg") || file.size > 131072) { setError("material_svg_file_invalid"); return; }
    await run("preview", async () => {
      const raw = await file.text();
      const result = await previewMaterial(raw, controller.current?.signal);
      if (!controller.current?.signal.aborted) { accept(result); setSource("upload"); if (!title) setTitle(file.name.replace(/\.svg$/i, "")); }
    });
  }
  async function loadVersion(number?: number) {
    if (!material) return;
    await run("history", async () => {
      const result = await getMaterial(material.id, number, controller.current?.signal);
      if (!controller.current?.signal.aborted) { accept(result); setTitle(result.title); setDescription(result.description); setSubject(result.subject); setAliases(result.aliases.join(", ")); setGuidanceNote(result.guidance_note ?? ""); setEnabled(result.enabled); setRevision(result.latest_revision ?? result.revision); setRevisions(result.revisions ?? []); }
    });
  }
  const errorText = conflict ? text("素材已被修改，请重新载入最新版本后编辑。", "This material changed. Reload the latest version before editing.")
    : error === "admin_required" ? text("只有管理员可以修改公有素材。", "Only administrators can edit public materials.")
    : error === "material_svg_file_invalid" ? text("请选择不超过 128 KiB 的 SVG 文件。", "Choose an SVG file up to 128 KiB.")
    : error ? text("操作未完成，请检查 SVG、坐标及输入后重试。", "Could not complete the operation. Check the SVG, coordinates and inputs, then retry.") : "";
  return <Modal open onClose={onClose} width={1180} title={<div className="flex justify-between items-center"><span>{material ? canEdit ? text("编辑素材", "Edit material") : text("查看素材", "View material") : text("新增素材", "New material")}</span><Button variant="ghost" aria-label={text("关闭", "Close")} icon={<X size={16} />} onClick={onClose} /></div>}
    footer={<><span role="status" className="mr-auto text-xs text-muted">{busy ? text("处理中…", "Working…") : svg !== validated || parameterJson !== validatedParameters || JSON.stringify(previewParams) !== validatedPreviewParams ? text("修改后尚未预览", "Changes need a preview") : text("预览已更新", "Preview updated")}</span>
      <Button variant="outline" disabled={!!busy} onClick={() => void run("preview", async () => { const result = await previewMaterial(svg, controller.current?.signal, parameterization(), previewParams); if (!controller.current?.signal.aborted) accept(result, previewParams); })}>{text("检查并预览", "Check and preview")}</Button>
      {canEdit && <Button demoWrite disabled={!!busy || !title.trim() || conflict} onClick={() => void run("save", async () => {
        await saveMaterial({ title, description, subject, guidance_note: guidanceNote, aliases: aliases.split(/[,，]/).map(v => v.trim()).filter(Boolean), scope, enabled, source, svg, parameterization: parameterization(), ...(material ? { base_revision: revision } : {}) }, material?.id, controller.current?.signal);
        if (!controller.current?.signal.aborted) onSaved();
      })}>{text("保存素材", "Save material")}</Button>}</>}>
    <div className="space-y-4" data-testid="material-editor">
      {errorText && <div role="alert" className="rounded-lg bg-danger/5 p-3 text-sm text-danger">{errorText} {conflict && <Button variant="outline" onClick={() => void loadVersion()}>{text("重新载入", "Reload latest")}</Button>}</div>}
      {material && <div className="flex justify-end"><select disabled={!!busy} aria-label={text("历史版本", "History")} className={`${FIELD_CLS} max-w-44`} value="" onChange={e => void loadVersion(Number(e.target.value))}><option value="">{text("查看历史版本", "View history")}</option>{revisions.map(v => <option key={v} value={v}>v{v}</option>)}</select></div>}
      <fieldset disabled={!!busy || !canEdit} className="grid grid-cols-4 gap-3">
        <Field label={text("素材标题", "Material title")}><Input aria-label={text("素材标题", "Material title")} value={title} maxLength={80} onChange={e => setTitle(e.target.value)} /></Field>
        <Field label={text("学科", "Subject")}><select aria-label={text("学科", "Subject")} className={FIELD_CLS} value={subject} onChange={e => setSubject(e.target.value)}>{Object.entries(SUBJECT_LABELS).map(([v, label]) => <option key={v} value={v}>{label[en ? 1 : 0]}</option>)}</select></Field>
        <Field label={text("范围", "Scope")}><select aria-label={text("范围", "Scope")} className={FIELD_CLS} value={scope} disabled={!!material} onChange={e => setScope(e.target.value as MaterialScope)}><option value="private">{text("我的素材", "My materials")}</option>{(admin || scope === "public") && <option value="public">{text("公有素材", "Public materials")}</option>}</select></Field>
        <label className="flex items-center gap-2 text-sm"><Input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />{text("用于出题", "Use in questions")}</label>
        <Field label={text("说明", "Description")} className="col-span-2"><Input value={description} maxLength={600} onChange={e => setDescription(e.target.value)} /></Field>
        <Field label={text("别名（逗号分隔）", "Aliases (comma separated)")} className="col-span-2"><Input value={aliases} onChange={e => setAliases(e.target.value)} /></Field>
        <Field label={text("素材使用说明（选中时供 AI 参考）", "Usage note (shown to AI when selected)")} className="col-span-4"><Textarea aria-label={text("素材使用说明", "Material usage note")} rows={2} maxLength={400} value={guidanceNote} onChange={e => setGuidanceNote(e.target.value)} placeholder={text("简要描述适用情景、图中标签和需保留的结构。", "Describe suitable scenarios, labels and structures to preserve.")} /></Field>
      </fieldset>
      <fieldset disabled={!!busy || !canEdit} className="space-y-3 rounded-xl border border-border bg-bg p-4">
        <div className="flex flex-wrap items-center gap-2"><select aria-label={text("设计模板", "Design template")} className={`${FIELD_CLS} max-w-64`} value="" onChange={e => {
          const template = templates.find(v => v.id === e.target.value); if (template) { setSvg(template.svg); setParameterJson(JSON.stringify(template.parameterization, null, 2)); setPreviewParams({}); setSubject(template.subject); setSource("manual"); setSelected(0); }
        }}><option value="">{text("选择设计模板和样例", "Choose a template or example")}</option>{templates.map(v => <option key={v.id} value={v.id}>{v.title}</option>)}</select>
          <Button variant="outline" icon={<Upload size={14} />} onClick={() => fileInput.current?.click()}>{text("上传 SVG", "Upload SVG")}</Button>
          <input ref={fileInput} type="file" accept=".svg,image/svg+xml" className="hidden" aria-label={text("SVG 文件", "SVG file")} onChange={e => { void upload(e.target.files?.[0]); e.target.value = ""; }} />
        </div>
        <div className="flex items-start gap-3"><Textarea aria-label={text("AI 设计要求", "AI design request")} rows={2} maxLength={2400} value={requirement} onChange={e => setRequirement(e.target.value)} placeholder={text("描述对象、位置、文字和科学关系，例如：两步流程，左边光照，右边光合作用…", "Describe objects, positions, labels and relations…")} className="flex-1" />
          <Button demoWrite disabled={requirement.trim().length < 3} icon={<Sparkles size={15} />} onClick={() => void run("generate", async () => {
            // Current edits must pass the same sanitizer before model input.
            const result = await generateMaterial(requirement, svg, controller.current?.signal, parameterization());
            if (!controller.current?.signal.aborted) { accept(result); setSource("llm"); }
          })}>{text("生成 / 修改草稿", "Generate / revise draft")}</Button></div>
        <p className="text-xs text-muted">{text("AI 草稿可继续编辑，保存前请检查科学含义。生成不会自动发布或启用。", "AI drafts remain editable. Check their meaning before saving. Generation does not publish or enable them.")}</p>
      </fieldset>
      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-4">
        <div className="min-w-0 space-y-3"><div className="h-[330px] rounded-xl border border-border bg-white" data-testid="material-preview" onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); if (canEdit && !busy) void upload(e.dataTransfer.files[0]); }}>
          {image ? <Art image={image} /> : <p className="p-8 text-sm text-slate-500">{text("检查后显示安全预览；也可拖入 SVG 文件。", "Check the draft to preview it, or drop an SVG file here.")}</p>}</div>
          <p className="text-xs text-muted">{svg !== validated || parameterJson !== validatedParameters || JSON.stringify(previewParams) !== validatedPreviewParams ? text("当前显示上一次校验预览。点击“检查并预览”查看修改。", "Showing the last checked preview. Check again to see edits.") : text("白色画布与实际题图一致。", "The white canvas matches question rendering.")}</p>
          <details className="text-xs leading-6 text-muted"><summary className="cursor-pointer">{text("设计说明与样例", "Design guide and examples")}</summary><ul className="list-disc pl-5">{guide.map(v => <li key={v}>{v}</li>)}</ul><p>{templates.find(v => v.subject === subject)?.description}</p></details>
        </div>
        <fieldset disabled={!!busy || !canEdit} className="min-w-0 space-y-3">
          <div className="flex flex-wrap gap-2">{[["rect", "矩形", "Rectangle"], ["circle", "圆", "Circle"], ["line", "直线", "Line"], ["text", "文字", "Text"]].map(([tag, zh, eng]) => <Button key={tag} size="sm" variant="outline" onClick={() => add(tag)}>{text(zh, eng)}</Button>)}</div>
          <Field label={text("选中图元", "Select element")}><select aria-label={text("选中图元", "Select element")} className={FIELD_CLS} value={selected} onChange={e => setSelected(Number(e.target.value))}>{elements.map((v, i) => <option key={i} value={i}>{i+1}. {v.tag} {v.attrs.id ?? v.content.slice(0, 20)}</option>)}</select></Field>
          {element && <div className="grid grid-cols-3 gap-2">{[...ATTRIBUTES[element.tag], "fill", "stroke", ...(element.tag === "text" ? ["text"] : [])].map(key => <Field label={key} key={key}><Input aria-label={`element ${key}`} value={key === "text" ? element.content : element.attrs[key] ?? ""} onChange={e => modify(key, e.target.value)} /></Field>)}</div>}
          <details open={controls.length > 0} className="space-y-3 text-sm" data-testid="material-parameters">
            <summary className="cursor-pointer text-fg">{text("可调参数与文字", "Adjustable parameters and text")}</summary>
            <p className="text-xs text-muted">{text("模板会提供参数规范。修改预览值后检查效果；保存的是规范与默认值，出题时按题面条件调整。", "Templates include controls. Check preview values; questions supply their own facts. Saving preserves the specification and defaults.")}</p>
            <div className="grid grid-cols-2 gap-2">{controls.map(([key, spec]) => <Field key={key} label={spec.description || key}>
              <Input aria-label={`preview parameter ${key}`} type={spec.type === "number" || spec.type === "integer" ? "number" : "text"}
                min={spec.minimum} max={spec.maximum} step={spec.type === "integer" ? 1 : "any"}
                value={previewParams[key] ?? spec.default} onChange={e => setPreviewParams(v => ({ ...v,
                  [key]: spec.type === "number" || spec.type === "integer" ? Number(e.target.value) : e.target.value }))} />
            </Field>)}</div>
            <Field label={text("参数规范 JSON", "Parameter specification JSON")}><Textarea aria-label={text("参数规范 JSON", "Parameter specification JSON")}
              rows={8} spellCheck={false} className="font-mono text-xs" value={parameterJson} maxLength={20000}
              onChange={e => { setParameterJson(e.target.value); setPreviewParams({}); setSource("manual"); }} /></Field>
          </details>
          <Field label={text("SVG 源码", "SVG source")}><Textarea aria-label={text("SVG 源码", "SVG source")} className="font-mono text-xs" rows={9} spellCheck={false} value={svg} maxLength={131072} onChange={e => { setSvg(e.target.value); setSource("manual"); }} /></Field>
        </fieldset>
      </div>
      {material && canEdit && <Button variant="danger" disabled={!!busy} onClick={() => setConfirmDelete(true)}>{text("删除素材", "Delete material")}</Button>}
      <ConfirmModal open={confirmDelete} onClose={() => setConfirmDelete(false)} title={text("删除素材", "Delete material")} desc={text("删除后不再用于新题。已冻结的题图仍保留。", "It will no longer be used for new questions. Frozen question images remain available.")} confirmText={text("删除", "Delete")} cancelText={text("取消", "Cancel")} onConfirm={() => { setConfirmDelete(false); if (material) void run("delete", async () => { await deleteMaterial(material.id, revision, controller.current?.signal); if (!controller.current?.signal.aborted) onSaved(); }); }} />
    </div>
  </Modal>;
}
