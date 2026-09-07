"use client";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Menu, ArrowRight, Search, BookOpen, GraduationCap, ClipboardList, Target, MessageSquareOff, Plus, PanelRight, Phone } from "lucide-react";
import { useUIStore, useChatStore } from "@/lib/store";
import { t, type Lang } from "@/lib/i18n";
import { Sidebar } from "@/components/sidebar/Sidebar";
import { ChatMessage, StreamingMessage } from "@/components/chat/ChatMessage";
import { ChatInput } from "@/components/chat/ChatInput";
import { VoiceCallLayer, type VoiceTextController } from "@/components/chat/VoiceCallLayer";
import { ChatMaterialsPanel } from "@/components/chat/ChatMaterialsPanel";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { chatStream, listSessions, loadSession, getUxGreeting, attachLibraryFiles, getWorkspace, voiceStatus } from "@/lib/api";
import type { AttachmentMeta, MaterialSource } from "@/lib/types";
import { gradeFromApi, gradeForApi } from "@/lib/types";
import { containsMathMarkdown } from "@/components/chat/markdown";
import { WS_CHANGED_EVENT, notifySessionChanged } from "@/lib/ws-settings";

function buildSuggestions(lang: Lang) {
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  return [
    { icon: BookOpen, text: tr("suggestion.explain.text"), desc: tr("suggestion.explain") },
    { icon: ClipboardList, text: tr("suggestion.quiz.text"), desc: tr("suggestion.quiz") },
    { icon: Search, text: tr("suggestion.error.text"), desc: tr("suggestion.error") },
    { icon: Target, text: tr("suggestion.plan.text"), desc: tr("suggestion.plan") },
  ];
}

