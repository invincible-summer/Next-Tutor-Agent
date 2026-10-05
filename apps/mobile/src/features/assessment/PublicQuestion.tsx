import React, { useEffect, useRef, useState } from "react";
import { Pressable, View } from "react-native";
import { useQueryClient } from "@tanstack/react-query";
import type { QuestionPublic, TaskResult } from "@next-tutor/api-client";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { useAction, useServerQuery, record, str } from "@/lib/server-state";
import {
  Button,
  Card,
  Field,
  TextArea,
  RichContentRenderer,
  useTheme,
} from "@/ui";
import { Hint, Label, Section } from "@/ui/Elements";
import { SvgCanvas } from "@/ui/SvgCanvas";
import { useAuth } from "@/providers/AuthProvider";
export function Feedback({
  result,
  feedback,
}: {
  result: TaskResult | null | undefined;
  feedback?: string | undefined;
}) {
  const c = useCopy();
  return (
    <Card style={{ gap: 12 }}>
      <Label>{c("反馈与下一步", "Feedback and next steps")}</Label>
      {!result?.verdict && !feedback && !result?.feedback ? (
        <Hint>
          {c(
            "回答已保存，等待服务端整理反馈。",
            "Your answer is saved. Feedback is being prepared by your server.",
          )}
        </Hint>
      ) : null}
      {feedback ? <RichContentRenderer text={feedback} /> : null}
      {result?.verdict ? (
        <Hint>
          {c(
            result.verdict === "correct"
              ? "本次回答正确"
              : result.verdict === "partial"
                ? "本次回答部分正确"
                : "本次回答需要再想一想",
            result.verdict === "correct"
              ? "Correct on this question"
              : result.verdict === "partial"
                ? "Partially correct on this question"
                : "Revisit this question",
          )}
        </Hint>
      ) : null}
      {result?.feedback
        ? Object.values(result.feedback)
            .filter(Boolean)
            .map((t, i) => <RichContentRenderer key={i} text={t} />)
        : null}
      {result?.first_error?.description ? (
        <Hint>{result.first_error.description}</Hint>
      ) : null}
    </Card>
  );
}
export function PublicQuestion({
  question,
  active = true,
  submitted = false,
  pending = false,
  allowEnrichment = false,
  onSubmit,
  onHint,
}: {
  question: QuestionPublic;
  active?: boolean;
  submitted?: boolean;
  pending?: boolean;
  /** Only the current CAT question may initiate supplemental illustration generation. */
  allowEnrichment?: boolean;
  onSubmit: (answer: string) => Promise<void> | void;
  onHint?: () => Promise<string>;
}) {
  const c = useCopy();
  const action = useAction();
  const { theme } = useTheme();
  const cache = useQueryClient();
  const { owner } = useAuth();
  const [choice, setChoice] = useState("");
  const [text, setText] = useState("");
  const [hint, setHint] = useState("");
  const [polling, setPolling] = useState(true);
  const [enrichmentInterrupted, setEnrichmentInterrupted] = useState(false);
  const started = useRef("");
  useEffect(() => {
    setChoice("");
    setText("");
    setHint("");
    setPolling(true);
    setEnrichmentInterrupted(false);
  }, [question.question_id, question.question_revision]);
  const figure = useServerQuery(
    ["quiz-figure", question.question_id, question.question_revision],
    (s) =>
      apiClient().illustration.frozen(
        question.question_id,
        question.question_revision,
        s,
      ),
    {
      enabled:
        !question.illustration &&
        (allowEnrichment ||
          (!!question.visual_role && question.visual_role !== "none")),
      poll: active && polling ? 4000 : 0,
    },
  );
  useEffect(() => {
    if (figure.data && !["queued", "running"].includes(figure.data.status))
      setPolling(false);
  }, [figure.data]);
  useEffect(() => {
    const identity = `${owner}:${question.question_id}:${question.question_revision}`;
    if (
      !allowEnrichment ||
      !active ||
      submitted ||
      pending ||
      question.illustration ||
      !figure.data ||
      figure.data.status !== "not_required" ||
      started.current === identity
    )
      return;
    started.current = identity;
    const controller = new AbortController();
    void action
      .run(
        () =>
          apiClient().illustration.start(
            question.question_id,
            question.question_revision,
            { signal: controller.signal },
          ),
        (result) => {
          cache.setQueryData(
            [
              owner,
              "quiz-figure",
              question.question_id,
              question.question_revision,
            ],
            result,
          );
          setPolling(["queued", "running"].includes(result.status));
        },
      )
      .then((result) => {
        if (result === undefined && !controller.signal.aborted)
          setEnrichmentInterrupted(true);
      });
    return () => controller.abort();
  }, [
    active,
    allowEnrichment,
    submitted,
    pending,
    question.question_id,
    question.question_revision,
    owner,
    figure.data?.status,
  ]);
  const illustration =
    question.illustration || record(figure.data?.illustration);
  const svg = str(illustration.svg);
  const required = question.visual_role === "essential" && !svg;
  const answer =
    question.input_spec.kind === "choice"
      ? choice + (question.input_spec.requires_explanation ? "\n" + text : "")
      : text;
  const bytes = new TextEncoder().encode(answer).length;
  const answerReady =
    question.input_spec.kind === "choice"
      ? !!choice && (!question.input_spec.requires_explanation || !!text.trim())
      : !!text.trim();
  const imageStatus = required
    ? c(
        "题图是作答必需条件，准备好后即可作答。",
        "The illustration is required before answering.",
      )
    : figure.isError || enrichmentInterrupted
      ? c(
          "暂时无法读取题图状态，可以重试。",
          "The illustration status could not be loaded. Try again.",
        )
      : figure.data?.status === "failed"
        ? c(
            "题图暂未完成，文字题仍可独立作答。",
            "The illustration did not complete. The text question can still be answered.",
          )
        : figure.data?.status === "not_required" && !action.pending
          ? c(
              "此题不需要补充题图，可以直接作答。",
              "No supplemental image is needed for this question.",
            )
          : c(
              "补充题图正在准备，文字题可以独立作答。",
              "The text question can be answered while its supplemental image is prepared.",
            );
  return (
    <View style={{ gap: 16 }}>
      <Card style={{ gap: 16, padding: 20 }}>
        <Hint>{question.source_badge}</Hint>
        <RichContentRenderer text={question.stem} />
        {svg ? (
          <SvgCanvas svg={svg} alt={str(illustration.alt)} height={280} />
        ) : (question.visual_role && question.visual_role !== "none") ||
          allowEnrichment ? (
          <View style={{ gap: 8 }}>
            <Hint>{imageStatus}</Hint>
            {figure.isError ? (
              <Button
                title={c("重新读取题图", "Reload illustration")}
                variant="outline"
                onPress={() => void figure.refetch()}
              />
            ) : null}
            {active &&
            !submitted &&
            allowEnrichment &&
            enrichmentInterrupted ? (
              <Button
                title={c("恢复题图任务", "Resume illustration task")}
                variant="outline"
                loading={action.pending}
                disabled={pending}
                onPress={() =>
                  void action.run(
                    async () => {
                      const current = await apiClient().illustration.frozen(
                        question.question_id,
                        question.question_revision,
                      );
                      if (current.status !== "not_required") return current;
                      return apiClient().illustration.start(
                        question.question_id,
                        question.question_revision,
                      );
                    },
                    (result) => {
                      cache.setQueryData(
                        [
                          owner,
                          "quiz-figure",
                          question.question_id,
                          question.question_revision,
                        ],
                        result,
                      );
                      setPolling(["queued", "running"].includes(result.status));
                      setEnrichmentInterrupted(false);
                    },
                  )
                }
              />
            ) : null}
            {active &&
            !submitted &&
            figure.data?.status === "failed" &&
            (record(record(figure.data).failure).retryable ||
              figure.data.retryable) ? (
              <Button
                title={c("重试题图", "Retry image")}
                variant="outline"
                loading={action.pending}
                disabled={pending}
                onPress={() =>
                  void action.run(
                    () =>
                      str(record(figure.data).job_id)
                        ? apiClient().illustration.retry(
                            str(record(figure.data).job_id),
                          )
                        : apiClient().illustration.start(
                            question.question_id,
                            question.question_revision,
                          ),
                    (result) => {
                      cache.setQueryData(
                        [
                          owner,
                          "quiz-figure",
                          question.question_id,
                          question.question_revision,
                        ],
                        result,
                      );
                      setPolling(["queued", "running"].includes(result.status));
                    },
                  )
                }
              />
            ) : null}
          </View>
        ) : null}
        {question.input_spec.kind === "choice" ? (
          <View style={{ gap: 10 }}>
            {Object.entries(question.options).map(([key, value]) => (
              <Pressable
                key={key}
                accessible
                accessibilityRole="radio"
                accessibilityState={{
                  selected: choice === key,
                  disabled: pending || submitted || required || !active,
                }}
                disabled={pending || submitted || required || !active}
                onPress={() => setChoice(key)}
                accessibilityLabel={`${key}: ${value}`}
                style={{
                  minHeight: 52,
                  padding: 16,
                  borderRadius: 14,
                  borderWidth: choice === key ? 2 : 1,
                  borderColor:
                    choice === key ? theme.colors.accent : theme.colors.border,
                  backgroundColor:
                    choice === key
                      ? theme.colors["accent-soft"]
                      : theme.colors.surface,
                  opacity:
                    pending || submitted || required || !active ? 0.6 : 1,
                }}
              >
                <View pointerEvents="none">
                  <RichContentRenderer text={`${key}. ${value}`} />
                </View>
              </Pressable>
            ))}
          </View>
        ) : null}
        {question.input_spec.kind === "text" ||
        question.input_spec.requires_explanation ? (
          <Field
            label={c("你的回答", "Your answer")}
            hint={
              question.input_spec.requires_explanation
                ? c(
                    "请写出选择与思考过程。",
                    "Explain your choice and reasoning.",
                  )
                : undefined
            }
          >
            <TextArea
              testID="question-answer"
              accessibilityLabel={c("你的回答", "Your answer")}
              value={text}
              onChangeText={setText}
              editable={active && !submitted && !pending && !required}
              placeholder={c("写下你的思考…", "Write down your thinking…")}
            />
          </Field>
        ) : null}
        {bytes > question.input_spec.max_bytes ? (
          <Hint>
            {c(
              "回答超过长度限制，请精简后提交。",
              "Your answer is too long. Please shorten it.",
            )}
          </Hint>
        ) : null}
        <Button
          testID="question-submit"
          title={
            submitted
              ? c("已提交", "Submitted")
              : c("提交回答", "Submit answer")
          }
          disabled={
            !answerReady ||
            bytes > question.input_spec.max_bytes ||
            required ||
            submitted ||
            !active
          }
          loading={pending}
          onPress={() => void onSubmit(answer)}
        />
        {active && question.hints_available && onHint && !submitted ? (
          <Button
            title={c("给我一点提示", "A small hint")}
            variant="ghost"
            loading={action.pending}
            disabled={pending}
            onPress={() => void action.run(onHint, setHint)}
          />
        ) : null}
        {hint ? <RichContentRenderer text={hint} /> : null}
      </Card>
    </View>
  );
}
