import React, { useState, useRef, useCallback } from "react";
import { View } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import type { LessonBrief } from "@next-tutor/contracts/classroom";
import { randomUUID } from "expo-crypto";
import { SourcePicker } from "@/features/resources/SourcePicker";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { useAction, useServerQuery } from "@/lib/server-state";
import { useWorkspace } from "@/providers/WorkspaceProvider";
import { takeNativeDraft } from "@/stores/native-handoff";
import { domainRoute } from "@/shell/routes";
import {
  Button,
  Card,
  Field,
  TextField,
  TextArea,
  ListRow,
  SegmentedControl,
  Chip,
} from "@/ui";
import { Body, Hint, Label, Pager, QueryState, Section } from "@/ui/Elements";
import { FeatureShell, CapabilityGate } from "@/ui/FeatureShell";
import { EditSheet } from "@/ui/EditSheet";
export function CoursesScreen() {
  const c = useCopy();
  const router = useRouter();
  const scope = useWorkspace();
  const action = useAction();
  const [page, setPage] = useState(1);
  const [create, setCreate] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const books = useServerQuery(["textbooks"], (signal) =>
    apiClient().library.textbooks.list(signal),
  );
  const sourceFiles = [
    ...new Set([
      ...scope.fileIds,
      ...scope.textbookIds.flatMap(
        (id) =>
          books.data?.textbooks.find((book) => book.id === id)?.file_ids ?? [],
      ),
    ]),
  ];
  const [step, setStep] = useState("source");
  const [topic, setTopic] = useState("");
  const [goals, setGoals] = useState("");
  const [requirements, setRequirements] = useState("");
  const [duration, setDuration] = useState("10");
  const [start, setStart] = useState("automatic");
  const [theme, setTheme] = useState("academic_clear@2");
  const [pedagogy, setPedagogy] = useState("concept_deep@1");
  const creationKey = useRef({ signature: "", key: randomUUID() });
  const [assistantDraftId, setAssistantDraftId] = useState<string | null>(null);
  useFocusEffect(
    useCallback(() => {
      const draft = takeNativeDraft("lesson");
      if (draft?.prefill.kind === "lesson") {
        scope.select(draft.prefill.workspace_id);
        setTopic(draft.prefill.topic);
        setGoals(draft.prefill.objectives ?? "");
        setDuration(String(draft.prefill.duration_minutes ?? 10));
        setAssistantDraftId(draft.draft_id);
        setStep("source");
        setCreate(true);
      }
    }, []),
  );
  const [checkpoint, setCheckpoint] = useState("standard");
  const lessons = useServerQuery(
    ["lessons", scope.id, page],
    (s) =>
      apiClient().classroom.listLessons(scope.id!, { page, pageSize: 10 }, s),
    { enabled: !!scope.id, poll: 6000 },
  );
  const templates = useServerQuery(
    ["classroom-templates"],
    (s) => apiClient().classroom.templates(undefined, s),
    { enabled: create },
  );
  return (
    <FeatureShell title={c("课堂", "Courses")}>
      <CapabilityGate name="classroom">
        <Body>
          {!scope.id ? (
            <Card style={{ gap: 12, padding: 24 }}>
              <Label>
                {c("把课堂放进你的学习空间", "A home for your lessons")}
              </Label>
              <Hint>
                {c(
                  "选择工作区，集中管理课程与学习记录。",
                  "Choose a workspace to organize lessons and records.",
                )}
              </Hint>
              <Button
                title={c("选择工作区", "Choose workspace")}
                onPress={scope.open}
              />
            </Card>
          ) : (
            <>
              <Card style={{ gap: 12, padding: 24 }}>
                <Label>
                  {c("跟着理解，一页一页前进", "Learn, one page at a time")}
                </Label>
                <Hint>
                  {c(
                    "从教材或一个主题创建课程，按自己的节奏学习。",
                    "Create a lesson from materials or a topic, and learn at your own pace.",
                  )}
                </Hint>
                <Button
                  title={c("创建课程", "Create a lesson")}
                  onPress={() => {
                    setAssistantDraftId(null);
                    setCreate(true);
                  }}
                />
              </Card>
              {lessons.data?.resume ? (
                <Card style={{ gap: 12 }}>
                  <Label>{lessons.data.resume.title}</Label>
                  <Button
                    title={c("继续上次课堂", "Resume lesson")}
                    onPress={() =>
                      router.push(
                        domainRoute({
                          kind: "lesson",
                          id: lessons.data!.resume!.lesson_id,
                          runId: lessons.data!.resume!.run.run_id,
                          workspaceId: scope.id!,
                        }),
                      )
                    }
                  />
                </Card>
              ) : null}
              <QueryState query={lessons} empty={!lessons.data?.items.length}>
                {lessons.data?.items.map((l) => (
                  <Card key={l.lesson_id} style={{ padding: 8 }}>
                    <ListRow
                      title={l.title}
                      subtitle={l.extra?.chapter_label}
                      badge={{
                        label:
                          l.status === "ready"
                            ? c("可学习", "Ready")
                            : (l.latest_job?.phase ?? l.status),
                      }}
                      onPress={() =>
                        router.push(
                          domainRoute({
                            kind: "lesson",
                            id: l.lesson_id,
                            workspaceId: scope.id!,
                          }),
                        )
                      }
                    />
                  </Card>
                ))}
                <Pager
                  page={page}
                  total={lessons.data?.total ?? 0}
                  pageSize={10}
                  onChange={setPage}
                />
              </QueryState>
            </>
          )}
        </Body>
        <SourcePicker
          open={sourcesOpen}
          onClose={() => setSourcesOpen(false)}
        />
        <EditSheet
          open={create}
          title={c("设计一节课程", "Design a lesson")}
          onClose={() => setCreate(false)}
          pending={action.pending}
          disabled={
            topic.trim().length < 2 ||
            !scope.id ||
            (scope.textbookIds.length > 0 && !books.isSuccess)
          }
          saveLabel={c("确认并生成", "Create lesson")}
          onSave={() =>
            void action.run(
              async () => {
                const signature = JSON.stringify([
                  scope.id,
                  topic,
                  goals,
                  duration,
                  requirements,
                  theme,
                  pedagogy,
                  checkpoint,
                  start,
                  sourceFiles,
                ]);
                if (creationKey.current.signature !== signature)
                  creationKey.current = { signature, key: randomUUID() };
                const result = await apiClient().classroom.createLesson(
                  scope.id!,
                  {
                    brief: {
                      topic: topic.trim(),
                      goals: goals.split("\n").filter(Boolean),
                      duration_minutes: Number(duration) as NonNullable<
                        LessonBrief["duration_minutes"]
                      >,
                      custom_requirements: requirements,
                      theme_id: theme as NonNullable<LessonBrief["theme_id"]>,
                      pedagogy_id: pedagogy as NonNullable<
                        LessonBrief["pedagogy_id"]
                      >,
                      checkpoint_density: checkpoint as NonNullable<
                        LessonBrief["checkpoint_density"]
                      >,
                      source_policy: sourceFiles.length
                        ? "strict_textbook"
                        : "web_topic",
                      source_selection: {
                        files: sourceFiles.map((file_id) => ({ file_id })),
                      },
                      voice_preferences: {
                        policy: "cloud",
                        allow_local_fallback: false,
                      },
                    },
                    start_mode: start as "automatic" | "outline_first",
                  },
                  creationKey.current.key,
                );
                if (assistantDraftId)
                  await apiClient()
                    .assistant.consumeDraft(assistantDraftId, {
                      kind: "lesson",
                      id: result.lesson_id,
                    })
                    .catch(() => {});
                creationKey.current.signature = "";
                return result;
              },
              (r) => {
                setCreate(false);
                router.push(
                  domainRoute({
                    kind: "lesson",
                    id: r.lesson_id,
                    workspaceId: scope.id!,
                  }),
                );
              },
            )
          }
        >
          <SegmentedControl
            items={[
              { key: "source", label: c("内容", "Content") },
              { key: "design", label: c("设计", "Design") },
              { key: "delivery", label: c("学习", "Learning") },
            ]}
            active={step}
            onChange={setStep}
          />
          {step === "source" ? (
            <>
              <Field label={c("课程主题", "Topic")}>
                <TextField
                  value={topic}
                  onChangeText={setTopic}
                  maxLength={120}
                  accessibilityLabel={c("课程主题", "Lesson topic")}
                />
              </Field>
              <Field label={c("学习目标（每行一项）", "Goals (one per line)")}>
                <TextArea
                  value={goals}
                  onChangeText={setGoals}
                  accessibilityLabel={c("学习目标", "Learning goals")}
                />
              </Field>
              <Button
                title={c("选择课程来源", "Choose lesson sources")}
                variant="outline"
                onPress={() => setSourcesOpen(true)}
              />
              <Hint>
                {sourceFiles.length
                  ? c(
                      `使用已选 ${sourceFiles.length} 份资料`,
                      `Uses ${sourceFiles.length} selected materials`,
                    )
                  : c(
                      "主题模式。可先从资料页选择文件，再创建教材课程。",
                      "Topic mode. Select files in Materials to build a textbook lesson.",
                    )}
              </Hint>
            </>
          ) : step === "design" ? (
            <>
              <Field label={c("视觉风格", "Visual style")}>
                <View style={{ gap: 8 }}>
                  {templates.data?.themes.map((t) => (
                    <Chip
                      key={t.theme_id}
                      label={c(t.name_zh, t.name_en)}
                      active={theme === t.theme_id}
                      onPress={() => setTheme(t.theme_id)}
                    />
                  ))}
                </View>
              </Field>
              <Field label={c("教学方式", "Teaching approach")}>
                <View style={{ gap: 8 }}>
                  {templates.data?.pedagogy.map((t) => (
                    <Chip
                      key={t.pedagogy_id}
                      label={c(t.name_zh, t.name_en)}
                      active={pedagogy === t.pedagogy_id}
                      onPress={() => setPedagogy(t.pedagogy_id)}
                    />
                  ))}
                </View>
              </Field>
              <Field label={c("补充要求", "Additional requirements")}>
                <TextArea
                  value={requirements}
                  onChangeText={setRequirements}
                  accessibilityLabel={c("课程要求", "Lesson requirements")}
                />
              </Field>
            </>
          ) : (
            <>
              <Field label={c("课程时长", "Duration")}>
                <SegmentedControl
                  items={["5", "10", "15", "20", "30"].map((key) => ({
                    key,
                    label: key + c(" 分钟", " min"),
                  }))}
                  active={duration}
                  onChange={setDuration}
                />
              </Field>
              <Field label={c("生成方式", "Generation")}>
                <SegmentedControl
                  items={[
                    { key: "automatic", label: c("自动生成", "Automatic") },
                    {
                      key: "outline_first",
                      label: c("先看大纲", "Review outline"),
                    },
                  ]}
                  active={start}
                  onChange={setStart}
                />
              </Field>
              <Field label={c("互动密度", "Interaction")}>
                <SegmentedControl
                  items={[
                    { key: "light", label: c("少量", "Low") },
                    { key: "standard", label: c("适中", "Normal") },
                    { key: "none", label: c("不设置", "None") },
                  ]}
                  active={checkpoint}
                  onChange={setCheckpoint}
                />
              </Field>
            </>
          )}
        </EditSheet>
      </CapabilityGate>
    </FeatureShell>
  );
}
