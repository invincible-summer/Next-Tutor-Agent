import { useState, type ReactNode } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Pager, paged, pageCount } from "@/components/ui/Pager";
import type { PathNode } from "@/lib/types-modules";
import { DifficultyDots } from "./DifficultyDots";

function nodeName(n: PathNode): string {
  return n.name ?? n.concept ?? n.skill_id ?? "?";
}

/** 路径列表卡：接下来学什么 / 该复习什么（同款两行结构，分页展示）。 */
export function PathList({
  icon,
  title,
  desc,
  items,
  goLabel,
  emptyText,
  onGo,
}: {
  icon?: ReactNode;
  title: string;
  desc: string;
  items: PathNode[];
  goLabel: string;
  emptyText: string;
  onGo: (name: string) => void;
}) {
  const [page, setPage] = useState(0);
  const cur = Math.min(page, pageCount(items.length) - 1);
  const visible = paged(items, cur);
  return (
    <Card>
      <CardHeader icon={icon} title={title} desc={desc} />
      {items.length === 0 ? (
        <div className="rounded-[8px] border border-dashed border-border px-3 py-6 text-center text-xs text-muted">
          {emptyText}
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {visible.map((n, i) => {
            const name = nodeName(n);
            return (
              <div
                key={`${name}-${i}`}
                className="flex items-center gap-3 rounded-[8px] border border-border-light bg-surface px-3 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-fg">{name}</span>
                    {n.subject && <Badge tone="outline">{n.subject}</Badge>}
                  </div>
                  <div className="mt-1 flex items-center gap-2">
                    <DifficultyDots value={n.difficulty ?? 0} />
                    {n.reason && (
                      <span className="truncate text-xs text-muted">{n.reason}</span>
                    )}
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="shrink-0"
                  onClick={() => onGo(name)}
                >
                  {goLabel}
                </Button>
              </div>
            );
          })}
        </div>
      )}
      <Pager page={cur} total={items.length} onPage={setPage} />
    </Card>
  );
}
