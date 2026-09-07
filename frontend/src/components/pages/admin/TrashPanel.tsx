"use client";
// 公共教材库归档面板：恢复 / 彻底删除（功能与原卡片一致）。
import { useEffect, useState } from "react";
import { Archive, RotateCcw, Trash2 } from "lucide-react";
import { getAdminPublicTrash, purgeAdminPublicTrash, restoreAdminPublicTrash } from "@/lib/api";
import type { TrashItem } from "@/lib/types";
import { Card, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ConfirmModal } from "@/components/ui/Modal";
import type { Tr } from "./Field";

export function TrashPanel({ tr, onCount }: { tr: Tr; onCount?: (n: number) => void }) {
  const [items, setItems] = useState<TrashItem[]>([]);
  const [purgeTarget, setPurgeTarget] = useState<TrashItem | null>(null);

  const reload = () => {
    getAdminPublicTrash().then((list) => {
      setItems(list);
      onCount?.(list.length);
    }).catch(() => undefined);
  };

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const restore = async (item: TrashItem) => {
    await restoreAdminPublicTrash(item.id);
    reload();
  };

  const purge = async () => {
    if (!purgeTarget) return;
    await purgeAdminPublicTrash(purgeTarget.id);
    setPurgeTarget(null);
    reload();
  };

  return (
    <Card>
      <CardHeader
        icon={<Archive size={16} />}
        title={tr("adm.trash.title")}
        desc={tr("adm.trash.desc")}
      />
      {items.length === 0 ? <div className="text-xs text-muted">{tr("adm.trash.empty")}</div> : (
        <div className="space-y-2">
          {items.map((item) => (
            <div key={item.id} className="flex items-center justify-between gap-3 rounded-[8px] border border-border px-3 py-2">
              <div className="min-w-0">
                <div className="truncate text-xs font-medium text-fg">{item.title}</div>
                <div className="mt-0.5 text-[10px] text-muted">{item.resource_type} · {new Date(item.deleted_at * 1000).toLocaleString()}</div>
              </div>
              <div className="flex shrink-0 gap-1.5">
                <Button size="sm" variant="outline" icon={<RotateCcw size={12} />}
                  onClick={() => void restore(item)}>{tr("adm.trash.restore")}</Button>
                <Button size="sm" variant="danger" icon={<Trash2 size={12} />}
                  onClick={() => setPurgeTarget(item)}>{tr("adm.trash.purge")}</Button>
              </div>
            </div>
          ))}
        </div>
      )}
      <ConfirmModal
        open={purgeTarget !== null}
        onClose={() => setPurgeTarget(null)}
        onConfirm={() => void purge()}
        title={tr("adm.trash.purgeTitle")}
        desc={tr("adm.trash.purgeDesc")}
        confirmText={tr("adm.trash.purgeConfirm")}
        cancelText={tr("adm.users.cancel")}
      />
    </Card>
  );
}
