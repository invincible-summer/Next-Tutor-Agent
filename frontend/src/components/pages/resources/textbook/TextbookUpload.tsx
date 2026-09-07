"use client";
import { useRef, useState, type DragEvent } from "react";
import { Loader2, BookOpen, Globe2 } from "lucide-react";
import { cn } from "@/lib/cn";
import { useUIStore } from "@/lib/store";
import { Button } from "@/components/ui/Button";
import { Input, FIELD_CLS, LABEL_CLS } from "@/components/ui/Input";

/** 教材学段五值（与后端 TEXTBOOK_LEVELS 一致）。 */
const LEVELS = ["小学", "初中", "高中", "本科", "其他"] as const;

export interface TextbookUploadOpts {
  level: string;
  scope: string;
  subject: string;
  /** 教材组名（可选）：非空则本次全部文件编为一组，统一构建知识谱系。 */
  group: string;
  groupNote: string;
  defaultMaxChapters: number | null;
  defaultMaxConcepts: number | null;
  volumeOverrides: Record<string, { max_chapters: number | null; max_concepts: number | null }>;
}

/** 教材上传区：强调「支持大 PDF 教材」，调 POST /textbooks/upload。
 *  P6-A3：必选学段（图谱按学段分组）；P6-B：管理员可上传到公用教材库；
 *  教材组：多卷 PDF（上下册/力学光学电磁学分册）可编组统一建图谱。 */
