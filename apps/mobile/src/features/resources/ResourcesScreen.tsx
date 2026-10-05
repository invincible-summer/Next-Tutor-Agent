import React, { useEffect, useRef, useState } from "react";
import { ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import * as DocumentPicker from "expo-document-picker";
import { Check, FileText, Folder, Plus, Upload } from "lucide-react-native";
import type { LibraryFile, TextbookListItem } from "@next-tutor/api-client";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { confirm } from "@/lib/feedback";
import { useServerQuery, useAction, str, record } from "@/lib/server-state";
import { shareBytes, deletePickedFiles } from "@/platform/files";
import { useAuth } from "@/providers/AuthProvider";
import { useWorkspace } from "@/providers/WorkspaceProvider";
import {
  Button,
  Card,
  Chip,
  Field,
  TextField,
  ListRow,
  Sheet,
  Tabs,
  useTheme,
} from "@/ui";
import {
  Body,
  Hint,
  Label,
  Pager,
  QueryState,
  Section,
  SheetBody,
  SheetHeader,
} from "@/ui/Elements";
import { FeatureShell } from "@/ui/FeatureShell";
import { EditSheet } from "@/ui/EditSheet";
import { AdaptivePane } from "@/shell/adaptive/AdaptivePane";
import { domainRoute } from "@/shell/routes";
import { useAdaptive } from "@/shell/adaptive/window-class";
export function ResourcesScreen() {
  const c = useCopy();
  const { theme } = useTheme();
  const router = useRouter();
  const auth = useAuth();
  const scope = useWorkspace();
  const adaptive = useAdaptive();
  const action = useAction();
  const [tab, setTab] = useState("books");
  const [search, setSearch] = useState("");
  const [folder, setFolder] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<
    TextbookListItem | LibraryFile | null
  >(null);
  const [edit, setEdit] = useState<
    "folder" | "rename" | "rename-folder" | null
  >(null);
  const [manageFolders, setManageFolders] = useState(false);
  const [folderToEdit, setFolderToEdit] = useState("");
  const [name, setName] = useState("");
  const [upload, setUpload] = useState<
    DocumentPicker.DocumentPickerAsset[] | null
  >(null);
  const [subject, setSubject] = useState("");
  const [level, setLevel] = useState("其他");
  const [uploading, setUploading] = useState(false);
  const [outcomes, setOutcomes] = useState<string[]>([]);
  const abort = useRef<AbortController | null>(null);
  const picked = useRef<DocumentPicker.DocumentPickerAsset[]>([]);
  const alive = useRef(true);
  const cancelUpload = useRef(false);
  const signed = auth.state.status === "signed-in";
  const admin =
    auth.state.status === "signed-in" && auth.state.user.role === "admin";
  const library = useServerQuery(["library"], (s) =>
    apiClient().library.list(s),
  );
  const books = useServerQuery(
    ["textbooks"],
    async (s) =>
      signed
        ? apiClient().library.textbooks.list(s)
        : apiClient()
            .guest.textbooks()
            .then((r) => ({
              textbooks: r.items.map((b) => ({
                id: b.id,
                title: b.title || c("公共教材", "Public textbook"),
                scope: "public" as const,
                subject: "",
                level: "",
                status: "",
              })),
            })),
    { public: true, poll: 8000 },
  );
  const detail = useServerQuery(
    ["textbook", selected?.id],
    (s) => apiClient().library.textbooks.get(selected!.id, s),
    { enabled: !!selected && tab === "books" && signed, poll: 4000 },
  );
  useEffect(() => {
    setPage(1);
  }, [search, tab, folder]);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      abort.current?.abort();
      deletePickedFiles(picked.current.map((f) => f.uri));
      picked.current = [];
    };
  }, []);
  const items =
    tab === "books"
      ? (books.data?.textbooks ?? []).filter((b) =>
          b.title.toLowerCase().includes(search.toLowerCase()),
        )
      : (library.data?.files ?? []).filter(
          (f) =>
            (!folder || f.folder_id === folder) &&
            f.filename.toLowerCase().includes(search.toLowerCase()),
        );
  const query = tab === "books" ? books : library;
  const clearPicked = () => {
    deletePickedFiles(picked.current.map((f) => f.uri));
    picked.current = [];
    if (alive.current) setUpload(null);
  };
  const closeUpload = () => {
    cancelUpload.current = true;
    abort.current?.abort();
    if (!uploading) clearPicked();
  };
  async function pick() {
    const result = await DocumentPicker.getDocumentAsync({
      multiple: true,
      copyToCacheDirectory: true,
    });
    if (!result.canceled) {
      if (!alive.current) {
        deletePickedFiles(result.assets.map((f) => f.uri));
        return;
      }
      clearPicked();
      picked.current = result.assets;
      cancelUpload.current = false;
      setUpload(result.assets);
      setOutcomes([]);
    }
  }
  async function submitUpload() {
    if (!upload || uploading) return;
    setUploading(true);
    const controller = new AbortController();
    abort.current = controller;
    const form = new FormData();
    upload.forEach((f) =>
      form.append("files", {
        uri: f.uri,
        name: f.name,
        type: f.mimeType || "application/octet-stream",
      } as unknown as Blob),
    );
    cancelUpload.current = false;
    const result = await action.run(
      async () => {
        try {
          return await (tab === "books"
            ? apiClient().library.textbooks.upload(
                form,
                { scope: "private", subject, level },
                controller.signal,
              )
            : apiClient().library.upload(form, folder, controller.signal));
        } catch (error) {
          if (controller.signal.aborted) return { results: [] };
          throw error;
        }
      },
      (result) => {
        setOutcomes(
          result.results.map((r) =>
            "error" in r
              ? `${r.filename} · ${c("失败，请重试", "Failed, please retry")}`
              : `${r.filename} · ${c("已受理", "Accepted")}`,
          ),
        );
      },
    );
    if (result !== undefined || cancelUpload.current || !alive.current)
      clearPicked();
    if (alive.current) setUploading(false);
    abort.current = null;
  }
  const writable =
    selected &&
    signed &&
    (tab === "files" ||
      !((selected as TextbookListItem).scope === "public") ||
      admin);
  const statusLabel = (status: string) =>
    ({
      building: c("正在构建", "Building"),
      queued: c("等待处理", "Queued"),
      ready: c("可用于学习", "Ready for learning"),
      failed: c("处理失败", "Processing failed"),
      ocr_waiting: c("等待文字识别", "Waiting for text recognition"),
      cancelled: c("已停止", "Cancelled"),
      bm25_ready: c("可检索", "Search ready"),
    })[status] ||
    status ||
    c("暂无处理状态", "No processing status available");
  const list = (
    <Body>
      <Tabs
        items={[
          { key: "books", label: c("教材", "Textbooks") },
          { key: "files", label: c("文件", "Files") },
        ]}
        active={tab}
        onChange={(value) => {
          setTab(value);
          setSelected(null);
        }}
      />
      <View style={{ flexDirection: "row", gap: 8 }}>
        <TextField
          accessibilityLabel={c("搜索资料", "Search materials")}
          value={search}
          onChangeText={setSearch}
          placeholder={c("搜索标题或文件名", "Search titles or filenames")}
          style={{ flex: 1 }}
        />
        {signed ? (
          <Button
            title={c("上传", "Upload")}
            icon={<Upload size={18} color={theme.colors.onAccent} />}
            onPress={() => void pick()}
          />
        ) : null}
      </View>
      {tab === "files" ? (
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
          <Chip
            label={c("全部", "All")}
            active={!folder}
            onPress={() => setFolder("")}
          />
          {library.data?.folders.map((f) => (
            <Chip
              key={f.id}
              label={f.name}
              active={folder === f.id}
              onPress={() => setFolder(f.id)}
            />
          ))}
          {signed ? (
            <Button
              title={c("管理文件夹", "Manage folders")}
              variant="ghost"
              onPress={() => setManageFolders(true)}
            />
          ) : null}
          {signed ? (
            <Button
              title={c("新文件夹", "New folder")}
              variant="ghost"
              onPress={() => {
                setName("");
                setEdit("folder");
              }}
            />
          ) : null}
        </View>
      ) : null}
      {!signed && tab === "files" ? (
        <Hint>
          {c(
            "登录后上传并保存自己的文件。",
            "Sign in to upload and save your files.",
          )}
        </Hint>
      ) : (
        <QueryState query={query} empty={!items.length}>
          <View style={{ gap: 12 }}>
            {items.slice((page - 1) * 12, page * 12).map((item) => (
              <Card key={item.id} style={{ padding: 8 }}>
                <ListRow
                  title={"title" in item ? str(item.title) : str(item.filename)}
                  subtitle={
                    "title" in item
                      ? [item.subject, item.level, item.status]
                          .filter(Boolean)
                          .join(" · ")
                      : str(item.summary)
                  }
                  left={<FileText size={22} color={theme.colors.accent} />}
                  onPress={() => setSelected(item)}
                  right={
                    scope.textbookIds.includes(item.id) ||
                    scope.fileIds.includes(item.id) ? (
                      <Check size={19} color={theme.colors.accent} />
                    ) : null
                  }
                />
              </Card>
            ))}
          </View>
          <Pager
            page={page}
            total={items.length}
            pageSize={12}
            onChange={setPage}
          />
        </QueryState>
      )}
      {outcomes.map((o, i) => (
        <Hint key={`${i}:${o}`}>{o}</Hint>
      ))}
    </Body>
  );
  const detailContent = (
    <Body>
      {selected ? (
        <>
          <Hint>
            {tab === "books"
              ? c(
                  "教材来源用于检索和学习上下文。",
                  "Use this textbook as a learning source.",
                )
              : c(
                  "文件保存在你的个人资料库。",
                  "This file is in your personal library.",
                )}
          </Hint>
          <Button
            title={c("设为学习来源", "Use as a learning source")}
            onPress={() => {
              if (tab === "books")
                scope.setSources(
                  [...new Set([...scope.textbookIds, selected.id])],
                  scope.fileIds,
                );
              else
                scope.setSources(scope.textbookIds, [
                  ...new Set([...scope.fileIds, selected.id]),
                ]);
              setSelected(null);
            }}
          />
          <Button
            title={c("带着资料提问", "Ask about this")}
            variant="outline"
            onPress={() => {
              if (tab === "books") scope.setSources([selected.id], []);
              else scope.setSources([], [selected.id]);
              setSelected(null);
              router.push(
                domainRoute({
                  kind: "chat",
                  ...(scope.id ? { workspaceId: scope.id } : {}),
                }),
              );
            }}
          />
          {signed &&
          (tab === "books"
            ? !detail.data?.textbook.volumes?.length
            : (selected as LibraryFile).has_original !== false) ? (
            <Button
              title={c("下载原件", "Download original")}
              variant="outline"
              loading={action.pending}
              onPress={() =>
                void action.run(async () =>
                  shareBytes(
                    tab === "books"
                      ? await apiClient().library.textbooks.download(
                          selected.id,
                        )
                      : await apiClient().library.downloadFile(selected.id),
                    "title" in selected
                      ? selected.title + ".pdf"
                      : selected.filename,
                    "application/octet-stream",
                  ),
                )
              }
            />
          ) : null}
          {tab === "books" ? (
            <Card style={{ gap: 8 }}>
              <Label>{c("公开处理状态", "Processing status")}</Label>
              <Hint>
                {statusLabel(
                  str(
                    detail.data?.textbook.status,
                    str((selected as TextbookListItem).status),
                  ),
                )}
              </Hint>
              <Hint>
                {(selected as TextbookListItem).scope === "public"
                  ? c(
                      "公共教材 · 所有账户可读",
                      "Public textbook · available to all accounts",
                    )
                  : c("个人教材", "Personal textbook")}
              </Hint>
              {detail.data?.textbook.warnings?.map((warning, i) => (
                <Hint key={i}>{warning}</Hint>
              ))}
              {detail.data?.textbook.error ? (
                <Hint>
                  {c(
                    "处理暂未完成，请检查服务配置或重试。",
                    "Processing did not complete. Check service configuration or retry.",
                  )}
                </Hint>
              ) : null}
            </Card>
          ) : (
            <Hint>
              {str((selected as LibraryFile).summary) ||
                c(
                  "文件已保存在资料库。",
                  "This file is saved in your library.",
                )}
            </Hint>
          )}
          {tab === "books" && signed ? (
            <QueryState query={detail}>
              {detail.data?.textbook.volumes?.map((volume) => (
                <Card key={volume.file_id} style={{ gap: 8 }}>
                  <Label>{volume.filename}</Label>
                  {volume.has_original !== false ? (
                    <Button
                      title={c("下载此卷", "Download volume")}
                      variant="outline"
                      loading={action.pending}
                      onPress={() =>
                        void action.run(async () =>
                          shareBytes(
                            await apiClient().library.textbooks.downloadVolume(
                              selected.id,
                              volume.file_id,
                            ),
                            volume.original_filename || volume.filename,
                            "application/octet-stream",
                          ),
                        )
                      }
                    />
                  ) : null}
                </Card>
              ))}
              {detail.data?.outline.map((ch) => (
                <Section key={ch.chapter} title={ch.chapter}>
                  <Hint>{ch.concepts.join(" · ")}</Hint>
                </Section>
              ))}
            </QueryState>
          ) : null}
          {writable ? (
            <>
              <Button
                title={c("重命名", "Rename")}
                variant="ghost"
                onPress={() => {
                  setName(
                    "title" in selected
                      ? str(selected.title)
                      : str(selected.filename),
                  );
                  setEdit("rename");
                }}
              />
              {tab === "books" ? (
                <>
                  <Button
                    title={c("重新构建知识图谱", "Rebuild knowledge graph")}
                    variant="ghost"
                    loading={action.pending}
                    onPress={() =>
                      void action.run(() =>
                        apiClient().library.textbooks.rebuildGraph(selected.id),
                      )
                    }
                  />
                  {["building", "queued", "ocr_waiting", "processing"].includes(
                    str(detail.data?.textbook.status),
                  ) ? (
                    <Button
                      title={c("停止解析", "Cancel parsing")}
                      variant="ghost"
                      onPress={() =>
                        void action.run(() =>
                          apiClient().library.textbooks.cancel(selected.id),
                        )
                      }
                    />
                  ) : null}
                </>
              ) : (
                <Section title={c("移动到文件夹", "Move to folder")}>
                  <Button
                    title={c("未分类", "Unfiled")}
                    variant="ghost"
                    onPress={() =>
                      void action.run(
                        () => apiClient().library.moveFile(selected.id, ""),
                        () => setSelected(null),
                      )
                    }
                  />
                  {library.data?.folders.map((f) => (
                    <Button
                      key={f.id}
                      title={f.name}
                      variant="ghost"
                      onPress={() =>
                        void action.run(
                          () => apiClient().library.moveFile(selected.id, f.id),
                          () => setSelected(null),
                        )
                      }
                    />
                  ))}
                </Section>
              )}
              <Button
                title={c("移入归档", "Archive")}
                variant="ghost"
                onPress={() =>
                  void confirm(
                    c("归档此资料？", "Archive this material?"),
                    c(
                      "可以在「我的 → 归档」恢复。",
                      "Restore it later from Me → Archive.",
                    ),
                    c("归档", "Archive"),
                  ).then((ok) => {
                    if (ok)
                      void action.run(
                        async () =>
                          await (tab === "books"
                            ? apiClient().library.textbooks.archive(selected.id)
                            : apiClient().library.deleteFile(selected.id)),
                        () => setSelected(null),
                      );
                  })
                }
              />
            </>
          ) : null}
        </>
      ) : null}
    </Body>
  );
  return (
    <FeatureShell
      title={c("教材与文件", "Materials")}
      auth={false}
      scroll={false}
    >
      <AdaptivePane
        inspector={
          selected ? (
            <ScrollView keyboardShouldPersistTaps="handled">
              {detailContent}
            </ScrollView>
          ) : undefined
        }
      >
        <ScrollView
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={{ flexGrow: 1 }}
        >
          {list}
        </ScrollView>
      </AdaptivePane>
      <Sheet
        open={!!selected && adaptive.maxPanes === 1}
        onClose={() => setSelected(null)}
        label={c("资料详情", "Material details")}
      >
        <SheetHeader
          title={
            selected
              ? "title" in selected
                ? str(selected.title)
                : str(selected.filename)
              : ""
          }
          onClose={() => setSelected(null)}
        />
        <SheetBody>{detailContent}</SheetBody>
      </Sheet>
      <Sheet
        open={manageFolders}
        onClose={() => setManageFolders(false)}
        label={c("管理文件夹", "Manage folders")}
      >
        <SheetHeader
          title={c("管理文件夹", "Manage folders")}
          onClose={() => setManageFolders(false)}
        />
        <SheetBody>
          <QueryState query={library} empty={!library.data?.folders.length}>
            {library.data?.folders.map((item) => (
              <Card key={item.id} style={{ gap: 12 }}>
                <Label>{item.name}</Label>
                <Hint>
                  {c(
                    `${library.data?.files.filter((file) => file.folder_id === item.id).length ?? 0} 个文件`,
                    `${library.data?.files.filter((file) => file.folder_id === item.id).length ?? 0} files`,
                  )}
                </Hint>
                {item.workspace_id ? (
                  <Hint>
                    {c(
                      "此文件夹由工作区管理。",
                      "Manage this folder through its workspace.",
                    )}
                  </Hint>
                ) : (
                  <View style={{ gap: 8 }}>
                    <Button
                      title={c("重命名", "Rename")}
                      variant="outline"
                      onPress={() => {
                        setFolderToEdit(item.id);
                        setName(item.name);
                        setEdit("rename-folder");
                        setManageFolders(false);
                      }}
                    />
                    <Button
                      title={c("删除空文件夹", "Delete empty folder")}
                      variant="ghost"
                      disabled={library.data?.files.some(
                        (file) => file.folder_id === item.id,
                      )}
                      onPress={() =>
                        void confirm(
                          c("删除此空文件夹？", "Delete this empty folder?"),
                          c(
                            "文件夹删除后不能继续用于整理资料。",
                            "The folder will no longer be available for organizing files.",
                          ),
                          c("删除", "Delete"),
                        ).then((ok) => {
                          if (ok)
                            void action.run(
                              () => apiClient().library.deleteFolder(item.id),
                              () => {
                                if (folder === item.id) setFolder("");
                              },
                            );
                        })
                      }
                    />
                  </View>
                )}
              </Card>
            ))}
          </QueryState>
        </SheetBody>
      </Sheet>
      <EditSheet
        open={!!edit}
        title={
          edit === "folder"
            ? c("新建文件夹", "New folder")
            : c("重命名", "Rename")
        }
        onClose={() => setEdit(null)}
        pending={action.pending}
        disabled={!name.trim()}
        onSave={() =>
          void action.run(
            async () =>
              await (edit === "folder"
                ? apiClient().library.createFolder(name.trim())
                : edit === "rename-folder"
                  ? apiClient().library.renameFolder(folderToEdit, name.trim())
                  : tab === "books"
                    ? apiClient().library.textbooks.patch(selected!.id, {
                        title: name.trim(),
                      })
                    : apiClient().library.renameFile(
                        selected!.id,
                        name.trim(),
                      )),
            () => {
              setEdit(null);
              setSelected(null);
            },
          )
        }
      >
        <Field label={c("名称", "Name")}>
          <TextField
            value={name}
            onChangeText={setName}
            accessibilityLabel={c("名称", "Name")}
          />
        </Field>
      </EditSheet>
      <Sheet
        open={!!upload}
        onClose={closeUpload}
        label={c("上传资料", "Upload materials")}
      >
        <SheetHeader
          title={c("上传资料", "Upload materials")}
          onClose={closeUpload}
        />
        <SheetBody>
          {upload?.map((f) => (
            <ListRow
              key={f.uri}
              title={f.name}
              subtitle={`${((f.size ?? 0) / 1048576).toFixed(1)} MB · ${f.mimeType ?? c("未知类型", "Unknown type")}`}
            />
          ))}
          {tab === "books" ? (
            <>
              <Field label={c("学科", "Subject")}>
                <TextField
                  value={subject}
                  onChangeText={setSubject}
                  accessibilityLabel={c("学科", "Subject")}
                />
              </Field>
              <Field label={c("学段", "Level")}>
                <TextField
                  value={level}
                  onChangeText={setLevel}
                  accessibilityLabel={c("学段", "Level")}
                />
              </Field>
            </>
          ) : null}
          <Hint>
            {uploading
              ? c(
                  "正在上传，完成后由服务端解析。",
                  "Uploading. Your server will process the materials.",
                )
              : c(
                  "上传至个人资料库；文件类型和访问权限由服务端确认。",
                  "Upload to your personal library. Your server validates the files and access.",
                )}
          </Hint>
          <Button
            title={c("确认上传", "Upload")}
            loading={uploading}
            disabled={tab === "books" && !level.trim()}
            onPress={() => void submitUpload()}
          />
          {uploading ? (
            <Button
              title={c("取消上传", "Cancel upload")}
              variant="ghost"
              onPress={closeUpload}
            />
          ) : null}
        </SheetBody>
      </Sheet>
    </FeatureShell>
  );
}
