"use client";
// 数据清理面板：扫描/清理孤儿数据——单元测试直写生产目录的合成 ID 残留、
// 已注销账号的遗物、无引用 trace、失去会话的转写、空回收站目录等。
// 注册账号与 public / student_default 共享命名空间由后端保护，永不被清。
import { useCallback, useEffect, useState } from "react";
import { Eraser, RefreshCw, Trash2 } from "lucide-react";
import { purgeAdminOrphanData, scanAdminOrphanData, type AdminOrphanReport } from "@/lib/api";
import { fmtBytes } from "@/lib/format";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ConfirmModal } from "@/components/ui/Modal";
import { Skeleton } from "@/components/ui/EmptyState";
import type { Tr } from "./Field";

const CATEGORY_KEYS: Record<string, string> = {
  students: "adm.cleanup.cat.students",
  sessions: "adm.cleanup.cat.sessions",
  transcripts: "adm.cleanup.cat.transcripts",
  traces: "adm.cleanup.cat.traces",
  uploads: "adm.cleanup.cat.uploads",
  workspaces: "adm.cleanup.cat.workspaces",
  library: "adm.cleanup.cat.library",
  trash: "adm.cleanup.cat.trash",
  notes: "adm.cleanup.cat.notes",
  knowledge: "adm.cleanup.cat.knowledge",
};

export function CleanupPanel({ tr, refresh }: { tr: Tr; refresh: () => void }) {
  const [report, setReport] = useState<AdminOrphanReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState<{ text: string; tone: "ok" | "err" } | null>(null);

  const flash = (text: string, tone: "ok" | "err") => {
    setNotice({ text, tone });
    setTimeout(() => setNotice(null), 5000);
  };

  const load = useCallback(() => {
    scanAdminOrphanData()
      .then((r) => setReport(r))
      .catch(() => flash(tr("adm.cleanup.loadFail"), "err"))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { load(); }, [load]);

  const rescan = () => {
    setLoading(true);
    load();
  };

  const purge = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const result = await purgeAdminOrphanData();
      setConfirming(false);
      flash(tr("adm.cleanup.notice.purged")
        .replace("{count}", String(result.total_deleted))
        .replace("{size}", fmtBytes(result.total_bytes)), "ok");
      load();
      refresh();
    } catch (e) {
      flash(`${tr("adm.cleanup.notice.fail")}（${e instanceof Error ? e.message : String(e)}）`, "err");
    } finally {
      setBusy(false);
    }
  };

  const rows = report
    ? Object.entries(report.categories).filter(([, c]) => c.items > 0)
    : [];

  return (
    <Card>
      <CardHeader
        icon={<Eraser size={16} />}
        title={tr("adm.cleanup.title")}
        desc={tr("adm.cleanup.desc")}
        right={(
          <Button size="sm" variant="outline" icon={<RefreshCw size={12} />} onClick={rescan}>
            {tr("adm.cleanup.rescan")}
          </Button>
        )}
      />
      {notice && (
        <div className={`mb-3 rounded-[8px] border px-3 py-2 text-xs ${
          notice.tone === "ok" ? "border-accent/40 text-accent" : "border-danger/40 text-danger"}`}>
          {notice.text}
        </div>
      )}
      {loading ? <Skeleton /> : !report ? (
        <div className="text-xs text-muted">{tr("adm.cleanup.loadFail")}</div>
      ) : rows.length === 0 ? (
        <div className="text-xs text-muted">{tr("adm.cleanup.empty")}</div>
      ) : (
        <div className="space-y-3">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="text-muted">
                <th className="py-1.5 pr-3 font-medium">{tr("adm.cleanup.col.category")}</th>
                <th className="py-1.5 pr-3 text-right font-medium">{tr("adm.cleanup.col.items")}</th>
                <th className="py-1.5 pr-3 text-right font-medium">{tr("adm.cleanup.col.bytes")}</th>
                <th className="py-1.5 font-medium">{tr("adm.cleanup.col.samples")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([key, c]) => (
                <tr key={key} className="border-t border-border align-top">
                  <td className="py-1.5 pr-3 text-fg">{tr(CATEGORY_KEYS[key] ?? "", key)}</td>
                  <td className="tnum py-1.5 pr-3 text-right text-fg">{c.items}</td>
                  <td className="tnum py-1.5 pr-3 text-right text-fg">{fmtBytes(c.bytes)}</td>
                  <td className="max-w-[280px] truncate py-1.5 font-mono text-[10px] text-muted" title={c.samples.join("  ")}>
                    {c.samples.join("  ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
            <span className="text-xs text-muted">
              {tr("adm.cleanup.total")
                .replace("{count}", String(report.total_items))
                .replace("{size}", fmtBytes(report.total_bytes))}
            </span>
            <Button size="sm" variant="danger" icon={<Trash2 size={12} />} onClick={() => setConfirming(true)}>
              {tr("adm.cleanup.purge")}
            </Button>
          </div>
        </div>
      )}
      <ConfirmModal
        open={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={() => void purge()}
        title={tr("adm.cleanup.purgeTitle")}
        desc={tr("adm.cleanup.purgeDesc")}
        confirmText={tr("adm.cleanup.purgeConfirm")}
        cancelText={tr("adm.users.cancel")}
      />
    </Card>
  );
}
