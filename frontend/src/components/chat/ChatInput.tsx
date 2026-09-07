"use client";
import { useRef, useEffect, useState } from "react";
import { ArrowUp, Square, Paperclip, X, FileText, Loader2, GraduationCap, ScanLine, LibraryBig } from "lucide-react";
import { useUIStore, useChatStore } from "@/lib/store";
import { t, GRADE_LABELS } from "@/lib/i18n";
import { uploadFiles, uploadFailures, attachLibraryFiles, patchSession } from "@/lib/api";
import { LibraryPickerModal, type LibraryRefItem } from "./LibraryPickerModal";
import type { AttachmentMeta } from "@/lib/types";
import { gradeForApi } from "@/lib/types";

export function ChatInput({ onSend, disabled, onStop, prefill }: {
  onSend: (message: string, attachments?: AttachmentMeta[]) => void;
  disabled: boolean;
  onStop?: () => void;
  /** Deep-link prefill (/chat?q=...): applied once per distinct value. */
  prefill?: string | null;
}) {
  const [text, setText] = useState("");
  const [pending, setPending] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const imgRef = useRef<HTMLInputElement>(null);
  const [ocrLoading, setOcrLoading] = useState(false);
  const [ocrText, setOcrText] = useState("");
  const [imageAttachments, setImageAttachments] = useState<AttachmentMeta[]>([]);
  const [libRefOpen, setLibRefOpen] = useState(false);
  const [attaching, setAttaching] = useState(false);
  /** 已有会话时立即 attach 成功的资料库引用（静态 chip，随发送清空） */
  const [attachedRefs, setAttachedRefs] = useState<AttachmentMeta[]>([]);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const prefillRef = useRef<string | null>(null);
  const { grade, setGrade, lang } = useUIStore();
  const { sessionId, pendingLibraryRefs, setPendingLibraryRefs } = useChatStore();
  const tr = (k: string, fb?: string) => t(lang, k, fb);
  const grades = GRADE_LABELS[lang];

  useEffect(() => {
    const ta = taRef.current;
    if (ta) { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 160) + "px"; }
  }, [text]);

  // Deep-link prefill: apply once per distinct ?q= value. Deferred via rAF so
  // setState doesn't run synchronously in the effect body.
  useEffect(() => {
    if (!prefill || prefillRef.current === prefill) return;
    prefillRef.current = prefill;
    const id = requestAnimationFrame(() => {
      setText(prefill);
      taRef.current?.focus();
    });
    return () => cancelAnimationFrame(id);
  }, [prefill]);

  const addFilesLocal = (files: FileList | null) => {
    if (!files) return;
    setUploadError(null);
    const accepted = Array.from(files).filter((f) =>
      /\.(pdf|docx|pptx|txt|md|markdown)$/i.test(f.name));
    if (accepted.length === 0) { setUploadError(tr("chat.input.unsupported")); return; }
    setPending((p) => [...p, ...accepted]);
  };

  const handleImageUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const img = files[0];
    if (!/^image\/(png|jpe?g|webp|bmp|tiff)$/i.test(img.type) && !/\.(png|jpe?g|webp|bmp|tiff)$/i.test(img.name)) {
      setUploadError(tr("chat.input.unsupported"));
      return;
    }
    setUploadError(null);
    setOcrLoading(true);
    try {
      const wsId = sessionId ? undefined : sessionStorage.getItem("edu-agent-active-ws");
      const res = await uploadFiles([img], sessionId || undefined, gradeForApi(grade), wsId);
      useChatStore.getState().setSessionId(res.session_id);
      const ok = res.results.filter((r) => !r.error);
      useChatStore.getState().addFiles(ok);
      setImageAttachments((prev) => [...prev, ...ok]);
      const preview = ok[0]?.preview_text?.trim() || "";
      if (preview) {
        setOcrText(preview);
        requestAnimationFrame(() => taRef.current?.focus());
      } else {
        const failures = uploadFailures(res.results);
        if (failures) setUploadError(failures);
      }
    } catch (e) {
      setUploadError("OCR: " + ((e as Error).message || String(e)));
    } finally {
      setOcrLoading(false);
    }
  };

  // 资料库引用确认：已有会话 → 立即 attach（复制进会话，静态 chip 展示）；
  // 新对话（无 sessionId）→ 暂存到 store，首条消息发出前 flush 绑定。
  const handleLibRefConfirm = async (items: LibraryRefItem[]) => {
    setLibRefOpen(false);
    if (items.length === 0) return;
    setUploadError(null);
    if (sessionId) {
      setAttaching(true);
      try {
        const res = await attachLibraryFiles(sessionId, items.map((i) => i.id));
        useChatStore.getState().addFiles(res.results);
        setAttachedRefs((prev) => [...prev, ...res.results]);
        if (res.errors.length > 0) {
          setUploadError(res.errors.map((e) => `${e.filename ?? e.file_id}: ${e.error}`).join("；"));
        }
      } catch {
        setUploadError(tr("chat.libref.attach.error"));
      } finally {
        setAttaching(false);
      }
    } else {
      const cur = useChatStore.getState().pendingLibraryRefs;
      const merged = [...cur];
      for (const it of items) if (!merged.some((m) => m.id === it.id)) merged.push(it);
      setPendingLibraryRefs(merged);
    }
  };

  /** 把暂存的资料库引用绑定到会话（可能随上传刚新建），返回新增资料元数据。 */
  const flushPendingRefs = async (): Promise<AttachmentMeta[]> => {
    const refs = useChatStore.getState().pendingLibraryRefs;
    if (refs.length === 0) return [];
    const sid = useChatStore.getState().sessionId;
    try {
      const wsId = sid ? undefined : sessionStorage.getItem("edu-agent-active-ws");
      const res = await attachLibraryFiles(sid || "new", refs.map((r) => r.id), wsId);
      useChatStore.getState().setSessionId(res.session_id);
      useChatStore.getState().addFiles(res.results);
      setPendingLibraryRefs([]);
      if (res.errors.length > 0) {
        setUploadError(res.errors.map((e) => `${e.filename ?? e.file_id}: ${e.error}`).join("；"));
      }
      return res.results;
    } catch {
      setUploadError(tr("chat.libref.attach.error"));
      return [];
    }
  };

  const handleSubmit = async () => {
    if (disabled) return;
    let uploadedOk = true;
    let uploadedAttachments: AttachmentMeta[] = [];
    if (pending.length > 0) {
      setUploading(true);
      try {
        // New chat started inside a workspace has no sessionId yet — pass the
        // pending workspace binding so the backend creates the session bound
        // to the workspace instead of an orphan that can never be attached.
        const wsId = sessionId ? undefined : sessionStorage.getItem("edu-agent-active-ws");
        const res = await uploadFiles(pending, sessionId || undefined, gradeForApi(grade), wsId);
        uploadedAttachments = res.results.filter((r) => !r.error);
        useChatStore.getState().setSessionId(res.session_id);
        useChatStore.getState().addFiles(uploadedAttachments);
        const failures = uploadFailures(res.results);
        if (failures) setUploadError(failures);
      } catch (e) {
        setUploadError(e instanceof Error ? e.message : String(e));
        uploadedOk = false;
      }
      setUploading(false);
    }
    // 引用资料库（新对话暂存的）在上传建会话后、发消息前绑定，保证首轮 RAG 可见。
    const flushedRefs = await flushPendingRefs();
    const trimmed = text.trim();
    if (!trimmed && !uploadedOk) return;
    // If OCR extracted a problem, prepend it to the user message so
    // the agent has the full problem text as context for fit_quiz.
    const ocrTrimmed = ocrText.trim();
    if (!trimmed && !ocrTrimmed) return;
    const finalMsg = ocrTrimmed
      ? `<ocr_material>${ocrTrimmed}</ocr_material>\n\n${trimmed || "请根据图片内容进行讲解。"}`
      : trimmed;
    const turnAttachments = [
      ...uploadedAttachments,
      ...imageAttachments,
      ...attachedRefs,
      ...flushedRefs,
    ].filter((item, index, all) => all.findIndex((x) => x.id === item.id) === index);
    onSend(finalMsg, turnAttachments.length > 0 ? turnAttachments : undefined);
    setText(""); setPending([]); setOcrText(""); setAttachedRefs([]); setImageAttachments([]);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSubmit(); }
  };

  return (
    <div className="px-4 pb-3 pt-1">
      <div className="mx-auto max-w-[820px]">
        {uploadError && <p className="mb-1.5 text-[0.72rem] text-danger">{uploadError}</p>}

        {/* 悬浮卡片式输入框 */}
        <div className="rounded-[14px] border border-border bg-surface shadow-sm transition-[border-color,box-shadow] duration-200 focus-within:border-accent focus-within:shadow-[0_0_0_3px_rgb(var(--accent)/0.12)]">
          {/* OCR 预览卡：不污染输入框，随消息一并提交 */}
          {ocrLoading && (
            <div className="mx-3 mt-3 flex items-center gap-2 rounded-[8px] border border-accent/25 bg-accent-soft/30 px-3 py-2">
              <Loader2 size={13} className="animate-spin text-accent" />
              <span className="text-[0.72rem] text-fg-secondary">{tr("chat.input.ocr")}</span>
            </div>
          )}
          {ocrText && !ocrLoading && (
            <div className="mx-3 mt-3 rounded-[8px] border border-accent/25 bg-accent-soft/30 px-3 py-2">
              <div className="flex items-center gap-1.5">
                <ScanLine size={12} className="shrink-0 text-accent" />
                <span className="flex-1 text-[0.7rem] font-medium text-accent-strong">{tr("chat.ocr.preview")}</span>
                <button
                  onClick={() => setOcrText("")}
                  className="text-muted transition-colors hover:text-danger"
                  aria-label="clear OCR text"
                >
                  <X size={12} />
                </button>
              </div>
              <p className="mt-1 max-h-20 overflow-y-auto whitespace-pre-wrap text-[0.72rem] leading-relaxed text-fg-secondary">
                {ocrText}
              </p>
            </div>
          )}

          {/* 附件 chips */}
          {pending.length > 0 && (
            <div className="mx-3 mt-3 flex flex-wrap gap-1.5">
              {pending.map((f, i) => (
                <span key={i} className="flex items-center gap-1.5 rounded-full bg-surface-sunken px-2.5 py-1 text-[0.7rem] text-fg-secondary">
                  <FileText size={11} className="text-accent/70" />
                  <span className="max-w-40 truncate">{f.name}</span>
                  <button onClick={() => setPending((p) => p.filter((_, j) => j !== i))} className="text-muted hover:text-danger">
                    <X size={11} />
                  </button>
                </span>
              ))}
            </div>
          )}

          {/* 资料库引用 chips：已 attach 的静态展示，暂存（新对话）的可移除 */}
          {(attachedRefs.length > 0 || pendingLibraryRefs.length > 0) && (
            <div className="mx-3 mt-3 flex flex-wrap gap-1.5">
              {attachedRefs.map((f) => (
                <span key={f.id} className="flex items-center gap-1.5 rounded-full border border-accent/25 bg-accent-soft/30 px-2.5 py-1 text-[0.7rem] text-fg-secondary">
                  <LibraryBig size={11} className="text-accent" />
                  <span className="max-w-40 truncate">{f.filename}</span>
                  <span className="rounded-[4px] bg-accent-soft px-1 text-[0.6rem] text-accent-strong">{tr("chat.libref.tag")}</span>
                </span>
              ))}
              {pendingLibraryRefs.map((f) => (
                <span key={f.id} className="flex items-center gap-1.5 rounded-full border border-accent/25 bg-accent-soft/30 px-2.5 py-1 text-[0.7rem] text-fg-secondary">
                  <LibraryBig size={11} className="text-accent" />
                  <span className="max-w-40 truncate">{f.filename}</span>
                  <span className="rounded-[4px] bg-accent-soft px-1 text-[0.6rem] text-accent-strong">{tr("chat.libref.tag")}</span>
                  <button
                    onClick={() => setPendingLibraryRefs(pendingLibraryRefs.filter((x) => x.id !== f.id))}
                    className="text-muted hover:text-danger"
                  >
                    <X size={11} />
                  </button>
                </span>
              ))}
            </div>
          )}

          <textarea
            ref={taRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={disabled}
            placeholder={tr("chat.input.placeholder")}
            rows={1}
            className="max-h-40 w-full resize-none bg-transparent px-3.5 pb-1 pt-3 text-[0.85rem] leading-relaxed text-fg outline-none placeholder:text-muted"
          />

          {/* 工具行 */}
          <div className="flex items-center gap-1.5 px-2 pb-2 pt-1">
            <div className="flex h-8 items-center gap-1 rounded-full bg-surface-hover px-3">
              <GraduationCap size={13} className="text-muted" />
              <select
                value={grade}
                onChange={(e) => {
                  const next = e.target.value as never;
                  setGrade(next);
                  // P1: 会话内切换学段 → 立即 PATCH 持久化（「自动」转空串）。
                  // 新会话（无 sid）只写 store，首轮发送随 chatStream 落库。
                  if (sessionId) {
                    const apiGrade = next === "自动" ? "" : (next as string);
                    patchSession(sessionId, { grade: apiGrade }).catch(() => undefined);
                  }
                }}
                disabled={disabled}
                className="cursor-pointer border-none bg-transparent pr-0.5 text-[0.72rem] text-fg-secondary outline-none"
              >
                {grades.map((g) => <option key={g.token} value={g.token}>{g.label}</option>)}
              </select>
            </div>
            <button
              onClick={() => fileRef.current?.click()}
              disabled={disabled || uploading}
              className="flex h-8 w-8 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-hover hover:text-accent disabled:opacity-40"
              title={tr("chat.input.upload.title")}
            >
              {uploading ? <Loader2 size={15} className="animate-spin" /> : <Paperclip size={15} />}
            </button>
            <input ref={fileRef} type="file" multiple accept=".pdf,.docx,.pptx,.txt,.md,.markdown"
              className="hidden" onChange={(e) => { addFilesLocal(e.target.files); e.target.value = ""; }} />
            <button
              onClick={() => setLibRefOpen(true)}
              disabled={disabled || attaching}
              className="flex h-8 w-8 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-hover hover:text-accent disabled:opacity-40"
              title={tr("chat.libref.button")}
            >
              {attaching ? <Loader2 size={15} className="animate-spin" /> : <LibraryBig size={15} />}
            </button>
            <button
              onClick={() => imgRef.current?.click()}
              disabled={disabled || ocrLoading}
              className="flex h-8 w-8 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-hover hover:text-accent disabled:opacity-40"
              title={tr("chat.input.ocr")}
            >
              {ocrLoading ? <Loader2 size={15} className="animate-spin" /> : <ScanLine size={15} />}
            </button>
            <input ref={imgRef} type="file" accept="image/png,image/jpeg,image/webp,image/bmp,image/tiff"
              className="hidden" onChange={(e) => { handleImageUpload(e.target.files); e.target.value = ""; }} />

            <div className="ml-auto">
              {disabled && onStop ? (
                <button
                  onClick={onStop}
                  title={tr("chat.stop")}
                  className="flex h-8 w-8 items-center justify-center rounded-[10px] bg-danger text-white shadow-sm transition-opacity hover:opacity-90"
                >
                  <Square size={13} fill="currentColor" />
                </button>
              ) : (
                <button
                  onClick={handleSubmit}
                  disabled={disabled || (!text.trim() && pending.length === 0)}
                  title={tr("chat.send")}
                  className="flex h-8 w-8 items-center justify-center rounded-[10px] bg-accent text-white shadow-sm transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <ArrowUp size={16} />
                </button>
              )}
            </div>
          </div>
        </div>

        <p className="mt-1.5 text-center text-[0.65rem] text-muted/70">{tr("chat.input.hint")}</p>
      </div>

      {libRefOpen && (
        <LibraryPickerModal
          onClose={() => setLibRefOpen(false)}
          onConfirm={handleLibRefConfirm}
        />
      )}
    </div>
  );
}
