"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Image from "next/image";
import { ArrowUpRight, Search, X, ZoomIn, ZoomOut } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea, FIELD_CLS } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { Pager } from "@/components/ui/Pager";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { getDiagramAsset, getDiagramTaxonomy, listDiagramAssets, previewDiagramAsset, type AssetDetail, type AssetPage, type DiagramAsset, type DiagramTaxonomy } from "@/lib/api-diagrams";
import type { QuestionIllustrationData } from "@/lib/types";
import { STRINGS } from "./strings";

const PER = 12;

function Art({ illustration, large = false }: { illustration: QuestionIllustrationData; large?: boolean }) {
  return <Image unoptimized src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(illustration.svg)}`}
    alt={illustration.alt} width={illustration.width} height={illustration.height}
    className={`h-full w-full object-contain ${large ? "p-5" : "p-4"}`} />;
}

export default function DiagramLibraryPage() {
  const lang = useUIStore(s => s.lang);
  const tr = useMemo(() => makePageT(lang, STRINGS), [lang]);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [family, setFamily] = useState("");
  const [level, setLevel] = useState("");
  const [kind, setKind] = useState("");
  const [taxonomy, setTaxonomy] = useState<DiagramTaxonomy | null>(null);
  const [page, setPage] = useState(0);
  const [data, setData] = useState<AssetPage | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState(false);
  const [taxonomyError, setTaxonomyError] = useState(false);
  const [retry, setRetry] = useState(0);
  const [selected, setSelected] = useState<DiagramAsset | null>(null);
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
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setBusy(true); setError(false);
      void listDiagramAssets(new URLSearchParams({ q: query, subject: category, family, education_level: level, asset_kind: kind, page: String(page), per: String(PER) }), controller.signal)
        .then(value => { if (!controller.signal.aborted) setData(value); })
        .catch(() => { if (!controller.signal.aborted) setError(true); })
        .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    }, query ? 220 : 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, category, family, level, kind, page, retry]);

  const close = useCallback(() => setSelected(null), []);
  return <div className="h-full overflow-y-auto overscroll-contain" data-testid="diagram-library">
    <div className="mx-auto w-full max-w-[1440px] space-y-8 px-8 py-8">
    <header className="relative overflow-hidden rounded-[22px] border border-border bg-surface p-8">
      <div className="absolute right-8 top-4 select-none text-[160px] font-light leading-none text-accent/5" aria-hidden="true">◯ △</div>
      <div className="relative max-w-3xl">
        <p className="mb-3 text-[10px] font-semibold tracking-[.24em] text-accent-strong">{tr("eyebrow")}</p>
        <div className="flex items-center gap-4"><h1 className="font-serif text-3xl font-semibold tracking-tight text-fg">{tr("title")}</h1>
          {data && <span className="rounded-full border border-border px-3 py-1 text-xs text-muted">{data.catalog_total} {tr("assets")}</span>}</div>
        <p className="mt-3 text-lg text-fg-secondary">{tr("subtitle")}</p>
        <p className="mt-2 text-sm leading-6 text-muted">{tr("intro")}</p>
      </div>
    </header>

    <section aria-label={tr("filters")} className="space-y-5">
      <div className="grid grid-cols-3 gap-4 rounded-xl border border-border bg-surface p-5">
        <Field label={tr("subject")}><select aria-label={tr("subject")} value={category} className={FIELD_CLS} onChange={e => { setCategory(e.target.value); setFamily(""); setPage(0); }}>
          <option value="">{tr("all")}</option>
          {taxonomy?.subject_groups.map(group => <optgroup key={group.id} label={group[lang === "en" ? "en" : "zh"]}>
            {group.subjects.map(key => <option key={key} value={key}>{label(key)} · {data?.subjects[key] ?? "—"}</option>)}
          </optgroup>)}
        </select></Field>
        <Field label={tr("educationLevel")}><select aria-label={tr("educationLevel")} value={level} className={FIELD_CLS} onChange={e => { setLevel(e.target.value); setPage(0); }}>
          <option value="">{tr("allLevels")}</option>{taxonomy?.education_levels.map(row => <option key={row.id} value={row.id}>{label(row.id)}</option>)}
        </select></Field>
        <Field label={tr("assetKind")}><select aria-label={tr("assetKind")} value={kind} className={FIELD_CLS} onChange={e => { setKind(e.target.value); setPage(0); }}>
          <option value="">{tr("allKinds")}</option>{taxonomy?.asset_kinds.map(row => <option key={row.id} value={row.id}>{label(row.id)}</option>)}
        </select></Field>
      </div>
      <div className="flex items-center gap-4">
        <div className="relative max-w-xl flex-1"><Search size={16} className="pointer-events-none absolute left-3 top-3 text-muted" />
          <Input aria-label={tr("searchLabel")} placeholder={tr("search")} value={query} className="pl-10"
            onChange={e => { setQuery(e.target.value); setPage(0); }} /></div>
        <select aria-label={tr("family")} value={family} className={`${FIELD_CLS} max-w-56`}
          onChange={e => { setFamily(e.target.value); setPage(0); }}><option value="">{tr("allFamilies")}</option>
          {Object.keys(data?.families ?? {}).map(key => <option key={key} value={key}>{label(key)}</option>)}</select>
        <p className="ml-auto whitespace-nowrap text-xs text-muted">{data?.total ?? "—"} {tr("assets")}</p>
      </div>
    </section>

    <section aria-label={query ? tr("result") : tr("selection")} aria-busy={busy}>
      {error || taxonomyError ? <div className="rounded-xl border border-border py-16 text-center text-sm text-muted"><p>{tr("failed")}</p>
        <Button variant="outline" className="mt-4" onClick={() => setRetry(v => v+1)}>{tr("retry")}</Button></div>
        : !data ? <p role="status" className="py-20 text-center text-sm text-muted">{tr("loading")}</p>
        : !data.items.length ? <p className="py-20 text-center text-sm text-muted">{tr("empty")}</p>
        : <div className={`grid grid-cols-2 gap-5 xl:grid-cols-3 2xl:grid-cols-4 ${busy ? "opacity-60" : ""}`}>
          {data.items.map(asset => <button key={asset.id} data-testid="diagram-asset" data-asset-id={asset.id}
            onClick={() => setSelected(asset)} className="group cursor-pointer overflow-hidden rounded-[16px] border border-border bg-surface text-left transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:border-accent/40 hover:shadow-lg focus-visible:outline-2 focus-visible:outline-accent motion-reduce:transform-none">
            <div className="h-48 border-b border-border-light bg-[#fcfcfb]"><Art illustration={asset.illustration} /></div>
            <div className="px-5 py-4"><div className="mb-2 flex items-center justify-between gap-2"><span className="text-[10px] tracking-wide text-muted">{label(asset.category)} / {label(asset.asset_kind)}</span>
              <ArrowUpRight size={14} className="text-muted transition-colors group-hover:text-accent-strong" /></div>
              <h2 className="truncate text-sm font-medium text-fg">{lang === "en" ? asset.english : asset.title}</h2>
              <p className="mt-1 truncate text-[11px] text-muted">{lang === "en" ? asset.title : asset.english}</p>
            </div></button>)}
        </div>}
      {data && data.total > PER && <Pager className="mt-6" page={page} total={data.total} per={PER} onPage={setPage} />}
    </section>
    <p className="border-t border-border-light pt-5 text-[11px] text-muted">{tr("original")}</p>
    {selected && <AssetExplorer key={selected.id} asset={selected} onClose={close} label={label} />}
    </div>
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
    <div className="grid grid-cols-[minmax(0,1fr)_280px] gap-6">
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
