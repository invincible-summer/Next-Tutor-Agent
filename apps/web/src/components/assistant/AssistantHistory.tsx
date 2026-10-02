"use client";

// 历史会话视图（plan.md §3.5，A06）：面板内部视图，Pager 每页 20。
import { useEffect } from "react";
import { ChevronLeft, Plus, Trash2 } from "lucide-react";
import { useAssistantStore } from "@/lib/assistant/store";
import { Pager } from "@/components/ui/Pager";
import { stringsFor } from "./strings";

export function AssistantHistory() {
  const lang = useAssistantStore((s) => s.lang);
  const history = useAssistantStore((s) => s.history);
  const historyTotal = useAssistantStore((s) => s.historyTotal);
  const historyPage = useAssistantStore((s) => s.historyPage);
  const historyLoading = useAssistantStore((s) => s.historyLoading);
  const refreshHistory = useAssistantStore((s) => s.refreshHistory);
  const openConversation = useAssistantStore((s) => s.openConversation);
  const removeConversation = useAssistantStore((s) => s.removeConversation);
  const setView = useAssistantStore((s) => s.setView);
  const newConversation = useAssistantStore((s) => s.newConversation);
  const t = stringsFor(lang);

  useEffect(() => {
    void refreshHistory(0);
  }, [refreshHistory]);

  return (
    <div className="assistant-history">
      <div className="assistant-history-head">
        <button
          type="button"
          className="assistant-icon-btn"
          aria-label={t.historyBack}
          onClick={() => setView("conversation")}
        >
          <ChevronLeft size={16} aria-hidden />
        </button>
        <span className="assistant-history-title">{t.historyTitle}</span>
        <button
          type="button"
          className="assistant-icon-btn"
          aria-label={t.newConversation}
          onClick={() => void newConversation()}
        >
          <Plus size={16} aria-hidden />
        </button>
      </div>
      <div className="assistant-history-list">
        {historyLoading && history.length === 0 && (
          <div className="assistant-history-empty">…</div>
        )}
        {!historyLoading && history.length === 0 && (
          <div className="assistant-history-empty">{t.historyEmpty}</div>
        )}
        {history.map((item) => (
          <div key={item.conversation_id} className="assistant-history-item">
            <button
              type="button"
              className="assistant-history-open"
              onClick={() => void openConversation(item.conversation_id)}
            >
              <span className="assistant-history-name">{item.title}</span>
              <span className="assistant-history-date">
                {String(item.updated_at ?? "").slice(0, 10)}
              </span>
            </button>
            <button
              type="button"
              className="assistant-icon-btn"
              aria-label={t.deleteConversation}
              title={t.deleteConversation}
              onClick={() => {
                if (window.confirm(t.deleteConfirm)) {
                  void removeConversation(item.conversation_id, item.revision);
                }
              }}
            >
              <Trash2 size={14} aria-hidden />
            </button>
          </div>
        ))}
      </div>
      {historyTotal > 20 && (
        <Pager
          page={historyPage}
          total={historyTotal}
          per={20}
          onPage={(page) => void refreshHistory(page)}
        />
      )}
    </div>
  );
}
