"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Image from "next/image";
import { Search, X } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Field, FIELD_CLS, Input } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { Pager } from "@/components/ui/Pager";
import { useToast } from "@/components/ui/Toast";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { getDiagramAsset, getDiagramTaxonomy, listDiagramAssets, type DiagramTaxonomy } from "@/lib/api-diagrams";
import { getMaterial, listMaterials } from "@/lib/api-diagram-materials";
import type { SelectedMaterialRef } from "@/lib/api-illustration-tools";
import type { QuestionIllustrationData } from "@/lib/types";
import { STRINGS } from "@/app/(workspace)/tools/strings";

export interface SelectedMaterial extends SelectedMaterialRef { title: string }
interface MaterialCard extends SelectedMaterial { illustration: QuestionIllustrationData; description: string; aliases: string[] }
const PER = 12;

export function MaterialPicker({ selected, onApply, onClose }: {
  selected: SelectedMaterial[]; onApply: (items: SelectedMaterial[]) => void; onClose: () => void;
}) {
  const lang = useUIStore(s => s.lang);
  const tr = useMemo(() => makePageT(lang, STRINGS), [lang]);
  const notify = useToast();
  const [choice, setChoice] = useState(selected);
  const [scope, setScope] = useState<"builtin" | "public" | "private">("builtin");
  const [query, setQuery] = useState("");
  const [subject, setSubject] = useState("");
  const [family, setFamily] = useState("");
  const [page, setPage] = useState(0);
  const [taxonomy, setTaxonomy] = useState<DiagramTaxonomy | null>(null);
  const [data, setData] = useState<{ items: MaterialCard[]; total: number } | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState(false);
  const [taxonomyError, setTaxonomyError] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [detail, setDetail] = useState<MaterialCard | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [detailError, setDetailError] = useState(false);
  const detailId = detail?.asset_id;
  const detailVersion = detail?.version;

  useEffect(() => {
    const ctl = new AbortController();
    void getDiagramTaxonomy(ctl.signal).then(value => {
      if (!ctl.signal.aborted) { setTaxonomy(value); setTaxonomyError(false); }
    }).catch(() => { if (!ctl.signal.aborted) setTaxonomyError(true); });
    return () => ctl.abort();
  }, [refresh]);
  useEffect(() => {
    const ctl = new AbortController();
    const timer = setTimeout(() => {
      setBusy(true); setError(false);
      const load = scope === "builtin"
        ? listDiagramAssets(new URLSearchParams({ q: query, subject, family, page: String(page), per: String(PER) }), ctl.signal)
          .then(value => ({ total: value.total, items: value.items.map(row => ({
            asset_id: row.id, version: row.version, title: lang === "en" ? row.english : row.title,
            illustration: row.illustration, description: row.features.join(" · "), aliases: row.aliases,
          })) }))
        : listMaterials(scope, query, page, ctl.signal, { subject, enabled_only: true })
          .then(value => ({ total: value.total, items: value.items.map(row => ({
            asset_id: `material.${row.id}`, version: row.revision, title: row.title,
            illustration: row.illustration, description: row.description, aliases: row.aliases,
          })) }));
      void load.then(value => {
        if (ctl.signal.aborted) return;
        const last = Math.max(0, Math.ceil(value.total / PER) - 1);
        if (page > last) setPage(last);
        else setData(value);
      }).catch(() => { if (!ctl.signal.aborted) setError(true); })
        .finally(() => { if (!ctl.signal.aborted) setBusy(false); });
    }, query ? 220 : 0);
    return () => { clearTimeout(timer); ctl.abort(); };
  }, [scope, query, subject, family, page, refresh, lang]);

  useEffect(() => {
    if (!detailId) return;
    const ctl = new AbortController();
    const load = detailId.startsWith("material.")
      ? getMaterial(detailId.slice(9), detailVersion, ctl.signal).then(row => ({ illustration: row.illustration, description: row.description, aliases: row.aliases }))
      : getDiagramAsset(detailId, ctl.signal).then(row => ({ illustration: row.illustration, description: row.features.join(" · "), aliases: row.aliases }));
    void load.then(value => { if (!ctl.signal.aborted) setDetail(row => row ? { ...row, ...value } : row); })
      .catch(() => { if (!ctl.signal.aborted) setDetailError(true); })
      .finally(() => { if (!ctl.signal.aborted) setDetailBusy(false); });
    return () => ctl.abort();
    // The ID and version are fixed while a detail dialog is open.
  }, [detailId, detailVersion]);

  const toggle = (row: SelectedMaterial) => {
    if (choice.some(item => item.asset_id === row.asset_id)) setChoice(items => items.filter(item => item.asset_id !== row.asset_id));
    else if (choice.length >= 12) notify(tr("maxMaterials"), "error");
    else setChoice(items => [...items, { asset_id: row.asset_id, version: row.version, title: row.title }]);
  };
  const closeDetail = useCallback(() => setDetail(null), []);
  const taxLabel = (row: { zh: string; en: string }) => row[lang === "en" ? "en" : "zh"];
  return <>
    <Modal open width={1050} onClose={onClose} title={<div className="flex items-center justify-between gap-3"><span>{tr("pickerTitle")}</span><Button variant="ghost" size="sm" icon={<X size={16} />} onClick={onClose} aria-label={tr("close")} /></div>}
      footer={<><Button variant="ghost" onClick={() => setChoice([])}>{tr("clearMaterials")}</Button><Button onClick={() => onApply(choice)} data-testid="apply-illustration-materials">{tr("applyMaterials")} ({choice.length})</Button></>}>
      <div className="space-y-4" data-testid="illustration-material-picker">
        <div role="group" aria-label={tr("materials")} className="flex gap-2">{(["builtin", "public", "private"] as const).map(value => <Button key={value} size="sm" variant={scope === value ? "primary" : "outline"} aria-pressed={scope === value}
          onClick={() => { setScope(value); setPage(0); setData(null); }}>{tr(value)}</Button>)}</div>
        <div className="grid grid-cols-[minmax(0,1fr)_180px_180px] items-end gap-3">
          <Field label={tr("search")}><div className="relative"><Search size={15} aria-hidden="true" className="pointer-events-none absolute left-3 top-3 text-muted" /><Input className="pl-9" aria-label={tr("search")} placeholder={tr("searchPlaceholder")} value={query} onChange={e => { setQuery(e.target.value); setPage(0); }} /></div></Field>
          <Field label={tr("subject")}><select className={FIELD_CLS} aria-label={tr("subject")} value={subject} onChange={e => { setSubject(e.target.value); setFamily(""); setPage(0); }}><option value="">{tr("allSubjects")}</option>{taxonomy?.subjects.map(row => <option key={row.id} value={row.id}>{taxLabel(row)}</option>)}</select></Field>
          <Field label={tr("family")}><select className={FIELD_CLS} aria-label={tr("family")} disabled={scope !== "builtin"} value={family} onChange={e => { setFamily(e.target.value); setPage(0); }}><option value="">{tr("allFamilies")}</option>{taxonomy?.families.map(row => <option key={row.id} value={row.id}>{taxLabel(row)}</option>)}</select></Field>
        </div>
        <p className="text-xs text-muted">{tr("materialCount").replace("%n", String(choice.length))} · {tr("maxMaterials")}</p>
        <section aria-busy={busy} className="min-h-[240px]">
          {error || taxonomyError ? <div role="alert" className="py-12 text-center"><p className="text-sm text-muted">{tr("materialFailed")}</p><Button className="mt-3" variant="outline" onClick={() => setRefresh(v => v + 1)}>{tr("retry")}</Button></div>
            : !data ? <p role="status" className="py-12 text-center text-sm text-muted">{tr("materialLoading")}</p>
            : !data.items.length ? <p className="py-12 text-center text-sm text-muted">{tr("materialEmpty")}</p>
            : <div className={`grid grid-cols-3 gap-3 ${busy ? "opacity-60" : ""}`}>{data.items.map(row => {
              const checked = choice.some(item => item.asset_id === row.asset_id);
              return <article key={row.asset_id} data-testid="illustration-material" data-asset-id={row.asset_id} className={`overflow-hidden rounded-xl border bg-surface ${checked ? "border-accent ring-1 ring-accent" : "border-border"}`}>
                <label className="block cursor-pointer"><div className="h-32 bg-white"><MaterialArt image={row.illustration} /></div><span className="flex items-center gap-2 px-3 pt-3"><Input type="checkbox" aria-label={row.title} checked={checked} onChange={() => toggle(row)} /><span className="truncate text-xs font-medium text-fg">{row.title}</span></span></label>
                <div className="flex items-center justify-between gap-2 px-3 py-2"><span className="text-[10px] text-muted">v{row.version}</span><Button size="sm" variant="ghost" onClick={() => { setDetail(row); setDetailBusy(true); setDetailError(false); }}>{tr("detail")}</Button></div>
              </article>;
            })}</div>}
        </section>
        {data && <Pager page={page} total={data.total} per={PER} onPage={setPage} />}
      </div>
    </Modal>
    {detail && <Modal open width={720} onClose={closeDetail} title={detail.title} footer={<><Button variant="ghost" onClick={closeDetail}>{tr("close")}</Button><Button onClick={() => toggle(detail)}>{choice.some(row => row.asset_id === detail.asset_id) ? tr("selected") : tr("select")}</Button></>}>
      <div className="h-72 rounded-lg border border-border bg-white"><MaterialArt image={detail.illustration} /></div>
      {detailBusy && <p role="status" className="mt-3 text-xs text-muted">{tr("loading")}</p>}
      {detailError && <p role="alert" className="mt-3 text-xs text-danger">{tr("materialFailed")}</p>}
      <p className="mt-3 text-xs leading-6 text-fg-secondary">{detail.description}</p><p className="mt-2 text-xs text-muted">{detail.aliases.join(" · ")}</p>
      <p className="mt-2 text-xs text-muted">{tr("materialVersion")} {detail.version}</p>
    </Modal>}
  </>;
}

function MaterialArt({ image }: { image: QuestionIllustrationData }) {
  return <Image unoptimized src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(image.svg)}`} alt={image.alt} width={image.width} height={image.height} className="h-full w-full object-contain p-3" />;
}
