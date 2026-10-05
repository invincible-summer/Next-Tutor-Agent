import React, { useRef, useState, useEffect, useCallback } from "react";
import { View } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import { apiClient } from "@/lib/api";
import { confirm } from "@/lib/feedback";
import { useCopy } from "@/lib/copy";
import { useAction, useServerQuery, str } from "@/lib/server-state";
import { useWorkspace } from "@/providers/WorkspaceProvider";
import { takeNativeDraft } from "@/stores/native-handoff";
import { domainRoute } from "@/shell/routes";
import {
  Button,
  Card,
  Chip,
  Field,
  TextField,
  TextArea,
  ListRow,
  RichContentRenderer,
} from "@/ui";
import { Body, Hint, Label, Pager, QueryState, Section } from "@/ui/Elements";
import { FeatureShell } from "@/ui/FeatureShell";
import { EditSheet } from "@/ui/EditSheet";
export function NotesScreen() {
  const c = useCopy();
  const router = useRouter();
  const scope = useWorkspace();
  const action = useAction();
  const vault = useServerQuery(["notes"], (s) => apiClient().notes.vault(s));
  const templates = useServerQuery(["note-templates"], (s) =>
    apiClient().notes.templates(s),
  );
  const [q, setQ] = useState("");
  const [folder, setFolder] = useState("");
  const [page, setPage] = useState(1);
  const [create, setCreate] = useState(false);
  const [title, setTitle] = useState("");
  const [draftContent, setDraftContent] = useState("");
  const [assistantDraftId, setAssistantDraftId] = useState<string | null>(null);
  useFocusEffect(
    useCallback(() => {
      const draft = takeNativeDraft("note");
      if (draft?.prefill.kind === "note") {
        setTitle(draft.prefill.title);
        setDraftContent(draft.prefill.markdown);
        setFolder(draft.prefill.folder_id ?? "");
        setAssistantDraftId(draft.draft_id);
        setTemplate("");
        setCreate(true);
      }
    }, []),
  );
  const [template, setTemplate] = useState("");
  const [renameFolderId, setRenameFolderId] = useState<string | null>(null);
  const [folderForm, setFolderForm] = useState(false);
  const [generate, setGenerate] = useState(false);
  const [instructions, setInstructions] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [output, setOutput] = useState("");
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => setPage(1), [q, folder]);
  const notes =
    vault.data?.notes.filter(
      (n) =>
        (!folder || n.folder_id === folder) &&
        n.title.toLowerCase().includes(q.toLowerCase()),
    ) ?? [];
  async function generateNotes() {
    if (streaming) return;
    const ctl = new AbortController();
    abort.current = ctl;
    setStreaming(true);
    setOutput("");
    await action.run(async () => {
      for await (const event of apiClient().notes.generateStream(
        {
          template_id: template,
          sources: scope.textbookIds.length
            ? { source_mode: "textbooks", textbook_ids: scope.textbookIds }
            : { source_mode: "workspace", workspace_id: scope.id ?? "" },
          target: { folder_id: folder, title: title.trim() },
          instructions,
        },
        ctl.signal,
      )) {
        if (ctl.signal.aborted) return;
        if (event.type === "token" || event.type === "answer")
          setOutput((old) => old + str(event.content, str(event.text)));
        else if (event.type === "done")
          setOutput(
            str(
              event.answer,
              c(
                "笔记已生成，请返回列表查看。",
                "Your note is ready. Find it in the list.",
              ),
            ),
          );
      }
    });
    setStreaming(false);
    void vault.refetch();
  }
  return (
    <FeatureShell title={c("笔记", "Notes")}>
      <Body>
        <View style={{ flexDirection: "row", gap: 8 }}>
          <TextField
            value={q}
            onChangeText={setQ}
            accessibilityLabel={c("搜索笔记", "Search notes")}
            placeholder={c("找到一段新的理解", "Find an understanding")}
            style={{ flex: 1 }}
          />
          <Button
            title={c("新笔记", "New note")}
            onPress={() => {
              setTitle("");
              setDraftContent("");
              setAssistantDraftId(null);
              setCreate(true);
            }}
          />
        </View>
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
          <Chip
            label={c("全部", "All")}
            active={!folder}
            onPress={() => setFolder("")}
          />
          {vault.data?.folders.map((f) => (
            <Chip
              key={f.id}
              label={f.name}
              active={folder === f.id}
              onPress={() => setFolder(f.id)}
            />
          ))}
          <Button
            title={c("新文件夹", "New folder")}
            variant="ghost"
            onPress={() => {
              setTitle("");
              setRenameFolderId(null);
              setFolderForm(true);
            }}
          />
        </View>
        {folder ? (
          <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
            <Button
              title={c("重命名文件夹", "Rename folder")}
              variant="ghost"
              onPress={() => {
                setRenameFolderId(folder);
                setTitle(
                  vault.data?.folders.find((f) => f.id === folder)?.name ?? "",
                );
                setFolderForm(true);
              }}
            />
            <Button
              title={c("删除文件夹", "Delete folder")}
              variant="ghost"
              onPress={() =>
                void confirm(
                  c("删除文件夹？", "Delete this folder?"),
                  c(
                    "笔记会保留并移到未分类。",
                    "Notes will be kept in Unfiled.",
                  ),
                  c("删除", "Delete"),
                ).then((ok) => {
                  if (ok)
                    void action.run(
                      () => apiClient().notes.deleteFolder(folder),
                      () => setFolder(""),
                    );
                })
              }
            />
          </View>
        ) : null}
        <Card style={{ gap: 12 }}>
          <Label>
            {c("把理解，整理成笔记", "Give your understanding a home")}
          </Label>
          <Hint>
            {c(
              "从工作区或已选择的教材起草，生成后可继续编辑。",
              "Draft from your workspace or selected textbooks, then make it your own.",
            )}
          </Hint>
          <Button
            title={c("根据学习资料生成", "Draft from learning materials")}
            variant="outline"
            onPress={() => {
              setTitle("");
              setGenerate(true);
            }}
          />
        </Card>
        <QueryState query={vault} empty={!notes.length}>
          {notes.slice((page - 1) * 12, page * 12).map((n) => (
            <Card key={n.id} style={{ padding: 8 }}>
              <ListRow
                title={n.title}
                subtitle={n.tags.join(" · ")}
                badge={{
                  label: n.review.enabled
                    ? c("复习已开启", "Review enabled")
                    : c("笔记", "Note"),
                }}
                onPress={() =>
                  router.push(domainRoute({ kind: "note", id: n.id }))
                }
              />
            </Card>
          ))}
          <Pager
            page={page}
            total={notes.length}
            pageSize={12}
            onChange={setPage}
          />
        </QueryState>
      </Body>
      <EditSheet
        open={create || folderForm}
        title={
          folderForm
            ? renameFolderId
              ? c("重命名文件夹", "Rename folder")
              : c("新建文件夹", "New folder")
            : c("新建笔记", "New note")
        }
        onClose={() => {
          setCreate(false);
          setFolderForm(false);
        }}
        pending={action.pending}
        disabled={!title.trim()}
        onSave={() =>
          void action.run(
            async () => {
              if (folderForm) {
                if (renameFolderId)
                  await apiClient().notes.renameFolder(renameFolderId, {
                    name: title.trim(),
                  });
                else await apiClient().notes.createFolder(title.trim());
                return null;
              }
              const result = await apiClient().notes.createNote({
                title: title.trim(),
                folder_id: folder,
                template_id: template,
                ...(draftContent ? { content: draftContent } : {}),
              });
              if (assistantDraftId)
                await apiClient()
                  .assistant.consumeDraft(assistantDraftId, {
                    kind: "note",
                    id: result.note.id,
                  })
                  .catch(() => {});
              return result;
            },
            (r) => {
              setCreate(false);
              setFolderForm(false);
              if (r) router.push(domainRoute({ kind: "note", id: r.note.id }));
            },
          )
        }
      >
        <Field label={c("名称", "Name")}>
          <TextField
            value={title}
            onChangeText={setTitle}
            accessibilityLabel={c("名称", "Name")}
          />
        </Field>
        {!folderForm && draftContent ? (
          <Field label={c("草稿内容", "Draft content")}>
            <TextArea
              value={draftContent}
              onChangeText={setDraftContent}
              accessibilityLabel={c("草稿内容", "Draft content")}
            />
          </Field>
        ) : null}
        {!folderForm ? (
          <Section title={c("模板", "Template")}>
            <Chip
              label={c("空白", "Blank")}
              active={!template}
              onPress={() => setTemplate("")}
            />
            {templates.data?.templates.map((t) => (
              <Chip
                key={str(t.id)}
                label={str(t.name)}
                active={template === t.id}
                onPress={() => setTemplate(str(t.id))}
              />
            ))}
          </Section>
        ) : null}
      </EditSheet>
      <EditSheet
        open={generate}
        title={c("起草学习笔记", "Draft a learning note")}
        onClose={() => {
          abort.current?.abort();
          setGenerate(false);
        }}
        pending={streaming}
        onSave={() => void generateNotes()}
        disabled={(!scope.id && !scope.textbookIds.length) || streaming}
        saveLabel={c("生成笔记", "Generate note")}
      >
        <Field label={c("标题（可选）", "Title (optional)")}>
          <TextField
            value={title}
            onChangeText={setTitle}
            accessibilityLabel={c("标题", "Title")}
          />
        </Field>
        <Field label={c("整理要求", "Instructions")}>
          <TextArea
            value={instructions}
            onChangeText={setInstructions}
            accessibilityLabel={c("整理要求", "Instructions")}
          />
        </Field>
        <Hint>
          {scope.textbookIds.length
            ? c("来源：已选择的教材", "Source: selected textbooks")
            : scope.id
              ? scope.name
              : c(
                  "请先选择一个工作区或教材来源。",
                  "Choose a workspace or textbook first.",
                )}
        </Hint>
        {streaming ? (
          <Button
            title={c("停止观察", "Stop observing")}
            variant="ghost"
            onPress={() => abort.current?.abort()}
          />
        ) : null}
        {output ? (
          <RichContentRenderer text={output} streaming={streaming} />
        ) : null}
      </EditSheet>
    </FeatureShell>
  );
}
