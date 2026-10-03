"use client";

// 收件箱视图（C05）：通知按日期排列（每页 20，Pager），
// 柔和未读点区分未读；每条最多一个主行动（查看落点/简报）；已读幂等、
// 可忽略（未完成课程提醒可附 mute）。简报详情显示固定统计窗口与生成
// 时间（§25.4：旧报表与当前查询区分）。
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Bell, Check, ChevronLeft, Trash2, X } from "lucide-react";
import { useAssistantStore } from "@/lib/assistant/store";
import { Pager } from "@/components/ui/Pager";
import { resolveTargetUrl } from "@/lib/assistant/routes";
import {
  deleteAssistantReport,
  dismissAssistantNotification,
  getAssistantReport,
  listAssistantNotifications,
  markNotificationRead,
  type AssistantNotification,
  type AssistantReport,
} from "@/lib/assistant/api";
import type { NavigationTarget } from "@/lib/assistant/types.generated";
import { stringsFor } from "./strings";

const PAGE_SIZE = 20;

export function AssistantInbox() {
  const lang = useAssistantStore((s) => s.lang);
  const setView = useAssistantStore((s) => s.setView);
  const router = useRouter();
  const t = stringsFor(lang);

  const [items, setItems] = useState<AssistantNotification[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [reportId, setReportId] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    listAssistantNotifications(page * PAGE_SIZE, PAGE_SIZE)
      .then((out) => {
        if (alive) {
          setItems(out.items);
          setTotal(out.total);
        }
      })
      .catch(() => undefined)
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [page]);

  const openTarget = (note: AssistantNotification) => {
    const url = note.target
      ? resolveTargetUrl(note.target as unknown as NavigationTarget)
      : null;
    if (note.report_id) {
      setReportId(note.report_id);
      return;
    }
    if (url) router.push(url);
  };

  return (
    <div className="assistant-history">
      {reportId ? (
        <ReportDetail reportId={reportId}
                      onClose={() => setReportId(null)} t={t} />
      ) : (
        <>
          <div className="assistant-history-head">
            <button
              type="button"
              className="assistant-icon-btn"
              aria-label={t.inboxBack}
              onClick={() => setView("conversation")}
            >
              <ChevronLeft size={16} aria-hidden />
            </button>
            <span className="assistant-history-title">{t.inboxTitle}</span>
            <span style={{ width: 24 }} aria-hidden />
          </div>
          <div className="assistant-history-list">
            {loading && items.length === 0 && (
              <div className="assistant-history-empty">{t.inboxLoading}</div>
            )}
            {!loading && items.length === 0 && (
              <div className="assistant-history-empty">{t.inboxEmpty}</div>
            )}
            {items.map((note) => (
              <div key={note.notification_id}
                   className="assistant-inbox-item"
                   data-unread={!note.read_at}>
                <button
                  type="button"
                  className="assistant-inbox-open"
                  onClick={() => void markNotificationRead(
                    note.notification_id).catch(() => undefined)}
                >
                  <span className="assistant-inbox-title">
                    {!note.read_at && (
                      <span className="assistant-inbox-dot" aria-hidden />
                    )}
                    {note.title}
                  </span>
                  <span className="assistant-inbox-summary">
                    {note.summary}
                  </span>
                  <span className="assistant-inbox-date">
                    {String(note.created_at ?? "").slice(0, 16)
                      .replace("T", " ")}
                  </span>
                </button>
                <div className="assistant-inbox-actions">
                  {(note.target || note.report_id) && (
                    <button
                      type="button"
                      className="assistant-task-mini-btn"
                      onClick={() => openTarget(note)}
                    >
                      <Bell size={11} aria-hidden />
                      {t.inboxOpenTarget}
                    </button>
                  )}
                  {!note.read_at && (
                    <button
                      type="button"
                      className="assistant-task-mini-btn"
                      aria-label={t.inboxMarkRead}
                      title={t.inboxMarkRead}
                      onClick={() => {
                        void markNotificationRead(note.notification_id)
                          .then(() => setItems((prev) => prev.map((n) =>
                            n.notification_id === note.notification_id
                              ? { ...n, read_at: new Date().toISOString() }
                              : n))).catch(() => undefined);
                      }}
                    >
                      <Check size={11} aria-hidden />
                    </button>
                  )}
                  <button
                    type="button"
                    className="assistant-task-mini-btn"
                    aria-label={t.inboxDismiss}
                    title={t.inboxDismissMute}
                    onClick={() => {
                      const mute = note.kind === "subscription"
                        && Boolean(note.target?.mute_key);
                      void dismissAssistantNotification(
                        note.notification_id, mute)
                        .then(() => {
                          setItems((prev) => prev.filter((n) =>
                            n.notification_id !== note.notification_id));
                        })
                        .catch(() => undefined);
                    }}
                  >
                    <X size={11} aria-hidden />
                  </button>
                </div>
              </div>
            ))}
          </div>
          {total > PAGE_SIZE && (
            <Pager page={page} total={total} per={PAGE_SIZE}
                   onPage={setPage} />
          )}
        </>
      )}
    </div>
  );
}

function ReportDetail({
  reportId, onClose, t,
}: {
  reportId: string; onClose: () => void;
  t: ReturnType<typeof stringsFor>;
}) {
  const [report, setReport] = useState<AssistantReport | null>(null);
  const [deleted, setDeleted] = useState(false);

  useEffect(() => {
    let alive = true;
    getAssistantReport(reportId)
      .then((out) => {
        if (alive) setReport(out);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [reportId]);

  const windowLabel = report?.window?.label
    ?? (report?.window?.start_at
        ? `${String(report.window.start_at).slice(0, 10)} ~ `
          + `${String(report.window?.end_at ?? "").slice(0, 10)}`
        : "");

  return (
    <>
      <div className="assistant-history-head">
        <button
          type="button"
          className="assistant-icon-btn"
          aria-label={t.inboxBack}
          onClick={onClose}
        >
          <ChevronLeft size={16} aria-hidden />
        </button>
        <span className="assistant-history-title">
          {t.inboxReportWindow}：{windowLabel}
        </span>
        <button
          type="button"
          className="assistant-icon-btn"
          aria-label={t.inboxReportDelete}
          title={t.inboxReportDelete}
          onClick={() => {
            void deleteAssistantReport(reportId)
              .then(() => setDeleted(true))
              .catch(() => undefined);
          }}
        >
          <Trash2 size={14} aria-hidden />
        </button>
      </div>
      <div className="assistant-history-list">
        {deleted && (
          <div className="assistant-history-empty">
            {t.inboxReportDeleted}
          </div>
        )}
        {!deleted && !report && (
          <div className="assistant-history-empty">{t.inboxLoading}</div>
        )}
        {!deleted && report?.expired && (
          <div className="assistant-history-empty">{t.inboxEmpty}</div>
        )}
        {!deleted && report && !report.expired && (
          <pre className="assistant-inbox-report-json">
{JSON.stringify(report.learning_report ?? report, null, 2)}
          </pre>
        )}
      </div>
    </>
  );
}
