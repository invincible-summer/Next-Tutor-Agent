"use client";

// 消息区（A06）：滚动跟随（底部 64px 内自动跟随），
// 向上阅读时显示「新内容」按钮；用户消息浅强调底靠右（max-w 88%）；
// Markdown 复用 StreamingMarkdown；结构化块渲染 cards/*。
import { useEffect, useRef, useState } from "react";
import { ArrowDown } from "lucide-react";
import { cn } from "@/lib/cn";
import { useAssistantStore } from "@/lib/assistant/store";
import { StreamingMarkdown } from "@/components/chat/markdown";
import { stringsFor } from "./strings";
import { ActionCard } from "./cards/ActionCard";
import { LearningReportCard } from "./cards/LearningReportCard";
import { TeachingReportCard } from "./cards/TeachingReportCard";
import { ChoiceCard } from "./cards/ChoiceCard";
import { MetricsCard } from "./cards/MetricsCard";
import { NoticeCard } from "./cards/NoticeCard";
import type { AssistantMessage, AssistantBlock } from "@/lib/assistant/types.generated";

function BlockView({ block }: { block: AssistantBlock }) {
  switch (block.type) {
    case "markdown":
      return <StreamingMarkdown className="assistant-md">
        {block.text}
      </StreamingMarkdown>;
    case "metrics":
      return <MetricsCard items={block.items} />;
    case "learning_report":
      return <LearningReportCard report={block.report} />;
    case "teaching_report":
      return <TeachingReportCard report={block.report} />;
    case "actions":
      return (
        <div className="assistant-card-stack">
          {block.items.map((action) => (
            <ActionCard key={action.action_id} action={action} />
          ))}
        </div>
      );
    case "choices":
      return <ChoiceCard blockId={block.block_id} prompt={block.prompt}
        options={block.options} />;
    case "notice":
      return <NoticeCard tone={block.tone} code={block.code}
        text={block.text} />;
    default:
      return null;
  }
}

export function AssistantMessages() {
  const lang = useAssistantStore((s) => s.lang);
  const messages = useAssistantStore((s) => s.messages);
  const turnState = useAssistantStore((s) => s.turnState);
  const statusLabel = useAssistantStore((s) => s.statusLabel);
  const error = useAssistantStore((s) => s.error);
  const t = stringsFor(lang);
  const scrollRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);
  const [newContent, setNewContent] = useState(false);
  const busy = ["accepting", "running", "reconnecting"].includes(turnState);

  // 滚动跟随只操作 DOM；「新内容」按钮由滚动事件驱动（用户离开底部
  // 即显示，回到底部隐藏），不在 effect 里 setState。
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (followRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight <= 64;
    followRef.current = nearBottom;
    if (nearBottom) setNewContent(false);
    else if (busy) setNewContent(true);
  };

  return (
    <div className="assistant-messages-wrap">
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="assistant-messages"
        role="log"
        aria-live="polite"
      >
        {messages.length === 0 && <WelcomeBlock />}
        {messages.map((message) => (
          <MessageRow key={message.message_id} message={message} />
        ))}
        {busy && statusLabel && (
          <div className="assistant-status" role="status">
            {t.statusMap?.[statusLabel] ?? statusLabel}
          </div>
        )}
        {turnState === "reconnecting" && (
          <div className="assistant-status assistant-status-warn" role="status">
            {t.connectionLost}
          </div>
        )}
        {error && (
          <div className="assistant-status assistant-status-error" role="alert">
            {error}
          </div>
        )}
      </div>
      {newContent && (
        <button
          type="button"
          className="assistant-new-content"
          onClick={() => {
            followRef.current = true;
            const el = scrollRef.current;
            if (el) el.scrollTop = el.scrollHeight;
            setNewContent(false);
          }}
        >
          <ArrowDown size={14} aria-hidden />
          {t.newContent}
        </button>
      )}
    </div>
  );
}

function WelcomeBlock() {
  const lang = useAssistantStore((s) => s.lang);
  const send = useAssistantStore((s) => s.send);
  const capabilities = useAssistantStore((s) => s.capabilities);
  const t = stringsFor(lang);
  const quick = [t.quickIntro, t.quickStart, t.quickRecent];
  if ((capabilities?.modules ?? []).some(
      (m) => m.route_id === "course" && m.available)) {
    quick.splice(2, 0, t.quickCourse);
  }
  return (
    <div className="assistant-welcome">
      <p className="assistant-welcome-text">{t.welcome}</p>
      <div className="assistant-quick-grid">
        {quick.slice(0, 4).map((q) => (
          <button
            key={q}
            type="button"
            className="assistant-quick-btn"
            onClick={() => void send(q)}
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}

function MessageRow({ message }: { message: AssistantMessage }) {
  const lang = useAssistantStore((s) => s.lang);
  const isUser = message.role === "user";
  return (
    <div className={cn("assistant-msg", isUser ? "assistant-msg-user" : "assistant-msg-bot")}
      data-status={message.status}>
      {(message.blocks ?? []).map((block) => (
        <div key={block.block_id} className="assistant-msg-block">
          <BlockView block={block} />
        </div>
      ))}
      {message.status === "cancelled" && (
        <div className="assistant-msg-meta">{stringsFor(lang).stopped}</div>
      )}
    </div>
  );
}
