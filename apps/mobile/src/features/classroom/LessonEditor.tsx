import React, { useEffect, useState } from "react";
import { View } from "react-native";
import { randomUUID } from "expo-crypto";
import { File } from "expo-file-system";
import * as ImagePicker from "expo-image-picker";
import type {
  SlideSpec,
  RevisionPublic,
  RevisionOperation,
  SlideBlock,
  InlineSpan,
} from "@next-tutor/contracts/classroom";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { confirm } from "@/lib/feedback";
import { useAction, useServerQuery } from "@/lib/server-state";
import { useDirtyGuard } from "@/lib/useDirtyGuard";
import {
  Button,
  Chip,
  Field,
  TextArea,
  TextField,
  SegmentedControl,
} from "@/ui";
import { Hint, Section } from "@/ui/Elements";
import { EditSheet } from "@/ui/EditSheet";
function spanText(spans: InlineSpan[]) {
  return spans
    .map((s) => ("text" in s ? s.text : "latex" in s ? s.latex : ""))
    .join("");
}
export function LessonEditor({
  open,
  onClose,
  workspaceId,
  lessonId,
  revision,
  slide,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  workspaceId: string;
  lessonId: string;
  revision: RevisionPublic;
  slide: SlideSpec;
  onSaved: () => void;
}) {
  const c = useCopy();
  const action = useAction();
  const [step, setStep] = useState("content");
  const [title, setTitle] = useState(slide.title);
  const [segments, setSegments] = useState(slide.segments);
  const [blocks, setBlocks] = useState(slide.blocks);
  const [instruction, setInstruction] = useState("");
  const [theme, setTheme] = useState(revision.brief.theme_id);
  const templates = useServerQuery(
    ["classroom-templates"],
    (s) => apiClient().classroom.templates(undefined, s),
    { enabled: open },
  );
  useEffect(() => {
    if (open) {
      setTitle(slide.title);
      setSegments(slide.segments.map((s) => ({ ...s })));
      setBlocks(slide.blocks);
      setTheme(revision.brief.theme_id);
    }
  }, [open, slide.slide_id]);
  const dirty =
    open &&
    (title !== slide.title ||
      JSON.stringify(segments) !== JSON.stringify(slide.segments) ||
      JSON.stringify(blocks) !== JSON.stringify(slide.blocks));
  useDirtyGuard(dirty);
  const close = () => {
    if (dirty)
      void confirm(
        c("放弃当前修改？", "Discard these changes?"),
        c("更改还没有保存到课程。", "These changes have not been saved."),
        c("放弃", "Discard"),
      ).then((ok) => {
        if (ok) onClose();
      });
    else onClose();
  };
  async function submit(operation: RevisionOperation) {
    if (
      dirty &&
      operation.op !== "edit_content" &&
      !(await confirm(
        c("先放弃当前草稿？", "Discard the current draft?"),
        c(
          "此操作会从已保存版本生成新版本。",
          "This operation starts from the saved version.",
        ),
        c("继续", "Continue"),
      ))
    )
      return;
    return action.run(
      () =>
        apiClient().classroom.createRevision(
          workspaceId,
          lessonId,
          { base_revision: revision.revision, operation },
          randomUUID(),
        ),
      () => {
        onSaved();
        onClose();
      },
    );
  }
  async function replaceImage(block: SlideBlock) {
    const chosen = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images"],
      quality: 0.9,
    });
    if (chosen.canceled) return;
    const asset = chosen.assets[0];
    if (!asset) return;
    const form = new FormData();
    form.append("file", {
      uri: asset.uri,
      name: asset.fileName ?? "lesson-image.jpg",
      type: asset.mimeType ?? "image/jpeg",
    } as unknown as Blob);
    try {
      await action.run(
        () => apiClient().classroom.uploadAsset(workspaceId, lessonId, form),
        (result) =>
          setBlocks((items) =>
            items.map((b) =>
              b.id === block.id && "asset_id" in b
                ? { ...b, asset_id: result.asset.asset_id }
                : b,
            ),
          ),
      );
    } finally {
      const cached = new File(asset.uri);
      if (cached.exists) cached.delete();
    }
  }
  function editBlock(block: SlideBlock, value: string) {
    setBlocks((items) =>
      items.map((b) => {
        if (b.id !== block.id) return b;
        if ("latex" in b) return { ...b, latex: value };
        if ("spans" in b)
          return { ...b, spans: [{ kind: "text", text: value }] };
        if (b.kind === "bullets")
          return {
            ...b,
            items: value.split("\n").map((text) => [{ kind: "text", text }]),
          };
        if (b.kind === "code") return { ...b, code: value };
        return b;
      }),
    );
  }
  return (
    <EditSheet
      open={open}
      onClose={close}
      title={c("课程编辑", "Lesson editor")}
      pending={action.pending}
      disabled={!title.trim()}
      onSave={() =>
        void submit({
          op: "edit_content",
          changes: [
            {
              op: "replace_slide",
              slide_id: slide.slide_id,
              slide: { ...slide, title, blocks, segments },
            },
          ],
        })
      }
    >
      <Hint>
        {c(
          `编辑 V${revision.revision}。提交会创建新版本，正在播放的课堂仍保留原版本。`,
          `Editing V${revision.revision}. Saving creates a new version; current playback keeps its original version.`,
        )}
      </Hint>
      <SegmentedControl
        items={[
          { key: "content", label: c("内容", "Content") },
          { key: "narration", label: c("讲稿", "Narration") },
          { key: "design", label: c("设计与结构", "Design") },
        ]}
        active={step}
        onChange={setStep}
      />
      {step === "content" ? (
        <>
          <Field label={c("页面标题", "Slide title")}>
            <TextField
              value={title}
              onChangeText={setTitle}
              accessibilityLabel={c("页面标题", "Slide title")}
            />
          </Field>
          {blocks.map((block) => {
            const editable =
              "spans" in block ||
              "latex" in block ||
              block.kind === "bullets" ||
              block.kind === "code";
            const value =
              "spans" in block
                ? spanText(block.spans)
                : "latex" in block
                  ? block.latex
                  : block.kind === "bullets"
                    ? block.items.map(spanText).join("\n")
                    : block.kind === "code"
                      ? block.code
                      : "";
            return (
              <Section
                key={block.id}
                title={c("内容块", "Content block") + " · " + block.id}
              >
                {editable ? (
                  <TextArea
                    value={value}
                    onChangeText={(v) => editBlock(block, v)}
                    accessibilityLabel={
                      c("编辑内容块", "Edit content block") + " " + block.id
                    }
                  />
                ) : (
                  <Hint>
                    {c(
                      "复杂表格、图示与检查点可通过 AI 重写，仍由服务器验证。",
                      "Complex tables, diagrams and checkpoints can be rewritten with AI and remain server validated.",
                    )}
                  </Hint>
                )}
                {block.kind === "image" ? (
                  <Button
                    title={c("替换图片", "Replace image")}
                    variant="outline"
                    loading={action.pending}
                    onPress={() => void replaceImage(block)}
                  />
                ) : null}
              </Section>
            );
          })}
        </>
      ) : step === "narration" ? (
        segments.map((segment, i) => (
          <Field
            key={segment.segment_id}
            label={c(`第 ${i + 1} 段讲稿`, `Narration ${i + 1}`)}
          >
            <TextArea
              value={segment.display_text}
              onChangeText={(value) =>
                setSegments((items) =>
                  items.map((s, j) =>
                    j === i
                      ? { ...s, display_text: value, spoken_text: value }
                      : s,
                  ),
                )
              }
              accessibilityLabel={
                c("编辑讲稿", "Edit narration") + " " + (i + 1)
              }
            />
          </Field>
        ))
      ) : (
        <>
          <Section title={c("课程主题", "Lesson theme")}>
            {templates.data?.themes.map((t) => (
              <Chip
                key={t.theme_id}
                label={c(t.name_zh, t.name_en)}
                active={theme === t.theme_id}
                onPress={() => setTheme(t.theme_id as typeof theme)}
              />
            ))}
            <Button
              title={c("应用主题", "Apply theme")}
              variant="outline"
              onPress={() =>
                void submit({
                  op: "change_theme",
                  theme_id: theme as Extract<
                    RevisionOperation,
                    { theme_id: unknown }
                  >["theme_id"],
                })
              }
            />
          </Section>
          <Section title={c("页面结构", "Slide structure")}>
            <View style={{ gap: 8 }}>
              {revision.slides.map((s, i) => (
                <View key={s.slide_id} style={{ gap: 6 }}>
                  <Hint>{s.order + " · " + s.title}</Hint>
                  <View
                    style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}
                  >
                    {i > 0 ? (
                      <Button
                        title={c("上移", "Move up")}
                        variant="ghost"
                        onPress={() => {
                          const ids = revision.slides.map((p) => p.slide_id);
                          [ids[i - 1], ids[i]] = [ids[i]!, ids[i - 1]!];
                          void submit({
                            op: "edit_content",
                            changes: [{ op: "reorder_slides", page_ids: ids }],
                          });
                        }}
                      />
                    ) : null}
                    <Button
                      title={c("删除此页", "Delete slide")}
                      variant="ghost"
                      disabled={revision.slides.length <= 1}
                      onPress={() =>
                        void confirm(
                          c("删除这页？", "Delete this slide?"),
                          c(
                            "会创建新版本，历史版本会保留。",
                            "A new version will be created; previous versions stay available.",
                          ),
                          c("删除", "Delete"),
                        ).then((ok) => {
                          if (ok)
                            void submit({
                              op: "edit_content",
                              changes: [
                                { op: "delete_slide", slide_id: s.slide_id },
                              ],
                            });
                        })
                      }
                    />
                  </View>
                </View>
              ))}
            </View>
          </Section>
          <Button
            title={c("刷新参考资料", "Refresh references")}
            variant="outline"
            onPress={() =>
              void submit({ op: "refresh_research", scope: "all" })
            }
          />
        </>
      )}
      <Field label={c("让 AI 重写当前页", "Rewrite this slide with AI")}>
        <TextArea
          value={instruction}
          onChangeText={setInstruction}
          accessibilityLabel={c("重写要求", "Rewrite instructions")}
        />
      </Field>
      <Button
        title={c("生成修订", "Generate revision")}
        variant="outline"
        disabled={!instruction.trim()}
        loading={action.pending}
        onPress={() =>
          void submit({
            op: "regenerate_slide",
            slide_id: slide.slide_id,
            instruction,
          })
        }
      />
    </EditSheet>
  );
}