function ChatWorkspace() {
  const { grade, lang, outputLanguage, toggleSidebar } = useUIStore();
  const chat = useChatStore();
  const router = useRouter();
  const scrollRef = useRef<HTMLDivElement>(null);
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  const suggestions = buildSuggestions(lang);
  const [greeting, setGreeting] = useState("");
  const [workspaceSources, setWorkspaceSources] = useState<MaterialSource[]>([]);
  const [materialsOpen, setMaterialsOpen] = useState(true);
  // P10 语音通话：后端 /voice/status 决定入口显隐（provider off 时整条链路
  // 不存在，不渲染按钮，保持与禁用智能层「无痕降级」的仓库惯例一致）。
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [voiceOpen, setVoiceOpen] = useState(false);
  // 通话期间由 VoiceCallLayer 注册：打字消息改走语音 WS（回复仍 TTS 播报），
  // 停止按钮在语音轮里对应打断播报。挂断/卸载时置 null，回落文字流路径。
  const voiceCtlRef = useRef<VoiceTextController | null>(null);
  useEffect(() => {
    let cancelled = false;
    voiceStatus()
      .then((r) => { if (!cancelled) setVoiceEnabled(!!r.enabled); })
      .catch(() => { if (!cancelled) setVoiceEnabled(false); });
    return () => { cancelled = true; };
  }, []);

  // URL structure: /chat = new chat, /chat/<sessionId> = existing session.
  // Both are the SAME optional-catch-all route, so router.replace() between
  // them only swaps the param — the workspace is never remounted and an
  // in-flight stream survives the URL switch untouched.
  const params = useParams<{ sessionId?: string[] }>();
  const segs = params.sessionId;
  const invalidPath = !!segs && segs.length > 1;
  // useParams may return the segment still percent-encoded (CJK session ids
  // like chat_..._你好 arrive as chat_..._%E4%BD%A0%E5%A5%BD). Decode it so it
  // matches the decoded ids held in the store/loadedRef — otherwise the
  // already-bound guard misses, the effect reloads, loadSession re-encodes the
  // encoded form (double-encoding) and the backend 404s a session that exists.
  const safeDecode = (s: string) => {
    try { return decodeURIComponent(s); } catch { return s; }
  };
  const urlSession = invalidPath ? null : (segs?.[0] ? safeDecode(segs[0]) : null);
  // Which session id failed to load (set from the async catch below, so no
  // synchronous setState in the effect). Derived: shows only while the URL
  // still points at that id — navigating away clears it automatically.
  const [loadErrorFor, setLoadErrorFor] = useState<string | null>(null);
  // P3 渐进加载：首屏只取最近 TAIL_INITIAL 条消息；更早的历史经顶部按钮
  // 按需取全量（长会话首屏不再为全部消息付 markdown+KaTeX 解析成本）。
  const TAIL_INITIAL = 40;
  const [earlierCount, setEarlierCount] = useState(0);
  const [loadingEarlier, setLoadingEarlier] = useState(false);

  // Deep links: /chat/<id> restores a session; /chat?q=<text> prefills the
  // input box (other pages jump here with a draft question). ?send=1 auto-sends
  // the draft instead (action buttons across the app deep-link straight into a
  // running conversation).
  const searchParams = useSearchParams();
  const deepPrefill = searchParams.get("q");
  const deepSend = searchParams.get("send");
  const legacySession = searchParams.get("s");

  // Backward compat: old /chat?s=<id> deep links redirect to /chat/<id>.
  useEffect(() => {
    if (legacySession) router.replace(`/chat/${encodeURIComponent(legacySession)}`);
  }, [legacySession, router]);

  useEffect(() => {
    // Sidebar owns the atomic session/workspace snapshot. Do not race it with
    // a second listSessions call that would briefly render sessions as loose.
    // M8: fetch a personalized greeting for the empty state (resume hint +
    // streak). Best-effort; falls back to the static tagline silently.
    getUxGreeting(lang, grade).then((g) => setGreeting(g.greeting || "")).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const refresh = () => {
      const wsId = sessionStorage.getItem("edu-agent-active-ws");
      if (!wsId) {
        if (!urlSession) setWorkspaceSources([]);
        return;
      }
      getWorkspace(wsId).then((ws) => {
        setWorkspaceSources((ws.knowledge_files || []).filter((s) =>
          s.source_scope === "workspace" || s.source_scope === "workspace_textbook"));
      }).catch(() => undefined);
    };
    refresh();
    window.addEventListener(WS_CHANGED_EVENT, refresh);
    return () => window.removeEventListener(WS_CHANGED_EVENT, refresh);
  }, [urlSession]);

  // The URL is the source of truth for the current session: deep links,
  // sidebar navigation and browser back/forward (popstate) all funnel
  // through this effect.
  const loadedRef = useRef<string | null>(null);
  // Guards the URL replace after the first message binds a session id:
  // `done` and the follow-up `history_saved` both carry it — replace once.
  const boundUrlRef = useRef<string | null>(urlSession);
  // Idempotence guards for the bare-/chat branch + auto-send. StrictMode's
  // dev double-mount re-fires this effect; without handledBareRef the second
  // pass would see st.streaming===true (the auto-send just started) and
  // abort + wipe it. autoSentRef is set synchronously before handleSend.
  const handledBareRef = useRef(false);
  const autoSentRef = useRef(false);
  useEffect(() => {
    if (invalidPath) return;
    if (!urlSession) {
      // Bare /chat is the new-chat page: clear any restored session (and stop
      // its stream) so the empty state shows. Runs once per stay on /chat —
      // a first message sent from here changes the store but not the param,
      // and StrictMode's remount early-returns here instead of killing the
      // in-flight auto-send stream.
      if (handledBareRef.current) return;
      handledBareRef.current = true;
      const st = useChatStore.getState();
      if (st.sessionId || st.streaming) {
        st.aborter?.abort();
        st.newChat();
      }
      setEarlierCount(0);
      loadedRef.current = null;
      boundUrlRef.current = null;
      if (!sessionStorage.getItem("edu-agent-active-ws")) {
        window.setTimeout(() => setWorkspaceSources([]), 0);
      }
      return;
    }
    handledBareRef.current = false; // a real session URL: next bare visit re-inits
    if (loadedRef.current === urlSession) return;
    loadedRef.current = urlSession;
    boundUrlRef.current = urlSession;
    if (useChatStore.getState().sessionId === urlSession) return; // already bound in-page
    // Cancel any in-flight stream before replacing the transcript; the aborted
    // flush is discarded via the generation snapshot in handleSend.
    useChatStore.getState().aborter?.abort();
    loadSession(urlSession, TAIL_INITIAL)
      .then((detail) => {
        useChatStore.getState().loadFull(detail.messages || [], detail.knowledge_files || [], urlSession);
        const total = detail.message_total ?? (detail.messages || []).length;
        setEarlierCount(Math.max(0, total - (detail.messages || []).length));
        setWorkspaceSources((detail.material_sources || []).filter((s) =>
          s.source_scope === "workspace" || s.source_scope === "workspace_textbook"));
        useUIStore.getState().setGrade(gradeFromApi(detail.grade) as never);
        if (detail.workspace_id) {
          sessionStorage.setItem("edu-agent-active-ws", detail.workspace_id);
          getWorkspace(detail.workspace_id).then((ws) => {
            setWorkspaceSources((ws.knowledge_files || []).filter((s) =>
              s.source_scope === "workspace" || s.source_scope === "workspace_textbook"));
          }).catch(() => undefined);
        } else {
          sessionStorage.removeItem("edu-agent-active-ws");
          setWorkspaceSources([]);
        }
        // P3 学段跟随教材：会话学段为「自动」时，若恰好选了一本有 level 的教材，
        // 把学段选择器预填为该教材 level（用户可改；多本/level 空则不预填）。
        const sessionGrade = gradeFromApi(detail.grade);
        const fileIds = (detail.knowledge_files || []).map((f: { id: string }) => f.id);
        if (sessionGrade === "自动" && fileIds.length > 0) {
          import("@/lib/api").then(({ getTextbooks }) =>
            getTextbooks().then((tbs) => {
              const matched = tbs.filter((tb) => fileIds.includes(tb.file_id) && tb.level);
              if (matched.length === 1) {
                useUIStore.getState().setGrade(matched[0].level as never);
              }
            }).catch(() => undefined),
          ).catch(() => undefined);
        }
      })
      .catch(() => setLoadErrorFor(urlSession));
  }, [urlSession, invalidPath]);

  // P3：按需取回更早的历史（tail 首屏只装了最近 TAIL_INITIAL 条）。
  const handleLoadEarlier = useCallback(async () => {
    if (!urlSession || loadingEarlier) return;
    setLoadingEarlier(true);
    try {
      const full = await loadSession(urlSession);
      const st = useChatStore.getState();
      // 会话可能已被切走：仅当仍指向同一会话时才替换消息。
      if (st.sessionId === urlSession) st.setMessages(full.messages || []);
      setEarlierCount(0);
    } catch { /* 保持现状，可重试 */ } finally { setLoadingEarlier(false); }
  }, [urlSession, loadingEarlier]);

  // Follow-scroll: only auto-scroll while the user is pinned near the bottom.
  // Streaming updates scroll instantly ("auto"); smooth scrolling is reserved
  // for explicit actions (send / session switch) — a per-token smooth scrollTo
  // queue was a major source of the streaming jank.
  const pinnedRef = useRef(true);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !pinnedRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [chat.messages, chat.pendingAnswer, chat.pendingThinking, chat.activeTool, chat.toolProgress, chat.retry]);

  const handleSend = useCallback(async (message: string, attachments?: AttachmentMeta[]) => {
    // 通话中的打字轮次走语音 WS：用户消息由 stt_result 回显（onTurnBegin）
    // 写入消息流，回复经 answer_delta/turn_end 落定——与按住说话完全同构。
    if (voiceOpen && voiceCtlRef.current && !chat.streaming) {
      pinnedRef.current = true;
      voiceCtlRef.current.sendText(message);
      return;
    }
    chat.setStreaming(true);
    chat.resetPending();
    // Read messages fresh from the store (not the render closure): the
    // ?send=1 auto-send effect fires in the same commit where bare-/chat
    // newChat() clears a restored session — a stale closure would resurrect
    // the old transcript under the new conversation.
    chat.setMessages([...useChatStore.getState().messages, { role: "user" as const, content: message, attachments }]);
    pinnedRef.current = true;
    const ac = new AbortController();
    chat.setAborter(ac);
    // Session-switch guard: newChat/loadFull bump `generation`, so any store
    // write from this loop after a switch (abort flush, done, errors, URL
    // binding) is dropped instead of leaking into the new session.
    const gen0 = useChatStore.getState().generation;
    const sameGeneration = () => useChatStore.getState().generation === gen0;

    // After the first message the backend eagerly assigns a session_id (done /
    // history_saved events): bind it to the store and swap the URL to
    // /chat/<id> with replace() — same route, so no remount, no reload, and
    // the stream keeps rendering undisturbed.
    const bindSessionUrl = (sid: string) => {
      chat.setSessionId(sid);
      loadedRef.current = sid;
      if (boundUrlRef.current !== sid) {
        boundUrlRef.current = sid;
        router.replace(`/chat/${encodeURIComponent(sid)}`, { scroll: false });
      }
    };

    let thinkingAccum = "";
    let answerAccum = "";
    const toolCallsAccum: { name: string; result?: unknown }[] = [];

    // Throttled flush: SSE tokens accumulate in locals and are pushed to the
    // store at most every 50ms, so React re-renders ~20x/s instead of once
    // per token (mainstream chat-agent pattern).
    let flushTimer: ReturnType<typeof setTimeout> | null = null;
    const flush = () => {
      if (flushTimer !== null) {
        clearTimeout(flushTimer);
        flushTimer = null;
      }
      useChatStore.getState().flushPending(thinkingAccum, answerAccum);
    };
    const scheduleFlush = () => {
      if (flushTimer !== null) return;
      flushTimer = setTimeout(() => {
        flushTimer = null;
        useChatStore.getState().flushPending(thinkingAccum, answerAccum);
      }, 50);
    };

    try {
      // Flush library references picked before the first message (e.g. the user
      // clicked a suggestion card instead of the send button): the backend
      // copies them into the — possibly brand-new — session so RAG sees them.
      const pendingRefs = useChatStore.getState().pendingLibraryRefs;
      if (pendingRefs.length > 0) {
        try {
          const sid = useChatStore.getState().sessionId;
          const ws = sid ? null : sessionStorage.getItem("edu-agent-active-ws");
          const res = await attachLibraryFiles(sid || "new", pendingRefs.map((r) => r.id), ws);
          chat.setSessionId(res.session_id);
          chat.addFiles(res.results);
          chat.setPendingLibraryRefs([]);
        } catch { /* best-effort: the message still sends without the references */ }
      }
      // Pass workspace_id when starting a new chat inside a workspace.
      // NOTE: read sessionId fresh — the flush above may have just created the
      // session, and the closure's chat.sessionId would then be stale (the
      // stream must target that session or the references are lost).
      const freshSid = useChatStore.getState().sessionId;
      const activeWs = freshSid ? null : sessionStorage.getItem("edu-agent-active-ws");
      const stream = chatStream({ message, session_id: freshSid, workspace_id: activeWs, grade: gradeForApi(grade), lang, output_language: outputLanguage === "auto" ? null : outputLanguage, attachments }, ac.signal);
      for await (const ev of stream) {
        switch (ev.type) {
          case "thinking":
            thinkingAccum += ev.content as string;
            scheduleFlush();
            break;
          case "answer":
            answerAccum += ev.content as string;
            scheduleFlush();
            break;
          case "step":
            chat.setCurrentStep(ev.step as string);
            chat.setHeartbeatElapsed(0);
            break;
          case "tool_start":
            chat.addToolStart(ev.name as string);
            toolCallsAccum.push({ name: ev.name as string });
            break;
          case "tool_progress":
            chat.addToolProgress(ev.message as string);
            break;
          case "tool_result":
            if (toolCallsAccum.length > 0) toolCallsAccum[toolCallsAccum.length - 1].result = ev.result;
            chat.setToolResult(ev.result);
            break;
          case "tool_warning":
            chat.addToolProgress(`⚠ ${ev.warning}`);
            break;
          case "heartbeat":
            chat.setHeartbeatElapsed(ev.elapsed as number);
            break;
          case "retry":
            chat.setRetry({ attempt: ev.attempt as number, reason: ev.reason as string, visible: true });
            break;
          case "done":
            flush();
            if (sameGeneration()) {
              chat.commitAssistant();
              if (ev.session_id) bindSessionUrl(ev.session_id as string);
            }
            chat.setStreaming(false);
            chat.setRetry(null);
            listSessions().then((r) => {
              chat.setSessions(r.sessions);
              notifySessionChanged();
            });
            // Do NOT return here: the backend emits a follow-up history_saved
            // event carrying the session_id. Returning early used to skip it,
            // so sessionId never bound and every turn started a fresh session
            // (multi-turn memory was lost). Fall through to drain it.
            break;
          case "error":
            flush();
            if (sameGeneration()) {
              chat.setMessages([...useChatStore.getState().messages, {
                role: "assistant", content: `**${tr("chat.error.connect")}**\n\n${ev.message}`, thinking: "",
              }]);
            }
            chat.setStreaming(false);
            chat.setRetry(null);
            return;
          case "history_saved":
            if (sameGeneration()) {
              if (ev.session_id) bindSessionUrl(ev.session_id as string);
              // Workspace binding consumed — clear the flag so resumed sessions
              // don't re-send workspace_id (the session file already stores it).
              sessionStorage.removeItem("edu-agent-active-ws");
            }
            break;
        }
      }
    } catch (e) {
      const aborted = ac.signal.aborted || (e instanceof DOMException && e.name === "AbortError");
      if (aborted) {
        // Manual stop commits the partial answer; an abort caused by a session
        // switch (generation bumped) discards it instead.
        if (sameGeneration() && (answerAccum || thinkingAccum || toolCallsAccum.length > 0)) {
          chat.setMessages([...useChatStore.getState().messages, {
            role: "assistant", content: answerAccum || thinkingAccum || "(已中断)",
            thinking: thinkingAccum, toolCalls: toolCallsAccum as never,
          }]);
        }
      } else if (sameGeneration()) {
        chat.setMessages([...useChatStore.getState().messages, {
          role: "assistant", content: `**${tr("chat.error.interrupted")}**\n\n${(e as Error).message}`, thinking: "",
        }]);
      }
    } finally {
      if (flushTimer !== null) {
        clearTimeout(flushTimer);
        flushTimer = null;
      }
      if (useChatStore.getState().aborter === ac) chat.setAborter(null);
      chat.setStreaming(false);
      chat.setRetry(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.messages, chat.sessionId, grade, voiceOpen]);

  // Auto-send deep link (?q=...&send=1 on bare /chat): fire the message
  // straight away, then strip the params with history.replaceState (NOT
  // router.replace — that would re-trigger Next navigation) so a refresh
  // can't resend. Runs after the URL-sync effect above (newChat first);
  // autoSentRef is set synchronously and handledBareRef makes the URL-sync
  // effect idempotent, so StrictMode's double-mount can neither resend nor
  // abort the stream this starts.
  useEffect(() => {
    if (deepSend !== "1" || !deepPrefill || urlSession || invalidPath) return;
    if (autoSentRef.current) return;
    autoSentRef.current = true;
    void handleSend(deepPrefill);
    window.history.replaceState(null, "", "/chat");
  }, [deepSend, deepPrefill, urlSession, invalidPath, handleSend]);

  const handleStop = () => {
    // 语音轮次没有 aborter：停止按钮对应打断 TTS 播报（barge-in）。
    if (voiceOpen && voiceCtlRef.current && !useChatStore.getState().aborter) {
      voiceCtlRef.current.stopSpeaking();
      return;
    }
    useChatStore.getState().aborter?.abort();
  };

  const handleRegenerate = useCallback(() => {
    const msgs = useChatStore.getState().messages;
    let lastAssistantIdx = -1;
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === "assistant") { lastAssistantIdx = i; break; }
    }
    if (lastAssistantIdx < 1) return;
    const userMsg = msgs[lastAssistantIdx - 1];
    if (!userMsg || userMsg.role !== "user") return;
    const trimmed = msgs.slice(0, lastAssistantIdx);
    useChatStore.getState().setMessages(trimmed);
    void handleSend(userMsg.content, userMsg.attachments);
  }, [handleSend]);

  const isEmpty = chat.messages.length === 0 && !chat.streaming;
  const materialSources: MaterialSource[] = [
    ...workspaceSources,
    ...chat.files.map((file) => ({
      ...file,
      source_scope: file.source_scope || (file.library_file_id ? "library" : "session"),
      source_visibility: file.source_visibility || "session_private",
    })),
  ].filter((item, index, all) => all.findIndex((x) => x.id === item.id) === index);

  // Invalid or foreign session id: show a not-found state with a way back to
  // a fresh chat (loadSession rejects for both 404 and non-owned sessions).
  if (invalidPath || (urlSession !== null && loadErrorFor === urlSession)) {
    return (
      <div className="flex h-full overflow-hidden">
        <Sidebar />
        <div className="flex min-w-0 flex-1 items-center justify-center p-6">
          <EmptyState
            icon={<MessageSquareOff size={28} />}
            title={tr("chat.notfound.title")}
            desc={tr("chat.notfound.desc")}
            action={
              <Button
                icon={<Plus size={14} />}
                onClick={() => { useChatStore.getState().newChat(); router.replace("/chat"); }}
              >
                {tr("chat.notfound.back")}
              </Button>
            }
          />
        </div>
      </div>
    );
  }

  return (
    <div className="relative flex h-full overflow-hidden">
      <Sidebar />
      <div className="relative flex min-w-0 flex-1 flex-col">
        <button
          onClick={toggleSidebar}
          aria-label="toggle session rail"
          className="absolute left-2 top-2 z-10 flex h-7 w-7 cursor-pointer items-center justify-center rounded-[8px] text-muted transition-colors hover:bg-surface-hover hover:text-fg"
        >
          <Menu size={16} />
        </button>
        {/* 右上角工具组：资料栏开关 + 电话（语音通话）。电话占最右侧；
            通话中入口隐藏，由本角落（按钮行正下方）放大的手机模拟指示
            通话状态，黑板不再与其同行。 */}
        <div className="absolute right-3 top-2 z-10 flex items-center gap-1">
          {!materialsOpen && (
            <button
              onClick={() => setMaterialsOpen(true)}
              aria-label={tr("chat.materials.open", "打开资料栏")}
              className="flex h-7 w-7 items-center justify-center rounded-[8px] text-muted transition-colors hover:bg-surface-hover hover:text-fg"
            >
              <PanelRight size={16} />
            </button>
          )}
          {voiceEnabled && !voiceOpen && (
            <button
              onClick={() => setVoiceOpen(true)}
              disabled={chat.streaming}
              title={tr("chat.voice.call.title")}
              aria-label={tr("chat.voice.call.title")}
              className="group flex w-16 shrink-0 flex-col items-center gap-0.5 rounded-[10px] border border-accent/25 bg-accent-soft/40 px-1 pb-1 pt-1.5 text-accent-strong shadow-sm transition-all hover:border-accent/50 hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Phone size={19} className="transition-transform duration-200 group-hover:-rotate-12" />
              <span className="text-center text-[0.55rem] font-medium leading-tight tracking-tight">
                {tr("chat.voice.call.cta")}
              </span>
            </button>
          )}
        </div>
        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          {isEmpty ? (
            <div className="flex min-h-full flex-col items-center justify-center px-6 py-10">
              <div className="page-in mb-5 flex h-14 w-14 items-center justify-center rounded-[14px] bg-accent text-white shadow-md">
                <GraduationCap className="h-7 w-7" />
              </div>
              <h1 className="page-in mb-2 font-serif text-[1.6rem] font-bold tracking-tight text-fg" style={{ animationDelay: "90ms" }}>{tr("app.name")}</h1>
              <p className="page-in mb-6 max-w-md text-center text-[0.85rem] leading-relaxed text-muted" style={{ animationDelay: "90ms" }}>
                {tr("app.tagline")}
              </p>
              {greeting && (
                <p className="page-in mb-6 max-w-md rounded-[10px] border border-accent/20 bg-accent-soft/30 px-4 py-2 text-center text-[0.78rem] leading-relaxed text-accent-strong" style={{ animationDelay: "180ms" }}>
                  {greeting}
                </p>
              )}
              <div className="page-in grid w-full max-w-xl grid-cols-1 gap-2.5 sm:grid-cols-2" style={{ animationDelay: "270ms" }}>
                {suggestions.map((s) => (
                  <Card
                    key={s.text}
                    hover
                    onClick={() => handleSend(s.text)}
                    className="group px-4 py-3 hover:border-accent/40"
                  >
                    <div className="flex items-start gap-3">
                      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] bg-accent-soft text-accent">
                        <s.icon className="h-4 w-4" />
                      </div>
                      <div className="min-w-0">
                        <p className="text-[0.8rem] font-medium text-fg">{s.text}</p>
                        <p className="mt-0.5 text-[0.68rem] text-muted/80">{s.desc}</p>
                      </div>
                      <ArrowRight className="ml-auto h-3.5 w-3.5 shrink-0 text-muted/30 transition-all group-hover:translate-x-0.5 group-hover:text-accent" />
                    </div>
                  </Card>
                ))}
              </div>
            </div>
          ) : (
            <div className="mx-auto max-w-[820px] px-4 pb-4 pt-2">
              {earlierCount > 0 && (
                <div className="flex justify-center py-2">
                  <button
                    onClick={() => void handleLoadEarlier()}
                    disabled={loadingEarlier}
                    className="rounded-full border border-border bg-surface px-3 py-1.5 text-xs text-fg-secondary transition-colors hover:border-accent hover:text-accent disabled:opacity-60"
                  >
                    {loadingEarlier
                      ? tr("chat.load.earlier.loading")
                      : `${tr("chat.load.earlier")}（${earlierCount}）`}
                  </button>
                </div>
              )}
              {chat.messages.map((m, i) => (
                <div key={i} className={containsMathMarkdown(m.content) ? undefined : "msg-cv"}>
                  <ChatMessage msg={m}
                    disabled={chat.streaming}
                    onRegenerate={i === chat.messages.length - 1 && m.role === "assistant" ? handleRegenerate : undefined}
                  />
                </div>
              ))}
              {chat.streaming && (
                <StreamingMessage
                  thinking={chat.pendingThinking}
                  answer={chat.pendingAnswer}
                  activeTool={chat.activeTool}
                  toolProgress={chat.toolProgress}
                  toolCalls={chat.pendingToolCalls}
                  currentStep={chat.currentStep}
                  heartbeatElapsed={chat.heartbeatElapsed}
                  retry={chat.retry}
                />
              )}
            </div>
          )}
        </div>

        {/* 通话期间输入框保持可用（草稿、附件逻辑不变）：控制条渲染在其
            上方，打字消息经语音 WS 发出，老师的回复仍以 TTS 播报。 */}
        {voiceOpen && (
          <VoiceCallLayer
            lang={lang}
            sessionId={chat.sessionId}
            workspaceId={chat.sessionId ? null : sessionStorage.getItem("edu-agent-active-ws")}
            onClose={() => setVoiceOpen(false)}
            onRegisterController={(ctl) => { voiceCtlRef.current = ctl; }}
          />
        )}
        <ChatInput onSend={handleSend} disabled={chat.streaming} onStop={handleStop}
          prefill={deepSend === "1" && !urlSession ? null : deepPrefill} />
      </div>
      {materialsOpen && (
        <ChatMaterialsPanel sources={materialSources} open onClose={() => setMaterialsOpen(false)} />
      )}
    </div>
  );
}

export default function ChatPage() {
  // useSearchParams requires a Suspense boundary in Next.js.
  return (
    <Suspense fallback={<div className="h-full w-full bg-bg" />}>
      <ChatWorkspace />
    </Suspense>
  );
}
