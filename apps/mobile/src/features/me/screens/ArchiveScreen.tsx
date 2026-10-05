import React, { useState } from "react";
import { View } from "react-native";
import type { TrashItem } from "@next-tutor/api-client";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { useAction, useServerQuery } from "@/lib/server-state";
import { confirm } from "@/lib/feedback";
import { useWorkspace } from "@/providers/WorkspaceProvider";
import { Button, Card, Chip, TextField, Sheet, ListRow } from "@/ui";
import {
  Body,
  Hint,
  Label,
  Pager,
  QueryState,
  SheetBody,
  SheetHeader,
} from "@/ui/Elements";
import { FeatureShell } from "@/ui/FeatureShell";
export function ArchiveScreen() {
  const c = useCopy();
  const scope = useWorkspace();
  const action = useAction();
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<TrashItem | null>(null);
  const [days, setDays] = useState("");
  const [restoreTo, setRestoreTo] = useState<string[]>([]);
  const query = useServerQuery(["archive", filter], (s) =>
    apiClient().archive.list(filter || undefined, s),
  );
  const policy = useServerQuery(["archive-policy"], (s) =>
    apiClient().archive.getPolicy(s),
  );
  return (
    <FeatureShell title={c("归档", "Archive")}>
      <Body>
        <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
          {[
            ["", c("全部", "All")],
            ["session", c("会话", "Chats")],
            ["notes_note", c("笔记", "Notes")],
            ["textbook", c("教材", "Textbooks")],
            ["classroom_lesson", c("课程", "Lessons")],
          ].map(([key, label]) => (
            <Chip
              key={key}
              label={label!}
              active={filter === key}
              onPress={() => {
                setFilter(key!);
                setPage(1);
              }}
            />
          ))}
        </View>
        <QueryState query={query} empty={!query.data?.items.length}>
          {query.data?.items.slice((page - 1) * 12, page * 12).map((item) => (
            <Card key={item.id} style={{ padding: 8 }}>
              <ListRow
                title={item.title}
                subtitle={item.deleted_at_iso}
                onPress={() => {
                  setSelected(item);
                  setRestoreTo(scope.id ? [scope.id] : []);
                }}
              />
            </Card>
          ))}
          <Pager
            page={page}
            total={query.data?.items.length ?? 0}
            pageSize={12}
            onChange={setPage}
          />
        </QueryState>
        <QueryState query={policy}>
          <Card style={{ gap: 12 }}>
            <Label>{c("保留时间", "Retention")}</Label>
            <Hint>
              {c(
                `当前保留 ${policy.data?.retention_days ?? "…"} 天。`,
                `Currently retained for ${policy.data?.retention_days ?? "…"} days.`,
              )}
            </Hint>
            <Hint>
              {c(
                `服务端允许最多 ${policy.data?.user_max_days ?? "…"} 天。`,
                `Your server allows up to ${policy.data?.user_max_days ?? "…"} days.`,
              )}
            </Hint>
            <TextField
              value={days}
              onChangeText={setDays}
              keyboardType="number-pad"
              accessibilityLabel={c("归档保留天数", "Archive retention days")}
            />
            <Button
              title={c("更新保留时间", "Update retention")}
              variant="outline"
              disabled={!/^\d+$/.test(days) || Number(days) < 1}
              onPress={() =>
                void action.run(() =>
                  apiClient().archive.setPolicy(Number(days)),
                )
              }
            />
          </Card>
        </QueryState>
      </Body>
      <Sheet
        open={!!selected}
        onClose={() => setSelected(null)}
        label={c("归档详情", "Archive details")}
      >
        <SheetHeader
          title={selected?.title ?? ""}
          onClose={() => setSelected(null)}
        />
        <SheetBody>
          <Hint>
            {c(
              "选择恢复后的工作区（可选）。",
              "Choose workspaces for the restored item (optional).",
            )}
          </Hint>
          {scope.workspaces.map((w) => (
            <Chip
              key={w.workspace_id}
              label={w.name}
              active={restoreTo.includes(w.workspace_id)}
              onPress={() =>
                setRestoreTo((old) =>
                  old.includes(w.workspace_id)
                    ? old.filter((x) => x !== w.workspace_id)
                    : [...old, w.workspace_id],
                )
              }
            />
          ))}
          <Button
            title={c("恢复", "Restore")}
            loading={action.pending}
            onPress={() =>
              void action.run(
                () => apiClient().archive.restore(selected!.id, restoreTo),
                () => setSelected(null),
              )
            }
          />
          <Hint>
            {c(
              "永久删除会清理对应来源。仍在近期窗口内的记忆贡献会一并移除。",
              "Permanent deletion removes the source and memory contributions still in the recent window.",
            )}
          </Hint>
          {selected?.metadata.memory_forget_status === "compacted" ? (
            <Hint>
              {c(
                "此会话记忆已合入整体摘要，无法安全地单独拆除。",
                "This conversation's memory is part of the combined summary and cannot be safely separated.",
              )}
            </Hint>
          ) : selected?.metadata.memory_forget_status === "legacy_unknown" ? (
            <Hint>
              {c(
                "历史记忆缺少精确归属，不会声称已拆除某一条贡献。",
                "Older memory has no precise attribution, so a specific contribution cannot be identified.",
              )}
            </Hint>
          ) : null}
          <Button
            title={c("永久删除", "Delete permanently")}
            variant="danger"
            onPress={() =>
              void confirm(
                c("永久删除此项？", "Permanently delete this item?"),
                c("此操作无法恢复。", "This action cannot be undone."),
                c("永久删除", "Delete permanently"),
              ).then((ok) => {
                if (ok)
                  void action.run(
                    () => apiClient().archive.purge(selected!.id),
                    () => setSelected(null),
                  );
              })
            }
          />
        </SheetBody>
      </Sheet>
    </FeatureShell>
  );
}
