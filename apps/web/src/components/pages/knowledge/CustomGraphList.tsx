"use client";
// 图谱管理列表：查看 / 删除。
// P6-A4 起知识谱系只来自教材（topic_key 前缀 tb-），手动构建/重生/回滚已移除；
// 遗留手动图谱保留「查看/删除」用于清理。
import { Eye, Trash2 } from "lucide-react";
import type { CustomGraphMeta } from "@/lib/types-modules";
import { cn } from "@/lib/cn";
import { Badge } from "@/components/ui/Badge";

type Tr = (key: string, fallback?: string) => string;

/** 是否为教材图谱（topic_key 前缀 tb- 或 source 以 textbook: 开头）。 */
function isTextbook(g: CustomGraphMeta): boolean {
  return g.topic_key.startsWith("tb-") || (g.source || "").startsWith("textbook:");
}

/** source 字段 → 人类可读：llm → 「LLM 生成」；material:/textbook: → 「教材: xxx」。 */
function sourceLabel(source: string, tr: Tr): string {
  if (source === "llm") return tr("custom.source.llm");
  if (source.startsWith("material:")) return `${tr("custom.source.material")}: ${source.slice("material:".length)}`;
  if (source.startsWith("textbook:")) return tr("custom.source.material");
  return source;
}

const ACT =
  "flex h-6 cursor-pointer items-center gap-1 rounded-full border border-border bg-surface px-2 text-[11px] text-fg-secondary transition-colors hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-50";

export function CustomGraphList({
  graphs,
  tr,
  busyKey,
  onView,
  onDelete,
}: {
  graphs: CustomGraphMeta[];
  tr: Tr;
  /** 进行中的操作（如 `delete:xxx`），命中行的按钮全部禁用 */
  busyKey: string | null;
  onView: (g: CustomGraphMeta) => void;
  onDelete: (g: CustomGraphMeta) => void;
}) {
  if (graphs.length === 0) return null;
  return (
    <div className="flex shrink-0 flex-col gap-1.5 rounded-[10px] border border-border bg-surface px-3 py-2.5">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-muted">
        {tr("custom.title")}
      </div>
      {graphs.map((g) => {
        const busy = busyKey !== null && busyKey.endsWith(g.topic_key);
        return (
          <div key={g.topic_key} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
            <span className="font-medium text-fg">{g.topic}</span>
            {isTextbook(g) && <Badge tone="accent">{tr("custom.textbook.badge")}</Badge>}
            <span className="tnum text-muted">v{g.version}</span>
            <span className="tnum text-muted">
              {g.node_count} {tr("statConcepts")}
            </span>
            <span className="text-muted">{sourceLabel(g.source, tr)}</span>
            <div className="ml-auto flex items-center gap-1.5">
              <button className={ACT} disabled={busy} onClick={() => onView(g)}>
                <Eye size={11} />
                {tr("custom.view")}
              </button>
              <button
                className={cn(ACT, "hover:border-danger hover:text-danger")}
                disabled={busy}
                onClick={() => onDelete(g)}
              >
                <Trash2 size={11} />
                {tr("custom.delete")}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
