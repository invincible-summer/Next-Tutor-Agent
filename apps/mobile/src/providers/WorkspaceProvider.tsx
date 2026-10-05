import React, {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { View } from "react-native";
import { Check, Layers3, Plus } from "lucide-react-native";
import { useRouter } from "expo-router";
import { apiClient } from "@/lib/api";
import { useServerQuery, useAction } from "@/lib/server-state";
import { useCopy } from "@/lib/copy";
import { useAuth } from "./AuthProvider";
import { Button, Card, Field, TextField, ListRow, Sheet, useTheme } from "@/ui";
import { QueryState, SheetHeader, SheetBody, Hint } from "@/ui/Elements";
import { registerSessionCleanup } from "@/lib/session-lifecycle";
import type { WorkspaceItem } from "@/features/chat/model/types";
type Scope = {
  id: string | null;
  name: string;
  textbookIds: string[];
  fileIds: string[];
};
type Value = Scope & {
  select: (id: string | null) => void;
  setSources: (books: string[], files: string[]) => void;
  open: () => void;
  workspaces: WorkspaceItem[];
};
const Context = createContext<Value | null>(null);
export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const c = useCopy();
  const { theme } = useTheme();
  const { owner } = useAuth();
  const [id, select] = useState<string | null>(null);
  const [textbookIds, setBooks] = useState<string[]>([]);
  const [fileIds, setFiles] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const action = useAction();
  const query = useServerQuery(["workspaces"], () =>
    apiClient().workspace.list<{ workspaces: WorkspaceItem[] }>(),
  );
  useEffect(() => {
    select(null);
    setBooks([]);
    setFiles([]);
    setOpen(false);
  }, [owner]);
  useEffect(
    () =>
      registerSessionCleanup(() => {
        select(null);
        setBooks([]);
        setFiles([]);
        setOpen(false);
      }),
    [],
  );
  const workspaces = query.data?.workspaces ?? [];
  const value = useMemo(
    () => ({
      id,
      name:
        workspaces.find((w) => w.workspace_id === id)?.name ??
        c("全部学习", "All learning"),
      textbookIds,
      fileIds,
      workspaces,
      select,
      setSources: (books: string[], files: string[]) => {
        setBooks(books);
        setFiles(files);
      },
      open: () => setOpen(true),
    }),
    [id, workspaces, c, textbookIds, fileIds],
  );
  return (
    <Context.Provider value={value}>
      {children}
      <Sheet
        open={open}
        onClose={() => setOpen(false)}
        label={c("学习工作区", "Learning workspaces")}
      >
        <SheetHeader
          title={c("学习工作区", "Learning workspaces")}
          onClose={() => setOpen(false)}
        />
        <SheetBody>
          <Hint>
            {c(
              "为不同课程整理会话、资料和学习证据。",
              "Keep conversations, materials and learning evidence together.",
            )}
          </Hint>
          <ListRow
            title={c("全部学习", "All learning")}
            onPress={() => {
              select(null);
              setOpen(false);
            }}
            right={!id ? <Check color={theme.colors.accent} /> : null}
          />
          <QueryState query={query}>
            {workspaces.map((w) => (
              <ListRow
                key={w.workspace_id}
                title={w.name}
                subtitle={c(
                  `${w.session_count} 个会话 · ${w.file_count} 份资料`,
                  `${w.session_count} conversations · ${w.file_count} files`,
                )}
                onPress={() => {
                  select(w.workspace_id);
                  setOpen(false);
                }}
                right={
                  id === w.workspace_id ? (
                    <Check color={theme.colors.accent} />
                  ) : null
                }
              />
            ))}
          </QueryState>
          <Button
            title={c("管理工作区与资料绑定", "Manage workspaces and sources")}
            variant="outline"
            onPress={() => {
              setOpen(false);
              router.push("/(main)/library/workspaces");
            }}
          />
          <Card style={{ gap: 12 }}>
            <Field label={c("新建工作区", "New workspace")}>
              <TextField
                value={name}
                onChangeText={setName}
                accessibilityLabel={c("工作区名称", "Workspace name")}
                placeholder={c("例如：我的数学学习", "e.g. Mathematics")}
                maxLength={100}
              />
            </Field>
            <Button
              title={c("创建", "Create")}
              loading={action.pending}
              disabled={!name.trim()}
              leftIcon={<Plus size={18} color={theme.colors.onAccent} />}
              onPress={() =>
                void action.run(
                  () => apiClient().workspace.create(name.trim()),
                  (w) => {
                    select(w.workspace_id);
                    setName("");
                    setOpen(false);
                  },
                )
              }
            />
          </Card>
        </SheetBody>
      </Sheet>
    </Context.Provider>
  );
}
export function useWorkspace() {
  const value = useContext(Context);
  if (!value) throw new Error("WorkspaceProvider missing");
  return value;
}
export function WorkspaceChip() {
  const scope = useWorkspace();
  const { theme } = useTheme();
  const { state } = useAuth();
  const c = useCopy();
  if (state.status !== "signed-in") return null;
  return (
    <Button
      title={scope.name}
      variant="ghost"
      size="sm"
      leftIcon={<Layers3 size={17} color={theme.colors.accent} />}
      onPress={scope.open}
      accessibilityLabel={c("切换学习工作区", "Switch workspace")}
    />
  );
}