export function TextbookUpload({
  uploading,
  tr,
  isAdmin = false,
  onFiles,
}: {
  uploading: boolean;
  tr: (key: string, fallback?: string) => string;
  isAdmin?: boolean;
  onFiles: (files: File[], opts: TextbookUploadOpts) => void;
}) {
  const [drag, setDrag] = useState(false);
  const [level, setLevel] = useState<string>("其他");
  const [isPublic, setIsPublic] = useState(false);
  const [subject, setSubject] = useState("");
  const [group, setGroup] = useState("");
  const [groupNote, setGroupNote] = useState("");
  const [maxChapters, setMaxChapters] = useState("");
  const [maxConcepts, setMaxConcepts] = useState("");
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [overrides, setOverrides] = useState<Record<string, { max_chapters: string; max_concepts: string }>>({});
  const inputRef = useRef<HTMLInputElement>(null);
  const lang = useUIStore((s) => s.lang);

  const levelLabel = (lv: string) =>
    lang === "en" ? ({ 小学: "Primary", 初中: "Middle", 高中: "High", 本科: "College", 其他: "Other" } as Record<string, string>)[lv] ?? lv : lv;

  const selectFiles = (files: File[]) => {
    if (files.length > 0) setPendingFiles(files);
  };

  const emit = () => {
    if (pendingFiles.length > 0) {
      const volumeOverrides = Object.fromEntries(Object.entries(overrides).map(([index, value]) => [index, {
        max_chapters: value.max_chapters ? Number(value.max_chapters) : null,
        max_concepts: value.max_concepts ? Number(value.max_concepts) : null,
      }]));
      onFiles(pendingFiles, { level, scope: isAdmin && isPublic ? "public" : "private", subject: subject.trim(), group: group.trim(), groupNote: groupNote.trim(),
        defaultMaxChapters: maxChapters ? Number(maxChapters) : null,
        defaultMaxConcepts: maxConcepts ? Number(maxConcepts) : null, volumeOverrides });
      setPendingFiles([]);
      setOverrides({});
    }
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDrag(false);
    if (uploading) return;
    selectFiles(Array.from(e.dataTransfer.files));
  };

  return (
    <div className="flex flex-col gap-4">
      {/* 学段胶囊分段选择 +（管理员）公用库开关 */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className={LABEL_CLS}>{tr("res.tb.upload.level", "教材学段：")}</div>
          <div className="flex w-fit items-center gap-1 rounded-full bg-surface-sunken p-1">
            {LEVELS.map((lv) => (
              <button
                key={lv}
                type="button"
                onClick={() => setLevel(lv)}
                className={cn(
                  "h-7 cursor-pointer rounded-full px-3 text-xs transition-colors",
                  level === lv ? "bg-surface text-accent shadow-sm" : "text-muted hover:text-fg",
                )}
              >
                {levelLabel(lv)}
              </button>
            ))}
          </div>
        </div>
        {isAdmin && (
          <button
            type="button"
            onClick={() => setIsPublic((v) => !v)}
            className={cn(
              "flex h-7 cursor-pointer items-center gap-1.5 rounded-full border px-3 text-xs transition-colors",
              isPublic
                ? "border-accent bg-accent-soft/40 text-accent"
                : "border-border bg-surface text-fg-secondary hover:border-accent hover:text-accent",
            )}
            title={tr("res.tb.upload.publicHint", "公用教材库：所有账号可选用")}
          >
            <Globe2 size={12} />
            {tr("res.tb.upload.public", "上传到公用教材库")}
          </button>
        )}
      </div>

      <div className="grid gap-3 sm:grid-cols-[150px_1fr]">
        <Input
          type="text"
          value={subject}
          maxLength={30}
          onChange={(e) => setSubject(e.target.value)}
          placeholder={tr("res.tb.subject", "学科标签（如物理、数学）")}
        />
        <Input
          type="text"
          value={group}
          maxLength={60}
          onChange={(e) => setGroup(e.target.value)}
          placeholder={tr("res.tb.upload.group", "教材组名（可选）：多卷 PDF 编为一组，统一构建知识谱系")}
        />
      </div>
      <Input
        type="text"
        value={groupNote}
        maxLength={500}
        onChange={(e) => setGroupNote(e.target.value)}
        placeholder={tr("res.tb.group.note.edit", "教材组备注（可选）")}
      />

      <div className="flex flex-wrap items-center gap-2.5 rounded-[10px] border border-border-light bg-surface-sunken/40 px-3 py-2.5 text-xs">
        <span className="font-medium text-fg-secondary">教材组默认容量</span>
        <div className="w-32">
          <input type="number" min={1} value={maxChapters} onChange={(e) => setMaxChapters(e.target.value)} placeholder="章节：不限制"
            className={FIELD_CLS} />
        </div>
        <div className="w-32">
          <input type="number" min={1} value={maxConcepts} onChange={(e) => setMaxConcepts(e.target.value)} placeholder="概念：不限制"
            className={FIELD_CLS} />
        </div>
        <span className="text-[11px] text-muted">留空表示不限制；每本教材独立应用，不共享总预算。</span>
      </div>

      <div
        onClick={() => !uploading && inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          if (!uploading) setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={handleDrop}
        className={cn(
          "flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-[10px] border border-dashed px-4 py-7 text-center transition-colors",
          drag ? "border-accent bg-accent-soft/50" : "border-border hover:border-accent/60 hover:bg-accent-soft/20",
          uploading && "cursor-wait opacity-80",
        )}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".pdf,.docx,.pptx,.txt,.md"
          className="hidden"
          onChange={(e) => {
            selectFiles(Array.from(e.target.files ?? []));
            e.target.value = "";
          }}
        />
        {uploading ? (
          <Loader2 size={20} className="animate-spin text-accent" />
        ) : (
          <BookOpen size={20} className="text-accent" />
        )}
        <div className="text-sm font-medium text-fg">
          {uploading ? tr("res.tb.uploading") : tr("res.tb.upload.drop")}
        </div>
        <div className="max-w-md text-[11px] leading-relaxed text-muted">{tr("res.tb.upload.hint")}</div>
      </div>

      {pendingFiles.length > 0 && (
        <>
          {/* 逐卷容量覆盖：细分隔线列表（留空=使用教材组默认） */}
          <div className="overflow-hidden rounded-[10px] border border-border">
            <div className="border-b border-border-light bg-surface-sunken/40 px-3 py-2 text-xs font-medium text-fg">逐本设置（留空=使用教材组默认）</div>
            <div className="divide-y divide-border-light">
              {pendingFiles.map((file, index) => {
                const value = overrides[String(index)] ?? { max_chapters: "", max_concepts: "" };
                return <div key={`${file.name}-${index}`} className="grid gap-2 px-3 py-2.5 sm:grid-cols-[1fr_120px_120px] sm:items-center">
                  <span className="truncate text-xs text-fg-secondary">{file.name}</span>
                  <input type="number" min={1} value={value.max_chapters} placeholder="章节：继承"
                    onChange={(e) => setOverrides({ ...overrides, [String(index)]: { ...value, max_chapters: e.target.value } })}
                    className={FIELD_CLS} />
                  <input type="number" min={1} value={value.max_concepts} placeholder="概念：继承"
                    onChange={(e) => setOverrides({ ...overrides, [String(index)]: { ...value, max_concepts: e.target.value } })}
                    className={FIELD_CLS} />
                </div>;
              })}
            </div>
          </div>
          <div className="flex flex-col gap-2">
            <Button className="w-full" disabled={uploading} onClick={emit}>开始上传并构建</Button>
            <Button variant="ghost" size="sm" className="self-end" onClick={() => { setPendingFiles([]); setOverrides({}); }}>取消</Button>
          </div>
        </>
      )}
    </div>
  );
}
