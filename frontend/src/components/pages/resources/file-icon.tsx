import { File, FileText, Presentation, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";

/** 按扩展名映射文件类型图标与配色（仅 token 色）。 */
const TYPE_MAP: Record<string, { icon: LucideIcon; cls: string }> = {
  pdf: { icon: FileText, cls: "bg-danger/10 text-danger" },
  doc: { icon: FileText, cls: "bg-info/10 text-info" },
  docx: { icon: FileText, cls: "bg-info/10 text-info" },
  ppt: { icon: Presentation, cls: "bg-warning/10 text-warning" },
  pptx: { icon: Presentation, cls: "bg-warning/10 text-warning" },
  txt: { icon: File, cls: "bg-surface-hover text-muted" },
  md: { icon: File, cls: "bg-surface-hover text-muted" },
};

function extOf(filename: string): string {
  const m = /\.([a-z0-9]+)$/i.exec(filename);
  return (m?.[1] ?? "").toLowerCase();
}

export function FileTypeIcon({ filename, size = 16 }: { filename: string; size?: number }) {
  const { icon: Icon, cls } = TYPE_MAP[extOf(filename)] ?? { icon: File, cls: "bg-surface-hover text-muted" };
  return (
    <div className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px]", cls)}>
      <Icon size={size} />
    </div>
  );
}
