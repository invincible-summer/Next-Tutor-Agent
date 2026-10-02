"use client";
import { useRef, useState, type DragEvent } from "react";
import { Loader2, Upload } from "lucide-react";
import { cn } from "@/lib/cn";

/** 拖拽上传卡：虚线边框，支持多选；仅在学习区选中时挂载使用。 */
export function UploadZone({
  uploading,
  tr,
  onFiles,
}: {
  uploading: boolean;
  tr: (key: string, fallback?: string) => string;
  onFiles: (files: File[]) => void;
}) {
  const [drag, setDrag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDrag(false);
    if (uploading) return;
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) onFiles(files);
  };

  return (
    <div
      onClick={() => !uploading && inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        if (!uploading) setDrag(true);
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={handleDrop}
      className={cn(
        "group flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-[10px] border border-dashed px-4 py-7 text-center transition-colors",
        drag ? "border-accent bg-accent-soft/50" : "border-border hover:border-accent/60 hover:bg-accent-soft/20",
        uploading && "cursor-wait opacity-80",
      )}
    >
      {uploading ? (
        <>
          <Loader2 size={18} className="animate-spin text-accent" />
          <div className="text-sm text-fg-secondary">{tr("res.uploading")}</div>
        </>
      ) : (
        <>
          <Upload size={18} className={cn("transition-transform group-hover:-translate-y-0.5", drag ? "text-accent" : "text-muted")} />
          <div className="text-sm text-fg-secondary">{tr("res.upload.drop")}</div>
          <div className="text-[11px] text-muted">{tr("res.upload.hint")}</div>
        </>
      )}
      <input
        ref={inputRef}
        type="file"
        multiple
        hidden
        accept=".pdf,.docx,.pptx,.txt,.md,.markdown"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          if (files.length > 0) onFiles(files);
          e.target.value = "";
        }}
      />
    </div>
  );
}
