import React, { useEffect, useRef, useState } from "react";
import { ScrollView, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { randomUUID } from "expo-crypto";
import type { QuestionPublic, TaskResult } from "@next-tutor/api-client";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { confirm } from "@/lib/feedback";
import { useAction, useServerQuery, record } from "@/lib/server-state";
import { shareBytes } from "@/platform/files";
import { useWorkspace } from "@/providers/WorkspaceProvider";
import { useAdaptive } from "@/shell/adaptive/window-class";
import { AdaptivePane } from "@/shell/adaptive/AdaptivePane";
import { domainRoute } from "@/shell/routes";
import {
  Button,
  Card,
  TextArea,
  RichContentRenderer,
  ListRow,
  Sheet,
} from "@/ui";
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
import { EditSheet } from "@/ui/EditSheet";
import { PublicQuestion, Feedback } from "@/features/assessment/PublicQuestion";
import { LessonFrame } from "./LessonFrame";
import { LessonEditor } from "./LessonEditor";
import { checkpointQuestionRef } from "./frame-document";
import { useLessonPlayback } from "./useLessonPlayback";
export function LessonScreen() {
  const params = useLocalSearchParams<{
    lessonId: string;
    runId?: string;
    ws?: string;
  }>();
  const id = params.lessonId;
  const c = useCopy();
  const router = useRouter();
  const scope = useWorkspace();
  const adaptive = useAdaptive();
  const action = useAction();
  const [runId, setRunId] = useState(params.runId ?? "");
  const [checkpointId, setCheckpointId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<TaskResult | null>(null);
  const [answerDone, setAnswerDone] = useState(false);
  const [note, setNote] = useState("");
  const [noteOpen, setNoteOpen] = useState(false);
  const [edit, setEdit] = useState(false);
  const [script, setScript] = useState(false);
  const [revisionsOpen, setRevisionsOpen] = useState(false);
  const [selectedRevision, setSelectedRevision] = useState<
    number | undefined
  >();
  const submitKey = useRef(randomUUID());
  useEffect(() => {
    if (params.ws) scope.select(params.ws);
  }, [params.ws]);
  const run = useServerQuery(
    ["classroom-run", scope.id, id, runId],
    (s) => apiClient().classroom.getRun(scope.id!, id, runId, s),
    { enabled: !!scope.id && !!runId },
  );
  const revision = runId ? run.data?.lesson_revision : selectedRevision;
  const lesson = useServerQuery(
    ["lesson", scope.id, id, revision],
    (s) => apiClient().classroom.getLesson(scope.id!, id, revision, s),
    { enabled: !!scope.id && (!runId || !!run.data), poll: 5000 },
  );
  const frame = useServerQuery(
    ["lesson-frame", scope.id, id, lesson.data?.revision?.revision],
    () =>
      apiClient().classroom.getRevisionFrame(
        scope.id!,
        id,
        lesson.data!.revision!.revision,
      ),
    { enabled: !!scope.id && !!lesson.data?.revision },
  );
  const job = useServerQuery(
    ["classroom-job", scope.id, id, lesson.data?.latest_job?.job_id],
    (s) =>
      apiClient().classroom.getJob(
        scope.id!,
        id,
        lesson.data!.latest_job!.job_id,
        s,
      ),
    { enabled: !!scope.id && !!lesson.data?.latest_job, poll: 3000 },
  );
  const preview = useServerQuery(
    ["classroom-outline", scope.id, id, job.data?.job_id, job.data?.state],
    (s) =>
      apiClient().classroom.getJobPreview(
        scope.id!,
        id,
        job.data!.job_id,
        undefined,
        s,
      ),
    { enabled: !!scope.id && job.data?.state === "awaiting_outline" },
  );
  const checkpoint = useServerQuery(
    ["classroom-checkpoint", runId, checkpointId],
    (s) =>
      apiClient().classroom.getCheckpoint(
        scope.id!,
        id,
        runId,
        checkpointId!,
        s,
      ),
    { enabled: !!scope.id && !!runId && !!checkpointId },
  );
  const submission = useServerQuery(
    ["checkpoint-submission", runId, checkpointId],
    (s) =>
      apiClient().classroom.getCheckpointSubmission(
        scope.id!,
        id,
        runId,
        checkpointId!,
        s,
      ),
    { enabled: !!scope.id && !!runId && !!checkpointId },
  );
  const history = useServerQuery(
    ["lesson-revisions", scope.id, id],
    () => apiClient().classroom.listRevisions(scope.id!, id),
    { enabled: !!scope.id && revisionsOpen },
  );
  const slides = lesson.data?.revision?.slides ?? [];
  const playback = useLessonPlayback(
    scope.id,
    id,
    runId,
    slides,
    run,
    lesson.data?.title ?? "Next Tutor",
    async (slideId) => {
      for (const cp of lesson.data?.revision?.checkpoints.filter(
        (cp) => cp.slide_id === slideId,
      ) ?? []) {
        const state = await apiClient().classroom.getCheckpoint(
          scope.id!,
          id,
          runId,
          cp.checkpoint_id,
        );
        if (state.run_state !== "answered" && state.run_state !== "skipped") {
          openCheckpoint(cp.checkpoint_id);
          return true;
        }
      }
      return false;
    },
  );
  const { index, segmentIndex } = playback;
  const slide = slides[index];
  const segment = slide?.segments[segmentIndex];
  const running =
    run.data?.status === "active" || run.data?.status === "paused";
  useEffect(() => {
    if (checkpoint.data?.run_state === "answered") {
      setAnswerDone(true);
      const result = record(submission.data?.submission).task_result;
      if (result && typeof result === "object")
        setFeedback(result as TaskResult);
    }
  }, [checkpoint.data, submission.data]);
  async function start() {
    const result = await apiClient().classroom.createRun(
      scope.id!,
      id,
      {
        lesson_revision: lesson.data?.revision?.revision ?? null,
        mode: "resume_or_create",
        voice_preferences: { policy: "cloud", allow_local_fallback: false },
      },
      randomUUID(),
    );
    setRunId(result.run_id);
    router.setParams({ runId: result.run_id });
  }
  function openCheckpoint(value: string) {
    playback.stop();
    setCheckpointId(value);
    setFeedback(null);
    setAnswerDone(false);
    submitKey.current = randomUUID();
  }
  const narrative = (
    <Body>
      <Section title={c("当前讲稿", "Narration")}>
        <RichContentRenderer
          text={
            segment?.display_text ||
            slide?.segments.map((s) => s.display_text).join("\n\n") ||
            ""
          }
        />
      </Section>
      {slide?.segments.map((s, i) => (
        <ListRow
          key={s.segment_id}
          title={c(`讲稿 ${i + 1}`, `Segment ${i + 1}`)}
          subtitle={s.display_text}
          onPress={() => void action.run(() => playback.jump(index, i))}
        />
      ))}
      <Button
        title={c("课堂随记", "Make a note")}
        variant="outline"
        disabled={!runId}
        onPress={() => setNoteOpen(true)}
      />
      {lesson.data?.revision?.checkpoints
        .filter((cp) => cp.slide_id === slide?.slide_id)
        .map((cp) => (
          <Button
            key={cp.checkpoint_id}
            title={c("互动检查点", "Checkpoint")}
            variant="outline"
            disabled={!runId}
            onPress={() => openCheckpoint(cp.checkpoint_id)}
          />
        ))}
      {running ? (
        <Button
          title={c("课堂插问", "Ask a question")}
          variant="ghost"
          onPress={() => {
            playback.stop();
            void action.run(
              () =>
                apiClient().classroom.ensureQaSession(
                  scope.id!,
                  id,
                  runId,
                  "qa-" + runId,
                ),
              (r) =>
                router.push(
                  domainRoute({
                    kind: "chat",
                    id: r.session_id,
                    workspaceId: scope.id!,
                  }),
                ),
            );
          }}
        />
      ) : null}
    </Body>
  );
  const question = checkpoint.data?.question as unknown as
    QuestionPublic | undefined;
  const questionRef = checkpoint.data?.question
    ? checkpointQuestionRef(checkpoint.data.question)
    : null;
  const statusNames = {
    active: c("进行中", "Active"),
    paused: c("已暂停", "Paused"),
    completed: c("已完成", "Completed"),
    ended: c("已结束", "Ended"),
  };
  return (
    <FeatureShell
      title={lesson.data?.title || c("课堂", "Lesson")}
      scroll={false}
      right={
        <Button
          title={c("讲稿", "Narration")}
          variant="ghost"
          onPress={() => setScript(true)}
        />
      }
    >
      {!scope.id ? (
        <Body>
          <Button
            title={c("选择工作区", "Choose workspace")}
            onPress={scope.open}
          />
        </Body>
      ) : (
        <AdaptivePane
          master={
            adaptive.maxPanes === 3 ? (
              <ScrollView>
                <Body>
                  {slides.map((s, i) => (
                    <ListRow
                      key={s.slide_id}
                      title={s.title}
                      subtitle={String(s.order)}
                      onPress={() => void action.run(() => playback.jump(i, 0))}
                    />
                  ))}
                </Body>
              </ScrollView>
            ) : undefined
          }
          inspector={<ScrollView>{narrative}</ScrollView>}
        >
          <ScrollView keyboardShouldPersistTaps="handled">
            <Body>
              <QueryState query={runId && !run.data ? run : lesson}>
                {slide ? (
                  <QueryState query={frame}>
                    {frame.data ? (
                      <LessonFrame
                        key={lesson.data?.revision?.revision}
                        html={frame.data}
                        order={slide.order}
                        slides={slides}
                        height={
                          adaptive.compactHeight
                            ? Math.max(160, adaptive.height - 180)
                            : Math.min(
                                520,
                                Math.max(
                                  220,
                                  (adaptive.width -
                                    (adaptive.isCompact ? 40 : 160)) *
                                    0.55,
                                ),
                              )
                        }
                        onPage={(order) => {
                          const next = slides.findIndex(
                            (s) => s.order === order,
                          );
                          if (next >= 0 && next !== index)
                            void action.run(() => playback.jump(next, 0));
                        }}
                      />
                    ) : null}
                  </QueryState>
                ) : null}
                {slide ? (
                  <Card style={{ gap: 12 }}>
                    <Hint>
                      {c(
                        `第 ${index + 1} / ${slides.length} 页 · V${lesson.data?.revision?.revision}`,
                        `Slide ${index + 1} / ${slides.length} · V${lesson.data?.revision?.revision}`,
                      )}
                    </Hint>
                    <Label>{slide.title}</Label>
                    <View
                      style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}
                    >
                      <Button
                        title={c("上一页", "Previous")}
                        variant="outline"
                        disabled={index === 0 || action.pending}
                        onPress={() =>
                          void action.run(() => playback.jump(index - 1, 0))
                        }
                      />
                      <Button
                        title={
                          !runId
                            ? c("进入课堂", "Enter lesson")
                            : !running
                              ? c("查看讲稿", "Read narration")
                              : playback.audio.playing || playback.buffering
                                ? c("暂停", "Pause")
                                : c("播放讲稿", "Play narration")
                        }
                        loading={action.pending}
                        onPress={() => {
                          if (!runId) void action.run(start);
                          else if (!running) setScript(true);
                          else void action.run(playback.toggle);
                        }}
                      />
                      <Button
                        title={c("下一页", "Next")}
                        variant="outline"
                        disabled={index >= slides.length - 1 || action.pending}
                        onPress={() =>
                          void action.run(() => playback.jump(index + 1, 0))
                        }
                      />
                    </View>
                    {run.data ? (
                      <Hint>{statusNames[run.data.status]}</Hint>
                    ) : null}
                    {playback.buffering ? (
                      <Hint>
                        {c(
                          "正在准备云端音频，随时可以暂停。",
                          "Preparing cloud audio. You can pause at any time.",
                        )}
                      </Hint>
                    ) : null}
                  </Card>
                ) : null}
                {run.data?.lease.held ? (
                  <Button
                    title={c("接管其他设备的课堂", "Take over playback")}
                    variant="ghost"
                    onPress={() =>
                      void action.run(() => playback.acquire(true))
                    }
                  />
                ) : null}
                {adaptive.maxPanes === 1 && !adaptive.compactHeight
                  ? narrative
                  : null}
                {job.data &&
                job.data.state !== "succeeded" &&
                job.data.state !== "cancelled" ? (
                  <Card style={{ gap: 12 }}>
                    <Label>{c("课程生成", "Lesson generation")}</Label>
                    <Hint>{job.data.phase ?? job.data.state}</Hint>
                    {preview.data?.outline ? (
                      <>
                        <Hint>{preview.data.outline.scope_note}</Hint>
                        {preview.data.outline.pages.map((p) => (
                          <Label key={p.order}>{p.title}</Label>
                        ))}
                        <Button
                          title={c(
                            "确认大纲并继续",
                            "Approve outline and continue",
                          )}
                          onPress={() =>
                            void action.run(() =>
                              apiClient().classroom.continueJob(
                                scope.id!,
                                id,
                                job.data!.job_id,
                                {
                                  expected_state_revision:
                                    job.data!.state_revision,
                                },
                              ),
                            )
                          }
                        />
                      </>
                    ) : null}
                    {job.data.state === "failed" ? (
                      <Button
                        title={c("重试生成", "Retry generation")}
                        onPress={() =>
                          void action.run(() =>
                            apiClient().classroom.retryJob(
                              scope.id!,
                              id,
                              job.data!.job_id,
                              {
                                expected_state_revision:
                                  job.data!.state_revision,
                              },
                              randomUUID(),
                            ),
                          )
                        }
                      />
                    ) : (
                      <Button
                        title={c("停止生成", "Cancel generation")}
                        variant="ghost"
                        onPress={() =>
                          void action.run(() =>
                            apiClient().classroom.cancelJob(
                              scope.id!,
                              id,
                              job.data!.job_id,
                              {
                                expected_state_revision:
                                  job.data!.state_revision,
                              },
                            ),
                          )
                        }
                      />
                    )}
                  </Card>
                ) : null}
                {slide ? (
                  <Section title={c("课程与版本", "Lesson and versions")}>
                    <Button
                      title={c("编辑课程", "Edit lesson")}
                      variant="outline"
                      onPress={() => setEdit(true)}
                    />
                    <Button
                      title={c("查看历史版本", "Version history")}
                      variant="ghost"
                      onPress={() => setRevisionsOpen(true)}
                    />
                    <View
                      style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}
                    >
                      {(["notes_md", "html_zip"] as const).map((format) => (
                        <Button
                          key={format}
                          title={
                            format === "notes_md"
                              ? c("导出讲稿", "Export notes")
                              : c("导出课件", "Export slides")
                          }
                          variant="ghost"
                          onPress={() =>
                            void action.run(async () => {
                              const r =
                                await apiClient().classroom.createExport(
                                  scope.id!,
                                  id,
                                  {
                                    revision: lesson.data!.revision!.revision,
                                    format,
                                  },
                                  randomUUID(),
                                );
                              await shareBytes(
                                await apiClient().classroom.exportContent(
                                  scope.id!,
                                  id,
                                  r.export_id,
                                ),
                                lesson.data!.title +
                                  (format === "notes_md" ? ".md" : ".zip"),
                                format === "notes_md"
                                  ? "text/markdown"
                                  : "application/zip",
                              );
                            })
                          }
                        />
                      ))}
                    </View>
                  </Section>
                ) : null}
                {runId ? (
                  <Button
                    title={
                      running
                        ? c("完成课堂并保存笔记", "Finish and save notes")
                        : c("保存课堂笔记", "Save lesson notes")
                    }
                    variant="outline"
                    loading={action.pending}
                    onPress={() =>
                      void action.run(
                        async () => {
                          playback.stop();
                          if (running)
                            await playback.progress(
                              index,
                              segmentIndex,
                              "complete",
                            );
                          return apiClient().classroom.saveRunNote(
                            scope.id!,
                            id,
                            runId,
                            { include_user_notes: true },
                            randomUUID(),
                          );
                        },
                        (r) =>
                          router.push(
                            domainRoute({ kind: "note", id: r.note_id }),
                          ),
                      )
                    }
                  />
                ) : null}
                <Button
                  title={c("归档课程", "Archive lesson")}
                  variant="ghost"
                  onPress={() =>
                    void confirm(
                      c("归档这门课程？", "Archive this lesson?"),
                      c(
                        "可以从「我的 → 归档」恢复。",
                        "You can restore it from Me → Archive.",
                      ),
                      c("归档", "Archive"),
                    ).then((ok) => {
                      if (ok)
                        void action.run(
                          () =>
                            apiClient().classroom.archiveLesson(scope.id!, id),
                          () => router.replace("/(main)/learn/courses"),
                        );
                    })
                  }
                />
              </QueryState>
            </Body>
          </ScrollView>
        </AdaptivePane>
      )}
      <Sheet
        open={script}
        onClose={() => setScript(false)}
        label={c("讲稿", "Narration")}
      >
        <SheetHeader
          title={c("讲稿", "Narration")}
          onClose={() => setScript(false)}
        />
        <SheetBody>{narrative}</SheetBody>
      </Sheet>
      <Sheet
        open={!!checkpointId}
        onClose={() => setCheckpointId(null)}
        label={c("互动检查点", "Checkpoint")}
      >
        <SheetHeader
          title={c("互动检查点", "Checkpoint")}
          onClose={() => setCheckpointId(null)}
        />
        <SheetBody>
          <QueryState query={checkpoint}>
            {question && questionRef && question.input_spec ? (
              <PublicQuestion
                question={question}
                active={running}
                submitted={
                  answerDone || checkpoint.data?.run_state === "answered"
                }
                pending={action.pending}
                onSubmit={(answer) =>
                  action
                    .run(
                      () =>
                        apiClient().classroom.submitCheckpoint(
                          scope.id!,
                          id,
                          runId,
                          checkpointId!,
                          {
                            question_ref: questionRef,
                            student_answer: answer,
                            idempotency_key: submitKey.current,
                          },
                        ),
                      (r) => {
                        setAnswerDone(true);
                        setFeedback(r.task_result as TaskResult | null);
                        void run.refetch();
                        void checkpoint.refetch();
                        void submission.refetch();
                      },
                    )
                    .then(() => {})
                }
                onHint={() =>
                  apiClient()
                    .classroom.checkpointHint(
                      scope.id!,
                      id,
                      runId,
                      checkpointId!,
                      randomUUID(),
                    )
                    .then((r) => r.hint)
                }
              />
            ) : (
              <RichContentRenderer text={checkpoint.data?.prompt ?? ""} />
            )}
            {feedback ? <Feedback result={feedback} /> : null}
            {checkpoint.data?.run_state === "skipped" ? (
              <Hint>{c("已跳过此检查点", "This checkpoint was skipped")}</Hint>
            ) : null}
            {running &&
            checkpoint.data?.optional &&
            checkpoint.data.run_state !== "answered" ? (
              <Button
                title={c("跳过此检查点", "Skip checkpoint")}
                variant="ghost"
                onPress={() =>
                  void action.run(
                    () =>
                      apiClient().classroom.skipCheckpoint(
                        scope.id!,
                        id,
                        runId,
                        checkpointId!,
                        run.data?.state_revision,
                      ),
                    () => setCheckpointId(null),
                  )
                }
              />
            ) : null}
          </QueryState>
        </SheetBody>
      </Sheet>
      <Sheet
        open={revisionsOpen}
        onClose={() => setRevisionsOpen(false)}
        label={c("历史版本", "Version history")}
      >
        <SheetHeader
          title={c("历史版本", "Version history")}
          onClose={() => setRevisionsOpen(false)}
        />
        <SheetBody>
          <Hint>
            {runId
              ? c(
                  "此课堂固定使用开始时的版本。新版本可从课程入口开启新课堂。",
                  "Playback keeps its starting version. Open a new run from the course to use newer versions.",
                )
              : c("选择一个版本阅读。", "Choose a version to read.")}
          </Hint>
          <QueryState query={history}>
            {history.data?.items.map((r) => (
              <ListRow
                key={r.revision}
                title={"V" + r.revision}
                subtitle={r.created_at}
                onPress={() => {
                  if (!runId) {
                    setSelectedRevision(r.revision);
                    setRevisionsOpen(false);
                  }
                }}
              />
            ))}
          </QueryState>
        </SheetBody>
      </Sheet>
      <EditSheet
        open={noteOpen}
        title={c("课堂随记", "Lesson note")}
        onClose={() => setNoteOpen(false)}
        onSave={() =>
          void action.run(
            () =>
              apiClient().classroom.addRunNote(scope.id!, id, runId, {
                slide_id: slide!.slide_id,
                segment_id: segment?.segment_id ?? null,
                user_text: note,
              }),
            () => {
              setNote("");
              setNoteOpen(false);
            },
          )
        }
        pending={action.pending}
        disabled={!runId || !note.trim()}
      >
        <TextArea
          value={note}
          onChangeText={setNote}
          accessibilityLabel={c("课堂随记", "Lesson note")}
        />
      </EditSheet>
      {edit && slide && lesson.data?.revision && scope.id ? (
        <LessonEditor
          open
          onClose={() => setEdit(false)}
          workspaceId={scope.id}
          lessonId={id}
          revision={lesson.data.revision}
          slide={slide}
          onSaved={() => void lesson.refetch()}
        />
      ) : null}
    </FeatureShell>
  );
}
