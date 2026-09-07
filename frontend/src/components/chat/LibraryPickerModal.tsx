"use client";
import { useEffect, useState } from "react";
import { BookOpen, CheckSquare, Globe2, LibraryBig, Loader2, Square } from "lucide-react";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { useUIStore } from "@/lib/store";
import { t } from "@/lib/i18n";
import { getTextbooks, type TextbookListItem } from "@/lib/api";

export interface LibraryRefItem {
  id: string;
  filename: string;
}

/** 从教材库挑选教材引用进当前对话（多选）。引用是"复制进会话"：
 *  只影响本次对话的 RAG 上下文，不改动教材库本身。
 *  P6-C3：引用来源只保留教材（公用 + 我的），普通文件不再可选。
 *  挂载式用法：父组件仅在打开时渲染（每次打开都是全新状态）。 */
export function LibraryPickerModal({
  onClose,
  onConfirm,
}: {
  onClose: () => void;
  onConfirm: (items: LibraryRefItem[]) => void;
}) {
  const lang = useUIStore((s) => s.lang);
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  const [textbooks, setTextbooks] = useState<TextbookListItem[] | null>(null);
  const [error, setError] = useState(false);
  const [checked, setChecked] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    getTextbooks()
      .then((tbs) => {
        if (cancelled) return;
        // 只展示 ready 的教材（building/failed 不可用）。
        setTextbooks((tbs || []).filter((tb) => tb.status === "ready"));
      })
      .catch(() => { if (!cancelled) setError(true); });
    return () => { cancelled = true; };
  }, []);

  const toggle = (id: string) =>
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // 教材组：一行勾选 = 全部卷 file_ids 一起选/取消（组键 = 组记录 id）。
  const groupChecked = (tb: TextbookListItem) =>
    (tb.file_ids || []).length > 0 && (tb.file_ids || []).every((fid) => checked.has(fid));

  const toggleGroup = (tb: TextbookListItem) =>
    setChecked((prev) => {
      const next = new Set(prev);
      const all = (tb.file_ids || []).every((fid) => next.has(fid));
      for (const fid of tb.file_ids || []) {
        if (all) next.delete(fid);
        else next.add(fid);
      }
      return next;
    });

  const handleConfirm = () => {
    const items: { id: string; filename: string }[] = [];
    for (const tb of textbooks ?? []) {
      if (tb.kind === "group") {
        // 组展开为各卷（filename 带组名前缀便于识别）。
        for (const v of tb.volumes || []) {
          if (checked.has(v.file_id)) {
            items.push({ id: v.file_id, filename: `${tb.title}·${v.filename}` });
          }
        }
      } else if (checked.has(tb.file_id)) {
        items.push({ id: tb.file_id, filename: tb.title });
      }
    }
    onConfirm(items);
  };

  const renderRow = (tb: TextbookListItem) => {
    const isGroup = tb.kind === "group";
    const on = isGroup ? groupChecked(tb) : checked.has(tb.file_id);
    return (
      <button
        key={tb.id}
        onClick={() => (isGroup ? toggleGroup(tb) : toggle(tb.file_id))}
        className="flex w-full cursor-pointer items-center gap-2 rounded-[6px] px-2 py-1.5 text-left transition-colors hover:bg-surface-hover"
      >
        {on ? <CheckSquare size={14} className="shrink-0 text-accent" /> : <Square size={14} className="shrink-0 text-muted/50" />}
        <BookOpen size={13} className="shrink-0 text-accent/70" />
        <span className="min-w-0 flex-1 truncate text-[0.78rem] text-fg">
          {tb.title}
          {isGroup && <span className="text-muted"> · {(tb.file_ids || []).length}卷</span>}
          {tb.level ? <span className="text-muted"> · {tb.level}</span> : null}
          {tb.subject ? <span className="text-muted"> · {tb.subject}</span> : null}
        </span>
      </button>
    );
  };

  const publicTbs = (textbooks ?? []).filter((tb) => tb.scope === "public");
  const ownTbs = (textbooks ?? []).filter((tb) => tb.scope !== "public");

  return (
    <Modal
      open
      onClose={onClose}
      width={520}
      title={
        <span className="flex items-center gap-2">
          <LibraryBig size={15} className="text-accent" />
          {tr("chat.libref.title")}
        </span>
      }
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>
            {tr("common.cancel")}
          </Button>
          <Button size="sm" disabled={checked.size === 0} onClick={handleConfirm}>
            {tr("chat.libref.confirm")}
            {checked.size > 0 ? `（${checked.size}）` : ""}
          </Button>
        </>
      }
    >
      <p className="mb-3 text-[0.72rem] leading-relaxed text-muted">{tr("chat.libref.desc")}</p>
      {!textbooks && !error && (
        <div className="flex items-center justify-center gap-2 py-8 text-muted">
          <Loader2 size={15} className="animate-spin" />
        </div>
      )}
      {error && <p className="py-6 text-center text-[0.78rem] text-danger">{tr("chat.libref.error")}</p>}
      {textbooks && textbooks.length === 0 && (
        <p className="py-6 text-center text-[0.78rem] text-muted">{tr("chat.libref.empty")}</p>
      )}
      {textbooks && textbooks.length > 0 && (
        <div className="max-h-[46vh] space-y-3 overflow-y-auto pr-1">
          {publicTbs.length > 0 && (
            <div>
              <div className="mb-1 flex items-center gap-1.5 px-1 text-[0.7rem] font-medium text-fg-secondary">
                <Globe2 size={12} className="text-accent/70" />
                <span className="truncate">{tr("chat.libref.public", "公用教材库")}</span>
                <span className="text-muted/60">{publicTbs.length}</span>
              </div>
              <div className="space-y-0.5">{publicTbs.map(renderRow)}</div>
            </div>
          )}
          {ownTbs.length > 0 && (
            <div>
              <div className="mb-1 flex items-center gap-1.5 px-1 text-[0.7rem] font-medium text-fg-secondary">
                <BookOpen size={12} className="text-accent/70" />
                <span className="truncate">{tr("chat.libref.mine", "我的教材")}</span>
                <span className="text-muted/60">{ownTbs.length}</span>
              </div>
              <div className="space-y-0.5">{ownTbs.map(renderRow)}</div>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
