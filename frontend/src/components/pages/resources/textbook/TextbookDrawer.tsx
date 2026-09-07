"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { X, BookOpen, Loader2, AlertTriangle, Pencil, Download, Trash2, Check, X as CloseIcon } from "lucide-react";
import type { TextbookDetail, TextbookGraphPolicy, TextbookListItem } from "@/lib/api";
import { getTextbook, patchTextbook, patchTextbookVolume, removeTextbookVolume, downloadTextbookVolume, setTextbookGraphPolicy } from "@/lib/api";
import type { Lang } from "@/lib/i18n";
import { Badge } from "@/components/ui/Badge";
import { GRADE_LABELS } from "@/lib/i18n";

/** 教材详情抽屉：章节大纲树 + 概念清单 + warnings + 跳知识图谱 + 编辑信息。
 *  教材组（kind=group）：卷清单（逐卷下载/移除，剩余卷自动重建组图谱）。 */
export function TextbookDrawer({
  textbookId,
  open,
  lang,
  tr,
  canWrite = true,
  onClose,
  onUpdated,
}: {
  textbookId: string | null;
  open: boolean;
  lang: Lang;
  tr: (key: string, fallback?: string) => string;
  /** 公用教材对非管理员为 false：隐藏卷移除按钮。 */
  canWrite?: boolean;
  onClose: () => void;
  onUpdated?: (tb: TextbookListItem) => void;
}) {
  const [detail, setDetail] = useState<TextbookDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ title: "", group_name: "", group_note: "", subject: "", level: "", filename: "" });
  const [editingVolume, setEditingVolume] = useState<string | null>(null);
  const [volumeName, setVolumeName] = useState("");
  const [saving, setSaving] = useState(false);
  const [volBusy, setVolBusy] = useState<string | null>(null);
  const [policy, setPolicy] = useState<TextbookGraphPolicy>({ default_max_chapters: null, default_max_concepts: null, volume_overrides: {} });

  useEffect(() => {
    if (!open || !textbookId) return;
    let cancelled = false;
    // setState 只在 Promise 回调里发生（lint: react-hooks/set-state-in-effect）；
    // loading 的重置由父级 key={textbookId} 触发 remount 实现（初始 loading=true）。
    getTextbook(textbookId)
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
        setPolicy(d.textbook.graph_policy ?? { default_max_chapters: null, default_max_concepts: null, volume_overrides: {} });
        setForm({ title: d.textbook.title, group_name: d.textbook.group_name || d.textbook.title, group_note: d.textbook.group_note || "", subject: d.textbook.subject, level: d.textbook.level, filename: d.textbook.filename });
        setEditing(false);
        setErr(null);
        setLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setErr(tr("res.load.failed"));
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, [open, textbookId, tr]);

  if (!open) return null;
  const tb = detail?.textbook;

  const save = async () => {
    if (!tb) return;
    setSaving(true);
    try {
      const { textbook } = await patchTextbook(tb.id, {
        title: form.group_name || form.title,
        group_name: form.group_name || form.title,
        group_note: form.group_note,
        subject: form.subject,
        level: form.level,
      });
      if (tb.file_id && form.filename.trim() && form.filename.trim() !== tb.filename) {
        await patchTextbookVolume(tb.id, tb.file_id, form.filename.trim());
      }
      setDetail((prev) => (prev ? { ...prev, textbook: { ...textbook, filename: form.filename.trim() || textbook.filename } } : prev));
      onUpdated?.(textbook);
      setEditing(false);
    } catch {
      setErr(tr("res.load.failed"));
    } finally {
      setSaving(false);
    }
  };

  const removeVolume = async (fileId: string) => {
    if (!tb || volBusy) return;
    setVolBusy(fileId);
    try {
      await removeTextbookVolume(tb.id, fileId);
      onUpdated?.(tb); // 触发父级刷新；组可能已被整组删除
      getTextbook(tb.id)
        .then((d) => setDetail(d))
        .catch(() => onClose()); // 组删空后详情 404 → 关抽屉
    } catch {
      setErr(tr("res.load.failed"));
    } finally {
      setVolBusy(null);
    }
  };

  const savePolicy = async () => {
    if (!tb || saving) return;
    setSaving(true);
    try {
      await setTextbookGraphPolicy(tb.id, policy);
      const next = await getTextbook(tb.id);
      setDetail(next);
      onUpdated?.(next.textbook);
    } catch { setErr(tr("res.load.failed")); }
    finally { setSaving(false); }
  };

  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="relative flex h-full w-full max-w-md flex-col bg-surface shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex min-w-0 items-center gap-2">
            <BookOpen size={16} className="shrink-0 text-accent" />
            <h2 className="truncate font-serif text-base font-semibold text-fg">{tr("res.tb.detail.title")}</h2>
          </div>
          <button onClick={onClose} className="rounded-[6px] p-1 text-muted hover:bg-surface-hover hover:text-fg">
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-4">
          {loading && <div className="flex justify-center py-8"><Loader2 className="animate-spin text-muted" /></div>}
          {err && !loading && <p className="text-sm text-danger">{err}</p>}
          {tb && !loading && (
            <div className="flex flex-col gap-4">
              {/* 标题 + 徽标 */}
              {!editing ? (
                <div>
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="font-serif text-lg font-semibold text-fg">{tb.title}</h3>
                    <button onClick={() => setEditing(true)} title={tr("res.tb.edit.title")} className="shrink-0 rounded-[6px] p-1 text-muted hover:bg-surface-hover hover:text-accent">
                      <Pencil size={14} />
                    </button>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    {tb.subject && <Badge tone="accent">{tb.subject}</Badge>}
                    {tb.level && <Badge tone="muted">{tb.level}</Badge>}
                    <span className="tnum text-[11px] text-muted">{tb.filename}</span>
                  </div>
                </div>
              ) : (
                <div className="flex flex-col gap-2 rounded-[10px] border border-border p-3">
                  <label className="text-xs text-muted">{tr("res.title")}</label>
                  <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })}
                    className="rounded-[6px] border border-border bg-transparent px-2 py-1.5 text-sm text-fg outline-none focus:border-accent" />
                  <label className="text-xs text-muted">{tr("res.tb.group.name", "教材组/栏目名称")}</label>
                  <input value={form.group_name} onChange={(e) => setForm({ ...form, group_name: e.target.value })}
                    className="rounded-[6px] border border-border bg-transparent px-2 py-1.5 text-sm text-fg outline-none focus:border-accent" />
                  <label className="text-xs text-muted">{tr("res.tb.group.note.edit", "教材组备注")}</label>
                  <textarea value={form.group_note} onChange={(e) => setForm({ ...form, group_note: e.target.value })}
                    rows={2} className="resize-none rounded-[6px] border border-border bg-transparent px-2 py-1.5 text-sm text-fg outline-none focus:border-accent" />
                  <label className="text-xs text-muted">{tr("res.tb.subject")}</label>
                  <input value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })}
                    className="rounded-[6px] border border-border bg-transparent px-2 py-1.5 text-sm text-fg outline-none focus:border-accent" />
                  <label className="text-xs text-muted">{tr("res.tb.file.name", "PDF 文件显示名")}</label>
                  <input value={form.filename} onChange={(e) => setForm({ ...form, filename: e.target.value })}
                    className="rounded-[6px] border border-border bg-transparent px-2 py-1.5 text-sm text-fg outline-none focus:border-accent" />
                  <label className="text-xs text-muted">{tr("res.tb.level")}</label>
                  <select value={form.level} onChange={(e) => setForm({ ...form, level: e.target.value })}
                    className="rounded-[6px] border border-border bg-transparent px-2 py-1.5 text-sm text-fg outline-none focus:border-accent">
                    <option value="">{lang === "zh" ? "自动" : "Auto"}</option>
                    {GRADE_LABELS[lang].filter((g) => g.token !== "自动").map((g) => (
                      <option key={g.token} value={g.token}>{g.label}</option>
                    ))}
                  </select>
                  <div className="mt-1 flex justify-end gap-2">
                    <button onClick={() => setEditing(false)} className="rounded-[6px] px-3 py-1 text-xs text-fg-secondary hover:bg-surface-hover">{tr("res.tb.cancel")}</button>
                    <button onClick={save} disabled={saving} className="rounded-[6px] bg-accent px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50">{tr("res.tb.save")}</button>
                  </div>
                </div>
              )}

              {/* 教材组：卷清单（逐卷下载/移除；移除后剩余卷自动重建组图谱） */}
              {tb.kind === "group" && (tb.volumes || []).length > 0 && (
                <div>
                  <h4 className="mb-2 text-sm font-medium text-fg">
                    {tr("res.tb.group.volumes.title", "分卷")} · {(tb.volumes || []).length}
                  </h4>
                  <div className="flex flex-col gap-1.5">
                    {(tb.volumes || []).map((v) => (
                      <div key={v.file_id} className="flex items-center gap-2 rounded-[8px] border border-border px-2.5 py-1.5">
                        <BookOpen size={12} className="shrink-0 text-accent/70" />
                        {editingVolume === v.file_id ? (
                          <input autoFocus value={volumeName} onChange={(e) => setVolumeName(e.target.value)}
                            className="min-w-0 flex-1 rounded border border-accent bg-transparent px-1.5 py-1 text-xs text-fg outline-none" />
                        ) : (
                          <span className="min-w-0 flex-1 truncate text-xs text-fg" title={v.filename}>{v.filename}</span>
                        )}
                        {editingVolume === v.file_id ? (
                          <>
                            <button onClick={() => void patchTextbookVolume(tb.id, v.file_id, volumeName).then(() => { setEditingVolume(null); return getTextbook(tb.id); }).then(setDetail).catch(() => setErr(tr("res.load.failed")))} className="shrink-0 rounded p-1 text-accent hover:bg-accent-soft"><Check size={13} /></button>
                            <button onClick={() => setEditingVolume(null)} className="shrink-0 rounded p-1 text-muted hover:bg-surface-hover"><CloseIcon size={13} /></button>
                          </>
                        ) : (
                          <button onClick={() => { setEditingVolume(v.file_id); setVolumeName(v.filename); }} title={tr("res.rename")} className="shrink-0 rounded p-1 text-muted hover:bg-accent-soft hover:text-accent"><Pencil size={13} /></button>
                        )}
                        {v.has_original && (
                          <button
                            onClick={() => void downloadTextbookVolume(tb.id, v.file_id).catch(() => setErr(tr("res.download.failed")))}
                            title={tr("res.download")}
                            className="shrink-0 cursor-pointer rounded-[6px] p-1 text-muted hover:bg-accent-soft hover:text-accent"
                          >
                            <Download size={13} />
                          </button>
                        )}
                        {canWrite && (
                          <button
                            onClick={() => void removeVolume(v.file_id)}
                            disabled={volBusy !== null}
                            title={tr("res.tb.group.volume.remove", "移除该卷（剩余卷自动重建图谱）")}
                            className="shrink-0 cursor-pointer rounded-[6px] p-1 text-muted hover:bg-danger/10 hover:text-danger disabled:opacity-50"
                          >
                            {volBusy === v.file_id ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                  <p className="mt-1.5 text-[11px] leading-relaxed text-muted/80">
                    {tr("res.tb.group.note", "组后加的卷不会自动进入已存在的会话/工作区选择，需要重新勾选。")}
                  </p>
                </div>
              )}

              {canWrite && (tb.volumes || []).length > 0 && (
                <div className="space-y-2 rounded-[10px] border border-border p-3">
                  <h4 className="text-sm font-medium text-fg">知识谱系容量设置</h4>
                  <p className="text-[11px] leading-relaxed text-muted">留空表示不限制。每本教材独立应用；保存只复用抽取缓存重新裁剪合并，不会 OCR、重新解析或调用 LLM。卡片上的“重新生成知识谱系”才会调用 LLM。</p>
                  <div className="grid grid-cols-2 gap-2">
                    <label className="text-[11px] text-muted">组默认章节
                      <input type="number" min={1} value={policy.default_max_chapters ?? ""}
                        onChange={(e) => setPolicy({ ...policy, default_max_chapters: e.target.value ? Number(e.target.value) : null })}
                        placeholder="不限制" className="mt-1 h-7 w-full rounded border border-border bg-surface px-2 text-xs text-fg" />
                    </label>
                    <label className="text-[11px] text-muted">组默认概念
                      <input type="number" min={1} value={policy.default_max_concepts ?? ""}
                        onChange={(e) => setPolicy({ ...policy, default_max_concepts: e.target.value ? Number(e.target.value) : null })}
                        placeholder="不限制" className="mt-1 h-7 w-full rounded border border-border bg-surface px-2 text-xs text-fg" />
                    </label>
                  </div>
                  {(tb.volumes || []).map((volume) => {
                    const override = policy.volume_overrides[volume.file_id];
                    return <div key={`policy-${volume.file_id}`} className="grid gap-1.5 border-t border-border-light pt-2 sm:grid-cols-[1fr_90px_90px] sm:items-end">
                      <div className="truncate text-[11px] text-fg-secondary">{volume.filename}<br /><span className="text-muted">{override ? "自定义" : "使用组默认"}</span></div>
                      <input type="number" min={1} value={override?.max_chapters ?? ""} placeholder="章节继承"
                        onChange={(e) => setPolicy({ ...policy, volume_overrides: { ...policy.volume_overrides,
                          [volume.file_id]: { max_chapters: e.target.value ? Number(e.target.value) : null, max_concepts: override?.max_concepts ?? null } } })}
                        className="h-7 rounded border border-border bg-surface px-1.5 text-[11px] text-fg" />
                      <input type="number" min={1} value={override?.max_concepts ?? ""} placeholder="概念继承"
                        onChange={(e) => setPolicy({ ...policy, volume_overrides: { ...policy.volume_overrides,
                          [volume.file_id]: { max_chapters: override?.max_chapters ?? null, max_concepts: e.target.value ? Number(e.target.value) : null } } })}
                        className="h-7 rounded border border-border bg-surface px-1.5 text-[11px] text-fg" />
                    </div>;
                  })}
                  <div className="flex justify-end"><button onClick={() => void savePolicy()} disabled={saving}
                    className="rounded bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50">保存容量设置（快速）</button></div>
                </div>
              )}

              {/* 统计 */}
              <div className="flex gap-4 text-xs text-muted">
                <span className="tnum">{tb.chapter_count} {tr("res.tb.chapters")}</span>
                <span className="tnum">{tb.concept_count} {tr("res.tb.concepts")}</span>
                <Badge tone={tb.status === "ready" ? "success" : ["building", "ocr_waiting"].includes(tb.status) ? "accent" : "warning"}>
                  {tb.status === "ready" ? tr("res.tb.ready")
                    : tb.status === "building" ? tr("res.tb.building")
                    : tb.status === "ocr_waiting" ? tr("res.tb.stage.ocr_waiting", "OCR 等待重试")
                    : tb.status === "ocr_paused" ? tr("res.tb.ocr_paused", "OCR 已暂停")
                    : tr("res.tb.graph_failed")}
                </Badge>
              </div>

              {/* warnings */}
              {tb.warnings.length > 0 && (
                <div className="rounded-[8px] bg-warning/5 p-2.5">
                  <div className="mb-1 flex items-center gap-1 text-xs font-medium text-warning">
                    <AlertTriangle size={12} /> {tr("res.tb.warnings")}
                  </div>
                  <ul className="flex flex-col gap-0.5 text-[11px] text-fg-secondary">
                    {tb.warnings.map((w, i) => <li key={i}>· {w}</li>)}
                  </ul>
                </div>
              )}

              {/* 章节大纲 */}
              <div>
                <h4 className="mb-2 text-sm font-medium text-fg">{tr("res.tb.outline")}</h4>
                {detail && detail.outline.length > 0 ? (
                  <div className="flex flex-col gap-2">
                    {detail.outline.map((ch, i) => (
                      <div key={i} className="rounded-[8px] border border-border p-2.5">
                        <div className="flex items-center justify-between">
                          <span className="text-sm font-medium text-fg">{ch.chapter}</span>
                          <span className="tnum text-[11px] text-muted">{ch.concept_count} {tr("res.tb.concepts")}</span>
                        </div>
                        {ch.concepts.length > 0 && (
                          <div className="mt-1.5 flex flex-wrap gap-1">
                            {ch.concepts.map((c, j) => <Badge key={j} tone="muted">{c}</Badge>)}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-muted">{tr("res.tb.no_outline")}</p>
                )}
              </div>
            </div>
          )}
        </div>

        {tb && !loading && (
          <div className="border-t border-border px-4 py-3">
            <Link
              href={`/knowledge?level=${encodeURIComponent(tb.level || "自定义")}${tb.subject ? `&subject=${encodeURIComponent(tb.subject)}` : ""}`}
              onClick={onClose}
              className="inline-flex w-full items-center justify-center rounded-[8px] border border-border bg-surface px-3 py-2 text-sm font-medium text-fg transition-colors hover:border-accent hover:text-accent"
            >
              {tr("res.tb.view_graph")}
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
