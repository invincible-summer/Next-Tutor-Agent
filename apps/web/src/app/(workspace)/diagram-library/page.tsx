"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Image from "next/image";
import { Search, X, ZoomIn, ZoomOut } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { EmptyState, ErrorNote, Skeleton } from "@/components/ui/EmptyState";
import { Field, Input, Textarea, FIELD_CLS } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { Pager } from "@/components/ui/Pager";
import { useAuthStore } from "@/lib/auth-store";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { getDiagramAsset, getDiagramTaxonomy, listDiagramAssets, previewDiagramAsset, type AssetDetail, type DiagramAsset, type DiagramTaxonomy } from "@/lib/api-diagrams";
import { getMaterial, listMaterialCatalog, type DiagramMaterial, type UnifiedMaterialCard, type UnifiedMaterialPage } from "@/lib/api-diagram-materials";
import type { QuestionIllustrationData } from "@/lib/types";
import { MaterialEditor, MaterialLibrary } from "@/components/diagrams/MaterialLibrary";
import { MaterialAtlasMark } from "@/components/pages/tools/ToolMarks";
import { STRINGS } from "./strings";

const PER = 12;

function Art({ illustration, large = false }: { illustration: QuestionIllustrationData; large?: boolean }) {
  return <Image unoptimized src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(illustration.svg)}`} alt={illustration.alt} width={illustration.width} height={illustration.height} className={`h-full w-full object-contain ${large ? "p-5" : "p-4"}`} />;
}

type Selected = { kind: "builtin"; asset: DiagramAsset } | { kind: "public"; material: DiagramMaterial };

export default function DiagramLibraryPage() {
  const lang = useUIStore(s => s.lang);
  const user = useAuthStore(s => s.user);
  const tr = useMemo(() => makePageT(lang, STRINGS), [lang]);
  const [scope, setScope] = useState<"public" | "private">("public");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [family, setFamily] = useState("");
  const [level, setLevel] = useState("");
  const [kind, setKind] = useState("");
  const [taxonomy, setTaxonomy] = useState<DiagramTaxonomy | null>(null);
  const [data, setData] = useState<UnifiedMaterialPage | null>(null);
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState(false);
  const [taxonomyError, setTaxonomyError] = useState(false);
  const [retry, setRetry] = useState(0);
  const [selected, setSelected] = useState<Selected | null>(null);
  const [opening, setOpening] = useState(false);
  const label = useCallback((key: string) => {
    const item = taxonomy && [...taxonomy.subjects, ...taxonomy.families, ...taxonomy.education_levels, ...taxonomy.asset_kinds].find(row => row.id === key);
    return item ? item[lang === "en" ? "en" : "zh"] : tr(key);
  }, [taxonomy, lang, tr]);

  useEffect(() => {
    const controller = new AbortController();
    void getDiagramTaxonomy(controller.signal).then(value => {
      if (!controller.signal.aborted) { setTaxonomy(value); setTaxonomyError(false); }
    }).catch(() => { if (!controller.signal.aborted) setTaxonomyError(true); });
    return () => controller.abort();
  }, [retry]);

  useEffect(() => {
    if (scope !== "public") return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setBusy(true); setError(false);
      const params = { subject: category, family, education_level: level, asset_kind: kind };
      // Keep the legacy read warm for existing deployments while the unified
      // catalogue rolls out; it is never used to render a second public lane.
      void listDiagramAssets(new URLSearchParams({ q: query, ...params, page: String(page), per: String(PER) }), controller.signal).catch(() => undefined);
      void listMaterialCatalog(query, page, controller.signal, params)
        .then(value => { if (!controller.signal.aborted) setData(value); })
        .catch(() => { if (!controller.signal.aborted) setError(true); })
        .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    }, query ? 220 : 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [scope, query, category, family, level, kind, page, retry]);

  const close = useCallback(() => setSelected(null), []);
  async function openItem(row: UnifiedMaterialCard) {
    setOpening(true);
    try {
      if (row.source === "builtin") setSelected({ kind: "builtin", asset: await getDiagramAsset(row.id) });
      else setSelected({ kind: "public", material: await getMaterial(row.id) });
    } catch { setError(true); }
    finally { setOpening(false); }
  }

  return <div className="h-full overflow-y-auto overscroll-contain bg-bg p-5 page-in sm:p-6" data-testid="diagram-library">
    <div className="mx-auto flex w-full max-w-[1240px] flex-col gap-5">
      <header className="flex flex-wrap items-start justify-between gap-4"><div className="flex items-start gap-3"><span className="flex h-11 w-11 items-center justify-center rounded-[12px] bg-accent-soft text-accent-strong"><MaterialAtlasMark className="h-7 w-7" /></span><div><p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted">{tr("eyebrow")}</p><h1 className="mt-1 font-serif text-2xl font-semibold tracking-tight text-fg">{tr("title")}</h1><p className="mt-1 max-w-2xl text-sm leading-6 text-fg-secondary">{tr("intro")}</p></div></div>{data && <span className="rounded-full border border-border bg-surface px-3 py-1.5 text-xs text-muted">{data.total} {tr("assets")}</span>}</header>
      <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex gap-2" role="group" aria-label={lang === "en" ? "Material scope" : "素材范围"}><Button variant={scope === "public" ? "primary" : "outline"} onClick={() => { setScope("public"); setPage(0); }}>{lang === "en" ? "Public materials" : "公有素材"}</Button><Button variant={scope === "private" ? "primary" : "outline"} onClick={() => { setScope("private"); }}>{lang === "en" ? "My materials" : "我的素材"}</Button></div><p className="text-xs text-muted">{scope === "public" ? (lang === "en" ? "Built-in and approved public art in one catalogue" : "内置素材与审核通过的公有素材共用一个目录") : (lang === "en" ? "Private SVG drafts" : "个人 SVG 草稿")}</p></div>
      {scope === "private" ? <section className="rounded-[14px] border border-border bg-surface p-4 sm:p-5"><div className="mb-4 flex items-center justify-between gap-3"><div><h2 className="text-sm font-semibold text-fg">{lang === "en" ? "My materials" : "我的素材"}</h2><p className="mt-1 text-xs text-muted">{lang === "en" ? "Search, edit and version your reusable SVGs." : "搜索、编辑并管理可复用的 SVG 素材。"}</p></div></div><MaterialLibrary scope="private" /></section> : <>
        <section aria-label={tr("filters")} className="space-y-3 rounded-[14px] border border-border bg-surface p-4"><div className="flex flex-wrap items-end gap-3"><div className="relative min-w-[220px] flex-1"><Search size={16} className="pointer-events-none absolute left-3 top-3 text-muted" /><Input aria-label={tr("searchLabel")} placeholder={tr("search")} value={query} className="pl-10" onChange={e => { setQuery(e.target.value); setPage(0); }} /></div><Field label={tr("subject")}><select aria-label={tr("subject")} value={category} className={FIELD_CLS} onChange={e => { setCategory(e.target.value); setFamily(""); setPage(0); }}><option value="">{tr("all")}</option>{taxonomy?.subject_groups.map(group => <optgroup key={group.id} label={group[lang === "en" ? "en" : "zh"]}>{group.subjects.map(key => <option key={key} value={key}>{label(key)}</option>)}</optgroup>)}</select></Field><Field label={tr("educationLevel")}><select aria-label={tr("educationLevel")} value={level} className={FIELD_CLS} onChange={e => { setLevel(e.target.value); setPage(0); }}><option value="">{tr("allLevels")}</option>{taxonomy?.education_levels.map(row => <option key={row.id} value={row.id}>{label(row.id)}</option>)}</select></Field><Field label={tr("assetKind")}><select aria-label={tr("assetKind")} value={kind} className={FIELD_CLS} onChange={e => { setKind(e.target.value); setPage(0); }}><option value="">{tr("allKinds")}</option>{taxonomy?.asset_kinds.map(row => <option key={row.id} value={row.id}>{label(row.id)}</option>)}</select></Field><Field label={tr("family")}><select aria-label={tr("family")} value={family} className={FIELD_CLS} onChange={e => { setFamily(e.target.value); setPage(0); }}><option value="">{tr("allFamilies")}</option>{taxonomy?.families.map(row => <option key={row.id} value={row.id}>{label(row.id)}</option>)}<option value="custom">{lang === "en" ? "Custom" : "自定义"}</option></select></Field></div><div className="flex items-center justify-between gap-3"><p className="text-xs text-muted">{lang === "en" ? "Search title, alias or description" : "可搜索名称、别名和说明"}</p><span className="text-xs text-muted">{data?.source_counts.builtin ?? "—"} {lang === "en" ? "built-in" : "内置"} · {data?.source_counts.public ?? "—"} {lang === "en" ? "public" : "公有"}</span></div></section>
        <section aria-label={tr("selection")} aria-busy={busy}>{error || taxonomyError ? <ErrorNote message={tr("failed")} retry={() => setRetry(v => v + 1)} /> : !data ? <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4"><Skeleton className="h-56" /><Skeleton className="h-56" /><Skeleton className="h-56" /></div> : !data.items.length ? <EmptyState title={tr("empty")} /> : <div className={`grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 ${busy || opening ? "opacity-60" : ""}`}>{data.items.map(asset => <button key={`${asset.source}:${asset.id}`} data-testid={asset.source === "public" ? "custom-material" : "diagram-asset"} data-asset-id={asset.source === "public" ? `material.${asset.id}` : asset.id} onClick={() => void openItem(asset)} className="group cursor-pointer overflow-hidden rounded-[14px] border border-border bg-surface text-left shadow-sm transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:border-accent/50 hover:shadow-md focus-visible:outline-2 focus-visible:outline-accent motion-reduce:transform-none"><div className="h-48 border-b border-border-light bg-surface-sunken"><Art illustration={asset.illustration} /></div><div className="px-4 py-3"><div className="mb-1.5 flex items-center justify-between gap-2"><span className="rounded-full bg-bg px-2 py-1 text-[10px] tracking-wide text-muted">{asset.source === "builtin" ? (lang === "en" ? "Built-in" : "内置") : (lang === "en" ? "Public" : "公有")} · v{asset.version}</span><MaterialAtlasMark className="h-4 w-4 text-muted transition-colors group-hover:text-accent-strong" /></div><h2 className="truncate text-sm font-medium text-fg">{lang === "en" ? asset.english ?? asset.title : asset.title}</h2><p className="mt-1 truncate text-[11px] text-muted">{asset.description || label(asset.category)}{asset.subject ? ` · ${label(asset.subject)}` : ""}</p></div></button>)}</div>}{data && data.total > PER && <Pager className="mt-6" page={page} total={data.total} per={PER} onPage={setPage} />}</section><p className="border-t border-border-light pt-5 text-[11px] text-muted">{tr("original")}</p>
      </>}
    </div>
    {selected?.kind === "builtin" && <AssetExplorer key={selected.asset.id} asset={selected.asset} onClose={close} label={label} />}
    {selected?.kind === "public" && <MaterialEditor key={selected.material.id} material={selected.material} scope="public" admin={user?.role === "admin"} onClose={close} onSaved={() => { setSelected(null); setRetry(v => v + 1); }} />}
  </div>;
}

function AssetExplorer({ asset, onClose, label }: { asset: DiagramAsset; onClose: () => void; label: (key: string) => string }) {
  const lang = useUIStore(s => s.lang);
  const tr = useMemo(() => makePageT(lang, STRINGS), [lang]);
  const [detail, setDetail] = useState<AssetDetail | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [illustration, setIllustration] = useState(asset.illustration);
  const [profile, setProfile] = useState("textbook");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState(1);
  const reset = useCallback((data: AssetDetail) => {
    const defaults: Record<string, string> = {};
    for (const [key, spec] of Object.entries(data.parameters)) {
      const value = data.sample_params[key] ?? spec.default;
      defaults[key] = value === undefined ? "" : spec.type === "list" ? JSON.stringify(value) : String(value);
    }
    setValues(defaults); setIllustration(data.illustration); setProfile("textbook"); setError("");
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void getDiagramAsset(asset.id, controller.signal).then(data => {
      if (!controller.signal.aborted) { setDetail(data); reset(data); }
    }).catch(() => { if (!controller.signal.aborted) setError(tr("failed")); });
    return () => controller.abort();
  }, [asset.id, reset, tr]);
  async function apply() {
    if (!detail || busy) return;
    setBusy(true); setError("");
    try {
      const params: Record<string, unknown> = {};
      for (const [key, spec] of Object.entries(detail.parameters)) {
        const raw = values[key];
        if (raw === "" || raw === undefined) continue;
        params[key] = spec.type === "list" ? JSON.parse(raw) : spec.type === "number" || spec.type === "integer" ? Number(raw) : spec.type === "boolean" ? raw === "true" : raw;
      }
      setIllustration((await previewDiagramAsset(asset.id, params, profile)).illustration);
    } catch { setError(tr("invalid")); }
    finally { setBusy(false); }
  }
  return <Modal open onClose={onClose} width={1080} title={<div className="flex items-center justify-between gap-4"><span>{lang === "en" ? asset.english : asset.title}</span>
    <Button variant="ghost" size="sm" aria-label={tr("close")} icon={<X size={16} />} onClick={onClose} /></div>}>
    <div className="grid grid-cols-1 gap-6 sm:grid-cols-[minmax(0,1fr)_280px]">
      <div className="min-w-0">
        <div className="mb-3 flex items-center justify-between gap-3"><span className="text-xs text-muted">{label(asset.category)} · {label(asset.asset_kind)}</span>
          <div className="flex items-center gap-2"><Button type="button" variant="ghost" size="sm" aria-label={tr("zoomOut")} disabled={zoom <= 1} onClick={() => setZoom(v => Math.max(1, v - .5))} icon={<ZoomOut size={15} />} />
            <span className="w-10 text-center text-xs tabular-nums text-muted">{zoom * 100}%</span>
            <Button type="button" variant="ghost" size="sm" aria-label={tr("zoomIn")} disabled={zoom >= 3} onClick={() => setZoom(v => Math.min(3, v + .5))} icon={<ZoomIn size={15} />} /></div></div>
        <div className="h-[440px] overflow-auto rounded-xl border border-border bg-white" data-testid="asset-large-preview">
          <div style={{ width: `${zoom * 100}%`, height: `${zoom * 100}%` }}><Art illustration={illustration} large /></div>
        </div>
        <p className="mt-4 text-xs leading-6 text-muted">{asset.education_levels.map(label).join(" · ")}</p>
        {!!Object.keys(detail?.sample_params ?? {}).length && <p className="mt-1 text-xs leading-6 text-muted">{tr("previewSample")}</p>}
        <p className="mt-1 text-xs leading-6 text-muted">{asset.aliases.join(" · ")}</p>
        {detail?.v2 && <div className="mt-3 space-y-1 text-xs leading-6 text-muted" data-testid="asset-v2-capabilities">
          <p>{lang === "en" ? "Supports question composition" : "可用于题目关系构图"} · {detail.v2.capabilities.map(capability => ({
            liquid_fill: lang === "en" ? "Liquid level" : "液面", supports_submersion: lang === "en" ? "Submersion" : "浸没",
            open_top: lang === "en" ? "Open vessel" : "敞口", reading_binding: lang === "en" ? "Bound reading" : "读数绑定",
            readable_scale: lang === "en" ? "Scale" : "刻度", wire_terminal: lang === "en" ? "Wire connection" : "导线连接",
            rope_attach: lang === "en" ? "Suspension" : "悬挂", tube_terminal: lang === "en" ? "Tube connection" : "导管连接",
            support: lang === "en" ? "Support" : "支撑", heating: lang === "en" ? "Heating" : "加热",
            geometry_construction: lang === "en" ? "Exact construction" : "几何构造", data_binding: lang === "en" ? "Question data" : "题目数据",
            function_binding: lang === "en" ? "Function" : "函数",
          }[capability] ?? capability)).join(" · ")}</p>
          <p>{lang === "en" ? "Readings, states and data must come from question material." : "读数、状态和图表数据须绑定题目条件；预览示例不用于出题。"}</p>
        </div>}
        <p className="mt-4 text-xs text-muted" data-testid="asset-origin">{tr("original")}</p>
      </div>
      <form onSubmit={e => { e.preventDefault(); void apply(); }} className="min-w-0 space-y-4">
        <h3 className="text-sm font-medium text-fg">{tr("settings")}</h3>
        <Field label={tr("style")}><select aria-label={tr("style")} className={FIELD_CLS} value={profile} onChange={e => setProfile(e.target.value)}><option value="textbook">{tr("textbook")}</option><option value="monochrome">{tr("monochrome")}</option></select></Field>
        <div className="max-h-[320px] space-y-3 overflow-y-auto pr-2">
          {detail && Object.entries(detail.parameters).map(([key, spec]) => {
            const label = tr(key === "function" ? "expression" : key);
            return spec.type === "boolean" ? <label key={key} className="flex cursor-pointer items-center gap-2 text-xs"><Input type="checkbox" checked={values[key] === "true"} onChange={e => setValues(v => ({ ...v, [key]: String(e.target.checked) }))} />{label}</label>
              : <Field key={key} label={label}>{spec.type === "list" ? <Textarea aria-label={label} rows={2} value={values[key] ?? ""} onChange={e => setValues(v => ({ ...v, [key]: e.target.value }))} spellCheck={false} />
                : spec.type === "enum" ? <select aria-label={label} className={FIELD_CLS} value={values[key] ?? ""} onChange={e => setValues(v => ({ ...v, [key]: e.target.value }))}>{spec.choices?.map(choice => <option key={choice} value={choice}>{tr(choice)}</option>)}</select>
                : <Input aria-label={label} type={spec.type === "number" || spec.type === "integer" ? "number" : spec.type === "color" ? "color" : "text"} className={spec.type === "color" ? "h-10 cursor-pointer" : undefined} style={spec.type === "color" ? { padding: 6 } : undefined} min={spec.minimum} max={spec.maximum} step={spec.type === "integer" ? 1 : "any"} value={values[key] ?? ""} onChange={e => setValues(v => ({ ...v, [key]: e.target.value }))} />}</Field>;
          })}
        </div>
        {error && <p role="alert" className="text-xs text-danger">{error}</p>}
        <div className="flex gap-2"><Button type="submit" size="sm" disabled={!detail || busy}>{tr("apply")}</Button>
          <Button type="button" size="sm" variant="outline" disabled={!detail || busy} onClick={() => detail && reset(detail)}>{tr("reset")}</Button></div>
      </form>
    </div>
  </Modal>;
}
