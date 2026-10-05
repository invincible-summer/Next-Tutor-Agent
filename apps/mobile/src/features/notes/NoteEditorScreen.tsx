import React, { useEffect, useRef, useState } from "react";
import { ScrollView, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { ApiError, type NoteDetail } from "@next-tutor/api-client";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { useServerQuery, useAction } from "@/lib/server-state";
import { useDirtyGuard } from "@/lib/useDirtyGuard";
import { confirm, errorMessage } from "@/lib/feedback";
import { shareBytes } from "@/platform/files";
import { domainRoute } from "@/shell/routes";
import { useAdaptive } from "@/shell/adaptive/window-class";
import { AdaptivePane } from "@/shell/adaptive/AdaptivePane";
import {
  Button,
  Card,
  Chip,
  Field,
  TextField,
  TextArea,
  RichContentRenderer,
  SegmentedControl,
  Sheet,
  ListRow,
  useToast,
} from "@/ui";
import {
  Body,
  Hint,
  Label,
  QueryState,
  Section,
  SheetHeader,
  SheetBody,
} from "@/ui/Elements";
import { FeatureShell } from "@/ui/FeatureShell";
export function NoteEditorScreen() {
  const { noteId } = useLocalSearchParams<{ noteId: string }>();
  const router = useRouter();
  const c = useCopy();
  const toast = useToast();
  const action = useAction();
  const adaptive = useAdaptive();
  const query = useServerQuery(["note", noteId], (s) =>
    apiClient().notes.getNote(noteId, s),
  );
  const vault = useServerQuery(["notes"], (s) => apiClient().notes.vault(s));
  const revisions = useServerQuery(["note-revisions", noteId], () =>
    apiClient().notes.listRevisions(noteId),
  );
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [baseline, setBaseline] = useState({
    title: "",
    content: "",
    revision: 0,
  });
  const [view, setView] = useState("edit");
  const [metadata, setMetadata] = useState(false);
  const [conflict, setConflict] = useState<NoteDetail | null>(null);
  const [saving, setSaving] = useState(false);
  const [revision, setRevision] = useState<{
    revision: number;
    content: string;
  } | null>(null);
  const initialized = useRef("");
  const alive = useRef(true);
  const saveLock = useRef(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const dirty = title !== baseline.title || content !== baseline.content;
  useDirtyGuard(dirty);
  useEffect(() => {
    if (query.data && initialized.current !== noteId) {
      initialized.current = noteId;
      setTitle(query.data.note.title);
      setContent(query.data.content);
      setBaseline({
        title: query.data.note.title,
        content: query.data.content,
        revision: query.data.note.revision,
      });
    }
  }, [query.data, noteId]);
  async function save() {
    if (saveLock.current || !dirty) return;
    saveLock.current = true;
    setSaving(true);
    try {
      const r = await apiClient().notes.saveNote(noteId, {
        title,
        content,
        base_revision: baseline.revision,
      });
      if (!alive.current) return;
      setBaseline({ title, content, revision: r.note.revision });
      toast(c("笔记已保存", "Note saved"));
      void query.refetch();
      void revisions.refetch();
      void vault.refetch();
    } catch (e) {
      if (!alive.current) return;
      if (e instanceof ApiError && e.status === 409) {
        const latest = await apiClient()
          .notes.getNote(noteId)
          .catch(() => null);
        if (!alive.current) return;
        if (latest) setConflict(latest);
        else toast(errorMessage(e, c), "error");
      } else toast(errorMessage(e, c), "error");
    } finally {
      saveLock.current = false;
      if (alive.current) setSaving(false);
    }
  }
  const details = (
    <Body>
      <Section title={c("笔记信息", "Note details")}>
        <Hint>
          {c(
            `已保存版本 V${baseline.revision}`,
            `Saved revision V${baseline.revision}`,
          )}
        </Hint>
        <Field label={c("文件夹", "Folder")}>
          <View style={{ gap: 8 }}>
            {vault.data?.folders.map((f) => (
              <Chip
                key={f.id}
                label={f.name}
                active={query.data?.note.folder_id === f.id}
                onPress={() =>
                  void action.run(() =>
                    apiClient().notes.patchNote(noteId, { folder_id: f.id }),
                  )
                }
              />
            ))}
          </View>
        </Field>
        <Button
          title={
            query.data?.note.review.enabled
              ? c("关闭笔记复习", "Disable review")
              : c("开启笔记复习", "Enable review")
          }
          variant="outline"
          onPress={() =>
            void action.run(() =>
              apiClient().notes.patchNote(noteId, {
                review_enabled: !query.data?.note.review.enabled,
              }),
            )
          }
        />
        {query.data?.note.review.enabled ? (
          <View style={{ gap: 8 }}>
            <Hint>{c("这次回顾感觉如何？", "How did this review feel?")}</Hint>
            {[
              [1, c("还需练习", "Need practice")],
              [3, c("基本理解", "Mostly clear")],
              [5, c("很清晰", "Clear")],
            ].map(([q, label]) => (
              <Button
                key={String(q)}
                title={String(label)}
                variant="ghost"
                onPress={() =>
                  void action.run(() =>
                    apiClient().notes.submitReview(noteId, Number(q)),
                  )
                }
              />
            ))}
          </View>
        ) : null}
      </Section>
      <Section title={c("关联与反向链接", "Links and backlinks")}>
        {query.data?.backlinks.map((n) => (
          <ListRow
            key={n.id}
            title={n.title}
            onPress={() => router.push(domainRoute({ kind: "note", id: n.id }))}
          />
        ))}
        {query.data?.links.resolved.map((n) => (
          <ListRow
            key={n.note_id}
            title={n.title}
            onPress={() =>
              router.push(domainRoute({ kind: "note", id: n.note_id }))
            }
          />
        ))}
      </Section>
      <Section title={c("版本历史", "Revision history")}>
        {revisions.data?.revisions.map((r) => (
          <Button
            key={r.revision}
            title={`V${r.revision}`}
            variant="ghost"
            onPress={() =>
              void action.run(
                () => apiClient().notes.readRevision(noteId, r.revision),
                setRevision,
              )
            }
          />
        ))}
      </Section>
      <Button
        title={c("导出 Markdown", "Export Markdown")}
        variant="outline"
        onPress={() =>
          void action.run(async () =>
            shareBytes(
              await apiClient().notes.exportNote(noteId),
              title + ".md",
              "text/markdown",
            ),
          )
        }
      />
      <Button
        title={c("移入归档", "Archive note")}
        variant="ghost"
        disabled={dirty}
        onPress={() =>
          void confirm(
            c("归档此笔记？", "Archive this note?"),
            c("可以从归档恢复。", "Restore it later from Archive."),
            c("归档", "Archive"),
          ).then((ok) => {
            if (ok)
              void action.run(
                () => apiClient().notes.deleteNote(noteId),
                () => router.replace("/(main)/library/notes"),
              );
          })
        }
      />
    </Body>
  );
  const master = (
    <ScrollView>
      <Body>
        <Section title={c("我的笔记", "My notes")}>
          {vault.data?.notes.map((n) => (
            <ListRow
              key={n.id}
              title={n.title}
              onPress={() =>
                router.push(domainRoute({ kind: "note", id: n.id }))
              }
            />
          ))}
        </Section>
      </Body>
    </ScrollView>
  );
  return (
    <FeatureShell
      title={c("笔记编辑", "Note editor")}
      scroll={false}
      right={
        <Button
          title={c("信息", "Details")}
          variant="ghost"
          onPress={() => setMetadata(true)}
        />
      }
    >
      <AdaptivePane
        master={master}
        inspector={<ScrollView>{details}</ScrollView>}
      >
        <ScrollView keyboardShouldPersistTaps="handled">
          <Body>
            <QueryState query={query}>
              <Field label={c("标题", "Title")}>
                <TextField
                  value={title}
                  onChangeText={setTitle}
                  accessibilityLabel={c("笔记标题", "Note title")}
                />
              </Field>
              <View
                style={{ flexDirection: "row", alignItems: "center", gap: 8 }}
              >
                <SegmentedControl
                  items={[
                    { key: "edit", label: c("编辑", "Edit") },
                    { key: "preview", label: c("预览", "Preview") },
                  ]}
                  active={view}
                  onChange={setView}
                />
                <Button
                  testID="note-save"
                  title={c("保存", "Save")}
                  loading={saving}
                  disabled={!dirty}
                  onPress={() => void save()}
                />
              </View>
              <Hint>
                {dirty
                  ? c("尚未保存", "Unsaved changes")
                  : c("已保存到服务端", "Saved to your server")}
              </Hint>
              {view === "edit" ? (
                <TextArea
                  testID="note-content"
                  value={content}
                  onChangeText={setContent}
                  accessibilityLabel={c(
                    "笔记 Markdown 正文",
                    "Note Markdown content",
                  )}
                  style={{
                    minHeight: 440,
                    borderWidth: 0,
                    paddingHorizontal: 0,
                    backgroundColor: "transparent",
                  }}
                />
              ) : (
                <RichContentRenderer text={content} />
              )}
            </QueryState>
          </Body>
        </ScrollView>
      </AdaptivePane>
      <Sheet
        open={metadata}
        onClose={() => setMetadata(false)}
        label={c("笔记信息", "Note details")}
      >
        <SheetHeader
          title={c("笔记信息", "Note details")}
          onClose={() => setMetadata(false)}
        />
        <SheetBody>{details}</SheetBody>
      </Sheet>
      <Sheet
        open={!!conflict}
        onClose={() => setConflict(null)}
        label={c("笔记版本冲突", "Note conflict")}
      >
        <SheetHeader
          title={c("选择如何合并", "Choose how to merge")}
          onClose={() => setConflict(null)}
        />
        <SheetBody>
          <Hint>
            {c(
              "此笔记已在其他地方更新。你的草稿保留在编辑器中；比较后再保存。",
              "This note changed elsewhere. Your draft is kept in the editor. Compare the two before saving.",
            )}
          </Hint>
          <Section title={c("你的草稿", "Your draft")}>
            <Card>
              <RichContentRenderer text={content} />
            </Card>
          </Section>
          <Section title={c("服务端最新版本", "Latest server version")}>
            <Card>
              <RichContentRenderer text={conflict?.content ?? ""} />
            </Card>
          </Section>
          <Button
            title={c(
              "保留草稿，手动合并后保存",
              "Keep draft and merge manually",
            )}
            onPress={() => {
              if (conflict)
                setBaseline({
                  title: conflict.note.title,
                  content: conflict.content,
                  revision: conflict.note.revision,
                });
              setConflict(null);
              setView("edit");
            }}
          />
          <Button
            title={c("使用服务端版本", "Use server version")}
            variant="outline"
            onPress={() => {
              if (conflict) {
                setContent(conflict.content);
                setTitle(conflict.note.title);
                setBaseline({
                  title: conflict.note.title,
                  content: conflict.content,
                  revision: conflict.note.revision,
                });
              }
              setConflict(null);
            }}
          />
        </SheetBody>
      </Sheet>
      <Sheet
        open={!!revision}
        onClose={() => setRevision(null)}
        label={c("历史版本", "Historical revision")}
      >
        <SheetHeader
          title={`V${revision?.revision ?? ""}`}
          onClose={() => setRevision(null)}
        />
        <SheetBody>
          <RichContentRenderer text={revision?.content ?? ""} />
          <Button
            title={c("复制到当前草稿", "Copy into current draft")}
            onPress={() => {
              setContent(revision?.content ?? "");
              setRevision(null);
              setView("edit");
            }}
          />
        </SheetBody>
      </Sheet>
    </FeatureShell>
  );
}
