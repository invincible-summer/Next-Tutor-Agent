import React, { useEffect, useState } from "react";
import { View } from "react-native";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { useAction, useServerQuery, record, str } from "@/lib/server-state";
import { confirm } from "@/lib/feedback";
import { useWorkspace } from "@/providers/WorkspaceProvider";
import { Button, Card, Chip, Field, TextField, ListRow, Sheet } from "@/ui";
import {
  Body,
  Hint,
  Label,
  QueryState,
  Section,
  SheetBody,
  SheetHeader,
} from "@/ui/Elements";
import { FeatureShell } from "@/ui/FeatureShell";
export function WorkspacesScreen() {
  const c = useCopy();
  const scope = useWorkspace();
  const action = useAction();
  const [id, setId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [files, setFiles] = useState<string[]>([]);
  const detail = useServerQuery(
    ["workspace", id],
    (s) => apiClient().workspace.get<Record<string, unknown>>(id!, s),
    { enabled: !!id },
  );
  const books = useServerQuery(["textbooks"], (s) =>
    apiClient().library.textbooks.list(s),
  );
  useEffect(() => {
    if (detail.data) {
      setName(str(detail.data.name));
      setFiles(
        Array.isArray(detail.data.selected_file_ids)
          ? detail.data.selected_file_ids.filter(
              (s): s is string => typeof s === "string",
            )
          : [],
      );
    }
  }, [detail.data]);
  return (
    <FeatureShell title={c("学习工作区", "Workspaces")}>
      <Body>
        <Hint>
          {c(
            "资料范围会影响学习证据。更改绑定后，服务端会重新整理对应观察。",
            "Source scope affects learning evidence. Your server reconciles observations after changes.",
          )}
        </Hint>
        <Button title={c("新建工作区", "New workspace")} onPress={scope.open} />
        {scope.workspaces.map((w) => (
          <Card key={w.workspace_id} style={{ padding: 8 }}>
            <ListRow
              title={w.name}
              subtitle={c(
                `${w.session_count} 个会话 · ${w.file_count} 份资料`,
                `${w.session_count} conversations · ${w.file_count} files`,
              )}
              onPress={() => setId(w.workspace_id)}
            />
          </Card>
        ))}
      </Body>
      <Sheet
        open={!!id}
        onClose={() => setId(null)}
        label={c("工作区设置", "Workspace details")}
      >
        <SheetHeader
          title={c("工作区设置", "Workspace details")}
          onClose={() => setId(null)}
        />
        <SheetBody>
          <QueryState query={detail}>
            <Field label={c("名称", "Name")}>
              <TextField
                value={name}
                onChangeText={setName}
                accessibilityLabel={c("工作区名称", "Workspace name")}
              />
            </Field>
            <Section title={c("绑定教材", "Bound textbooks")}>
              <Hint>
                {c(
                  "选择教材卷，文件可见性由服务端再次确认。",
                  "Choose textbook volumes. Your server rechecks visibility.",
                )}
              </Hint>
              {books.data?.textbooks.map((b) => {
                const ids = b.file_ids ?? [b.file_id ?? b.id];
                return (
                  <Chip
                    key={b.id}
                    label={b.title}
                    active={ids.every((i) => files.includes(i))}
                    onPress={() =>
                      setFiles((old) =>
                        ids.every((i) => old.includes(i))
                          ? old.filter((i) => !ids.includes(i))
                          : [...new Set([...old, ...ids])],
                      )
                    }
                  />
                );
              })}
            </Section>
            <Button
              title={c("保存绑定", "Save scope")}
              loading={action.pending}
              disabled={!name.trim()}
              onPress={() =>
                void action.run(
                  () =>
                    apiClient().workspace.update(id!, {
                      name: name.trim(),
                      file_ids: files,
                    }),
                  () => setId(null),
                )
              }
            />
            <Button
              title={c("在此工作区学习", "Use this workspace")}
              variant="outline"
              onPress={() => {
                scope.select(id);
                setId(null);
              }}
            />
            <Button
              title={c("归档工作区", "Archive workspace")}
              variant="ghost"
              onPress={() =>
                void confirm(
                  c("归档此工作区？", "Archive this workspace?"),
                  c(
                    "会话与資料按服务端归档策略处理，可在归档中恢复。",
                    "Its resources follow the server archive policy and can be restored.",
                  ),
                  c("归档", "Archive"),
                ).then((ok) => {
                  if (ok)
                    void action.run(
                      () => apiClient().workspace.remove(id!),
                      () => {
                        if (scope.id === id) scope.select(null);
                        setId(null);
                      },
                    );
                })
              }
            />
          </QueryState>
        </SheetBody>
      </Sheet>
    </FeatureShell>
  );
}
