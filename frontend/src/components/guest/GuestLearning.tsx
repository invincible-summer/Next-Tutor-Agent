"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { LoaderCircle, MessageSquareText, RefreshCw, Send, Square } from "lucide-react";
import { API_BASE, chatStream } from "@/lib/api";
import { apiFetch } from "@/lib/api-fetch";
import { endGuestSession, ensureGuestSession } from "@/lib/guest-session";
import { useUIStore } from "@/lib/store";
import { gradeForApi, type ChatMessage as Message, type Grade, type QuizQuestion, type ToolResultData } from "@/lib/types";
import { GRADE_LABELS } from "@/lib/i18n";
import { makePageT } from "@/lib/i18n-page";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field, FIELD_CLS, Input, Textarea } from "@/components/ui/Input";
import { ChatMessage } from "@/components/chat/ChatMessage";
import { QuizQuestionCard } from "@/components/chat/QuizCard";
import { STRINGS } from "./strings";

type Book = { id: string; name: string; grade: string };

export function GuestLearning({ mode }: { mode: "chat" | "practice" }) {
  const { lang, theme, toggleTheme, setLang, grade: defaultGrade } = useUIStore();
  const tr = makePageT(lang, STRINGS);
  const [books, setBooks] = useState<Book[]>([]);
  const [book, setBook] = useState("");
  const [booksFailed, setBooksFailed] = useState(false);
  const [grade, setGrade] = useState<Grade>(defaultGrade);
  const [text, setText] = useState("");
  const [topic, setTopic] = useState("");
  const [difficulty, setDifficulty] = useState("medium");
  const [count, setCount] = useState("3");
  const [qType, setQType] = useState("");
  const [questions, setQuestions] = useState<QuizQuestion[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [reply, setReply] = useState<Message | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const abort = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const scroll = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    void ensureGuestSession(API_BASE).then(() => apiFetch(`${API_BASE}/guest/textbooks`))
      .then(async (res) => {
        if (!res.ok) throw new Error("books_unavailable");
        const data = await res.json() as { items: Book[] };
        if (alive) setBooks(data.items);
      }).catch(() => { if (alive) setBooksFailed(true); });
    const ended = () => {
      generation.current += 1;
      abort.current?.abort();
      setMessages([]); setReply(null); setQuestions([]); setText(""); setTopic("");
      setSessionId(null); setBusy(false); setNotice("guest.ended");
    };
    window.addEventListener("edu-guest-ended", ended);
    return () => {
      alive = false;
      generation.current += 1;
      abort.current?.abort();
      window.removeEventListener("edu-guest-ended", ended);
    };
  }, []);

  useEffect(() => { scroll.current?.scrollTo({ top: scroll.current.scrollHeight }); }, [messages, reply]);

  const startAgain = () => {
    endGuestSession();
    generation.current += 1;
    abort.current?.abort();
    setMessages([]); setQuestions([]); setReply(null); setSessionId(null);
    setText(""); setTopic(""); setNotice(""); setBusy(false);
  };

  async function send() {
    const message = text.trim();
    if (!message || busy) return;
    const expected = generation.current;
    const controller = new AbortController();
    abort.current = controller;
    setBusy(true); setNotice(""); setText("");
    setMessages((old) => [...old, { role: "user", content: message }]);
    let answer: Message = { role: "assistant", content: "", toolCalls: [] };
    setReply(answer);
    try {
      await ensureGuestSession(API_BASE);
      for await (const ev of chatStream({ message, session_id: sessionId, grade: gradeForApi(grade),
        lang, public_textbook_ids: book ? [book] : [] }, controller.signal)) {
        if (expected !== generation.current) return;
        if (ev.type === "answer") answer = { ...answer, content: answer.content + String(ev.content || "") };
        if (ev.type === "tool_result") {
          const result = ev.result as ToolResultData;
          answer = { ...answer, toolCalls: [...(answer.toolCalls || []), { name: result.tool, result }] };
        }
        if (ev.type === "done") {
          if (ev.session_id) setSessionId(String(ev.session_id));
          answer = { ...answer, content: String(ev.answer || answer.content) };
        }
        if (ev.type === "error") {
          if (ev.code === "guest_session_expired") { endGuestSession(); return; }
          throw new Error("chat_failed");
        }
        setReply(answer);
      }
    } catch (e) {
      if (expected === generation.current && !(e instanceof Error && e.name === "AbortError")) setNotice("guest.failed");
    } finally {
      if (expected === generation.current) {
        if (answer.content || answer.toolCalls?.length) setMessages((old) => [...old, answer]);
        setReply(null); setBusy(false);
      }
    }
  }

  async function generate() {
    if (busy || !topic.trim()) return;
    const expected = generation.current;
    const controller = new AbortController();
    abort.current = controller;
    setBusy(true); setNotice(""); setQuestions([]);
    try {
      await ensureGuestSession(API_BASE);
      const res = await apiFetch(`${API_BASE}/guest/quiz/generate`, {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
        body: JSON.stringify({ topic: topic.trim(), grade: gradeForApi(grade), difficulty, count: Number(count), lang,
          ...(qType ? { q_type: qType } : {}), public_textbook_ids: book ? [book] : [] }),
      });
      if (!res.ok) throw new Error("generation_failed");
      const data = await res.json() as { questions: QuizQuestion[] };
      if (expected === generation.current) setQuestions(data.questions);
    } catch (e) {
      if (expected === generation.current && !(e instanceof Error && e.name === "AbortError")) setNotice("guest.failed");
    } finally { if (expected === generation.current) setBusy(false); }
  }

  const selections = <div className="grid grid-cols-[minmax(0,1fr)_180px] gap-3">
    <Field label={tr("guest.book")}><select className={FIELD_CLS} value={book} disabled={busy} onChange={(e) => setBook(e.target.value)} aria-label={tr("guest.book")}>
      <option value="">{tr("guest.noBook")}</option>
      {books.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
    </select></Field>
    <Field label={tr("guest.grade")}><select className={FIELD_CLS} value={grade} disabled={busy} onChange={(e) => setGrade(e.target.value as Grade)} aria-label={tr("guest.grade")}>
      {GRADE_LABELS[lang].map((g) => <option key={g.token} value={g.token}>{g.label}</option>)}
    </select></Field>
  </div>;

  return <div className="mx-auto flex h-full w-full max-w-[980px] flex-col gap-4 p-6" data-testid={`guest-${mode}`}>
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h2 className="font-serif text-xl font-semibold text-fg">{tr(mode === "chat" ? "guest.chat" : "guest.practice")}</h2>
        <p className="mt-1 text-xs leading-5 text-muted">{tr("guest.notice")}</p>
        <Link className="mt-1 inline-block text-xs text-accent hover:underline" href={`/login?redirect=${mode === "chat" ? "/chat" : "/assessment"}`}>{tr("guest.login")}</Link></div>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" onClick={() => setLang(lang === "zh" ? "en" : "zh")}>{lang === "zh" ? "EN" : "中文"}</Button>
        <Button size="sm" variant="outline" aria-label={lang === "en" ? "Toggle theme" : "切换主题"} onClick={toggleTheme}>{theme === "dark" ? "☀" : "☾"}</Button>
        <Button size="sm" variant="outline" icon={<RefreshCw size={13} />} onClick={startAgain}>{tr("guest.new")}</Button>
      </div>
    </div>
    {booksFailed && <p className="text-xs text-muted">{tr("guest.booksFail")}</p>}
    {notice && <p role="status" className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs text-fg">{tr(notice)}</p>}
    {mode === "chat" ? <>
      {selections}
      <div ref={scroll} className="min-h-0 flex-1 overflow-y-auto">
        {!messages.length && !reply && <div className="flex h-full flex-col items-center justify-center gap-3 text-center text-muted"><MessageSquareText size={30} /><p className="text-sm">{tr("guest.empty")}</p></div>}
        {messages.map((m, i) => <ChatMessage key={i} msg={m} disabled={busy} />)}
        {reply && <ChatMessage msg={{ ...reply, content: reply.content || tr("guest.thinking") }} disabled />}
      </div>
      <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <Textarea aria-label={tr("guest.input")} placeholder={tr("guest.inputHint")} value={text} disabled={busy} maxLength={16000} rows={3} onChange={(e) => setText(e.target.value)} />
        <div className="flex justify-end">{busy ? <Button type="button" variant="outline" icon={<Square size={13} />} onClick={() => abort.current?.abort()}>{tr("guest.stop")}</Button>
          : <Button type="submit" icon={<Send size={13} />} disabled={!text.trim()}>{tr("guest.send")}</Button>}</div>
      </form>
    </> : <div className="min-h-0 flex-1 space-y-4 overflow-y-auto">
      <Card><form className="space-y-4" onSubmit={(e) => { e.preventDefault(); void generate(); }}>
        {selections}
        <Field label={tr("guest.topic")}><Input required maxLength={600} value={topic} disabled={busy} aria-label={tr("guest.topic")} placeholder={tr("guest.topicHint")} onChange={(e) => setTopic(e.target.value)} /></Field>
        <div className="grid grid-cols-3 gap-3">
          <Field label={tr("guest.difficulty")}><select className={FIELD_CLS} value={difficulty} disabled={busy} aria-label={tr("guest.difficulty")} onChange={(e) => setDifficulty(e.target.value)}>
            {["easy", "medium", "hard"].map((d) => <option key={d} value={d}>{tr(`guest.${d}`)}</option>)}</select></Field>
          <Field label={tr("guest.count")}><Input type="number" required min={1} max={5} value={count} disabled={busy} aria-label={tr("guest.count")} onChange={(e) => setCount(e.target.value)} /></Field>
          <Field label={tr("guest.type")}><select className={FIELD_CLS} value={qType} disabled={busy} aria-label={tr("guest.type")} onChange={(e) => setQType(e.target.value)}>
            <option value="">{tr("guest.autoType")}</option><option value="multiple_choice">{tr("guest.mc")}</option><option value="fill_blank">{tr("guest.fill")}</option><option value="short_answer">{tr("guest.open")}</option></select></Field>
        </div>
        <Button type="submit" disabled={busy || !topic.trim()} icon={busy ? <LoaderCircle size={14} className="animate-spin" /> : undefined}>{tr(busy ? "guest.generating" : "guest.generate")}</Button>
      </form></Card>
      {questions.map((q, i) => <QuizQuestionCard key={q.question_id || i} question={q} index={i} />)}
    </div>}
  </div>;
}
