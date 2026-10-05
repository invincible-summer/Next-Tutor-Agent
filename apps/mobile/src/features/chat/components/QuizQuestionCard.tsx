import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import * as Haptics from "expo-haptics";
import {
  BookOpen,
  ChevronDown,
  Eye,
  Lightbulb,
  Send,
} from "lucide-react-native";

import { useTheme } from "@/ui/ThemeProvider";
import { alpha } from "@/ui/theme";
import { Badge } from "@/ui/Badge";
import { TextArea } from "@/ui/Input";
import { RichContentRenderer } from "@/ui/rich-content";
import { useI18n } from "@/providers/I18nProvider";
import { apiClient } from "@/lib/api";
import type { QuizQuestion, QuizSourceRef } from "../model/types";
import { registerSessionCleanup } from "@/lib/session-lifecycle";

/** 提交结果（对齐 Web QuizSubmitOutcome 的展示子集）。 */
export interface QuizSubmitOutcome {
  task_result?: { verdict?: string | null } | null;
  evaluation?: { status?: string };
  feedback?: string;
}

interface QuizSubmissionState {
  student_answer: string;
  verdict?: string | null;
  pending?: boolean;
  revealed?: { answer: string; explanation: string };
  evaluation?: { status?: string };
  feedback?: string;
}

/** 可注入传输层：聊天默认走 /quiz/*；课堂检查点走 R/checkpoints/*（M10）。 */
export interface QuizCardTransport {
  submit(body: {
    question_id: string;
    question_revision: number;
    student_answer: string;
    session_id?: string;
  }): Promise<QuizSubmitOutcome>;
  hint(
    questionId: string,
    revision: number,
  ): Promise<{ status: string; hint: string; message?: string }>;
  reveal(
    questionId: string,
    revision: number,
  ): Promise<{
    status: string;
    answer: string;
    explanation: string;
    [key: string]: unknown;
  }>;
  submission(
    questionId: string,
    revision: number,
  ): Promise<{ submission: QuizSubmissionState | null }>;
}

const defaultTransport: QuizCardTransport = {
  submit: (body) =>
    apiClient().assessment.quizRecord(body) as Promise<QuizSubmitOutcome>,
  hint: (qid, rev) => apiClient().assessment.quizHint(qid, rev),
  reveal: (qid, rev) => apiClient().assessment.reveal(qid, rev),
  submission: (qid, rev) =>
    apiClient().assessment.quizSubmission(qid, rev) as Promise<{
      submission: QuizSubmissionState | null;
    }>,
};

// 流式 reconcile 可能短暂重挂题卡：按题身份在内存里留草稿，重挂不丢输入。
const quizAnswerDrafts = new Map<string, string>();
registerSessionCleanup(() => {
  quizAnswerDrafts.clear();
});
const QUIZ_DRAFT_LIMIT = 100;

function rememberQuizDraft(key: string, value: string): void {
  if (!key) return;
  if (!value) {
    quizAnswerDrafts.delete(key);
    return;
  }
  if (!quizAnswerDrafts.has(key) && quizAnswerDrafts.size >= QUIZ_DRAFT_LIMIT) {
    const oldest = quizAnswerDrafts.keys().next().value;
    if (oldest !== undefined) quizAnswerDrafts.delete(oldest);
  }
  quizAnswerDrafts.set(key, value);
}

/** 教材依据定位行：filename · 章节路径 · 页码。 */
function useSourceLocation(ref: QuizSourceRef): string {
  const { t } = useI18n();
  const parts: string[] = [];
  if (ref.filename) parts.push(ref.filename);
  const section = (ref.section_path ?? []).filter(Boolean).join(" · ");
  if (section) parts.push(section);
  if (ref.printed_page != null) {
    parts.push(
      t("tool.knowledge.textbookPage").replace("%n", String(ref.printed_page)),
    );
  } else if (ref.page != null) {
    parts.push(t("tool.knowledge.pdfPage").replace("%n", String(ref.page)));
  }
  return parts.join(" · ");
}

function SourceRefRow({ sourceRef }: { sourceRef: QuizSourceRef }) {
  const { theme } = useTheme();
  const location = useSourceLocation(sourceRef);
  return (
    <View
      style={[
        styles.sourceRef,
        {
          borderColor: theme.colors["border-light"],
          backgroundColor: alpha(theme.colors.bg, 0.6),
        },
      ]}
    >
      <View style={styles.sourceRefHead}>
        <BookOpen size={11} color={theme.colors["accent-strong"]} />
        <Text
          style={[
            theme.type.caption,
            { color: theme.colors["accent-strong"], marginLeft: 4, flex: 1 },
          ]}
          numberOfLines={1}
        >
          {location}
        </Text>
      </View>
      {sourceRef.excerpt ? (
        <Text
          style={[
            theme.type.caption,
            { color: theme.colors.muted, marginTop: 3 },
          ]}
          numberOfLines={3}
        >
          {sourceRef.excerpt}
        </Text>
      ) : null}
    </View>
  );
}

export function QuizQuestionCard({
  question: q,
  index,
  sessionId,
  transport = defaultTransport,
}: {
  question: QuizQuestion;
  index: number;
  sessionId?: string | undefined;
  transport?: QuizCardTransport | undefined;
}) {
  const { theme } = useTheme();
  const { t } = useI18n();
  const qid = q.question_id ?? "";
  const rev = q.question_revision ?? 1;
  const hasIdentity = !!qid;
  const draftKey = hasIdentity ? `${qid}:${rev}` : "";
  const savedResult =
    q.result && (q.result.attempt_id || q.result.verdict) ? q.result : null;

  const [selected, setSelected] = useState<string | null>(
    savedResult?.student_answer ??
      (draftKey ? (quizAnswerDrafts.get(draftKey) ?? null) : null),
  );
  const [outcome, setOutcome] = useState<QuizSubmitOutcome | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [hint, setHint] = useState("");
  const [hintLoading, setHintLoading] = useState(false);
  const [revealed, setRevealed] = useState<{
    answer: string;
    explanation: string;
  } | null>(null);
  const [restoring, setRestoring] = useState(hasIdentity && !savedResult);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [canRefresh, setCanRefresh] = useState(false);
  const [expOpen, setExpOpen] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false);

  const updateDraft = useCallback(
    (value: string) => {
      setSelected(value);
      rememberQuizDraft(draftKey, value);
    },
    [draftKey],
  );

  // 恢复已提交状态（含 pending 轮询：评价落地后回填 verdict/feedback）。
  useEffect(() => {
    if (!qid) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;
    async function refresh() {
      attempts += 1;
      try {
        const { submission } = await transport.submission(qid, rev);
        if (!alive) return;
        if (submission) {
          rememberQuizDraft(draftKey, "");
          setOutcome(submission);
          setSelected(submission.student_answer);
          if (submission.revealed) setRevealed(submission.revealed);
        }
        setRestoring(false);
        if (!submission?.pending) {
          setCanRefresh(false);
          return;
        }
      } catch {
        if (!alive) return;
        setRestoring(false);
      }
      if (attempts < 20) timer = setTimeout(() => void refresh(), 2000);
      else setCanRefresh(true);
    }
    void refresh();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
    // transport 每次渲染都是新对象：恢复轮询只随题身份/显式刷新重启。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qid, rev, draftKey, refreshVersion]);

  const submitted = !!outcome || !!savedResult;
  const submittedAnswer = selected ?? savedResult?.student_answer ?? "";
  const isMC = q.type === "multiple_choice" && !!q.options;
  const options = q.options ? Object.entries(q.options) : [];
  const sourceRefs = (q.source_refs ?? []).filter((r) => r && r.file_id);
  const textbookGrounded =
    q.grounding_mode === "textbook" ||
    q.grounding_mode === "reference+textbook";
  const partialGrounded = textbookGrounded && q.grounding_tier === "partial";
  const essentialPending = q.visual_role === "essential" && !q.illustration;
  const verdict = outcome?.task_result?.verdict ?? savedResult?.verdict ?? null;

  const verdictHapticFired = useRef(false);
  useEffect(() => {
    if (!submitted || verdictHapticFired.current || !verdict) return;
    verdictHapticFired.current = true;
    void Haptics.notificationAsync(
      verdict === "correct"
        ? Haptics.NotificationFeedbackType.Success
        : Haptics.NotificationFeedbackType.Warning,
    ).catch(() => undefined);
  }, [submitted, verdict]);

  async function loadHint() {
    if (!hasIdentity || hintLoading || submitted) return;
    setHintLoading(true);
    try {
      const res = await transport.hint(qid, rev);
      setHint(
        res.status === "ok" ? res.hint : res.message || t("quiz.hint.none"),
      );
    } catch {
      setHint(t("quiz.hint.error"));
    } finally {
      setHintLoading(false);
    }
  }

  async function revealAnswer() {
    if (!hasIdentity || revealed) return;
    try {
      const res = await transport.reveal(qid, rev);
      if (res.status === "ok") {
        setRevealed({ answer: res.answer, explanation: res.explanation });
        setExpOpen(true);
      }
    } catch {
      // 揭晓失败静默：按钮可重试。
    }
  }

  async function submit() {
    if (
      !hasIdentity ||
      !selected?.trim() ||
      restoring ||
      submitting ||
      submitted ||
      essentialPending
    )
      return;
    setSubmitting(true);
    setSubmitError("");
    try {
      const res = await transport.submit({
        question_id: qid,
        question_revision: rev,
        student_answer: selected,
        ...(sessionId ? { session_id: sessionId } : {}),
      });
      rememberQuizDraft(draftKey, "");
      setOutcome(res);
      if (res.task_result?.verdict)
        setRevealed({ answer: q.answer, explanation: q.explanation });
      setRefreshVersion((v) => v + 1);
    } catch (error) {
      setSubmitError(
        error instanceof Error ? error.message : t("quiz.grade.error"),
      );
      setRefreshVersion((v) => v + 1);
    } finally {
      setSubmitting(false);
    }
  }

  const answerText = revealed?.answer ?? q.answer;
  const evaluationPending =
    submitted &&
    (outcome?.evaluation?.status ?? savedResult?.evaluation?.status) ===
      "pending";

  return (
    <View
      style={[theme.card as object, styles.card]}
      accessibilityLabel={`quiz-${index + 1}`}
    >
      {/* 题号 + 题型 + 教材依据 */}
      <View style={styles.metaRow}>
        <View
          style={[
            styles.indexBadge,
            { backgroundColor: theme.colors["accent-soft"] },
          ]}
        >
          <Text
            style={[
              styles.indexText,
              {
                color: theme.colors["accent-strong"],
                fontFamily: theme.fonts.mono,
              },
            ]}
          >
            {index + 1}
          </Text>
        </View>
        <Badge
          label={t(`quiz.type.${q.type || "multiple_choice"}`, q.type)}
          tone="outline"
        />
        {textbookGrounded ? (
          <Badge
            label={
              partialGrounded
                ? t("quiz.grounding.partial")
                : t("quiz.grounding.textbook")
            }
            tone="accent"
          />
        ) : null}
        {q.difficulty ? (
          <Text
            style={[
              theme.type.caption,
              { color: theme.colors.muted, marginLeft: "auto" },
            ]}
          >
            {t("quiz.difficulty")} {q.difficulty}
          </Text>
        ) : null}
      </View>

      <View style={{ marginTop: 8 }}>
        <RichContentRenderer text={q.stem} />
      </View>
      {/* 题图占位：M3 接入 react-native-svg 渲染；essential 无图时禁止提交 */}
      {essentialPending ? (
        <View
          style={[
            styles.illustrationPending,
            {
              borderColor: alpha(theme.colors.accent, 0.3),
              backgroundColor: alpha(theme.colors["accent-soft"], 0.3),
            },
          ]}
        >
          <ActivityIndicator size="small" color={theme.colors.accent} />
        </View>
      ) : null}

      {isMC ? (
        <View style={{ marginTop: 10, gap: 6 }}>
          {options.map(([key, val]) => {
            const isSelected = (submitted ? submittedAnswer : selected) === key;
            const isCorrect = submitted && answerText === key;
            let borderColor: string = theme.colors["border-light"];
            let backgroundColor: string = theme.colors.bg;
            let dimmed = false;
            if (!submitted && isSelected) {
              borderColor = theme.colors.accent;
              backgroundColor = alpha(theme.colors["accent-soft"], 0.5);
            } else if (submitted && isCorrect) {
              borderColor = alpha(theme.colors.success, 0.5);
              backgroundColor = alpha(theme.colors.success, 0.08);
            } else if (submitted && isSelected && !isCorrect) {
              borderColor = alpha(theme.colors.danger, 0.5);
              backgroundColor = alpha(theme.colors.danger, 0.08);
            } else if (submitted) {
              dimmed = true;
            }
            return (
              <Pressable
                key={key}
                disabled={
                  submitted ||
                  submitting ||
                  restoring ||
                  !hasIdentity ||
                  essentialPending
                }
                onPress={() => updateDraft(key)}
                accessibilityRole="button"
                accessibilityState={{
                  selected: isSelected,
                  disabled: submitted,
                }}
                style={[
                  styles.option,
                  { borderColor, backgroundColor, opacity: dimmed ? 0.55 : 1 },
                ]}
              >
                <View
                  style={[
                    styles.optionKey,
                    { borderColor: theme.colors.border },
                    !submitted && isSelected
                      ? {
                          borderColor: theme.colors.accent,
                          backgroundColor: theme.colors.accent,
                        }
                      : null,
                    submitted && isCorrect
                      ? {
                          borderColor: theme.colors.success,
                          backgroundColor: theme.colors.success,
                        }
                      : null,
                    submitted && isSelected && !isCorrect
                      ? {
                          borderColor: theme.colors.danger,
                          backgroundColor: theme.colors.danger,
                        }
                      : null,
                  ]}
                >
                  <Text
                    style={[
                      theme.type.caption,
                      { fontWeight: "600" },
                      (!submitted && isSelected) ||
                      (submitted && (isCorrect || isSelected))
                        ? { color: theme.colors.onAccent }
                        : { color: theme.colors["fg-secondary"] },
                    ]}
                  >
                    {key}
                  </Text>
                </View>
                <View style={{ flex: 1 }}>
                  <RichContentRenderer text={val} />
                </View>
              </Pressable>
            );
          })}
        </View>
      ) : submitted ? (
        <View
          style={[
            styles.submittedAnswer,
            {
              borderColor: theme.colors["border-light"],
              backgroundColor: theme.colors.bg,
            },
          ]}
        >
          <Text
            style={[
              theme.type.caption,
              { color: theme.colors.muted, marginBottom: 4 },
            ]}
          >
            {t("quiz.submitted.answer")}
          </Text>
          <RichContentRenderer text={submittedAnswer} />
        </View>
      ) : (
        <View style={{ marginTop: 10 }}>
          <TextArea
            value={selected ?? ""}
            onChangeText={updateDraft}
            editable={
              !submitting && !restoring && hasIdentity && !essentialPending
            }
            placeholder={
              hasIdentity ? t("quiz.answer.placeholder") : t("quiz.legacy.note")
            }
          />
        </View>
      )}

      {/* 操作行：提交一次 + 提示 + 揭晓（均服务端记录） */}
      {hasIdentity && !submitted ? (
        <View style={styles.actionRow}>
          <Pressable
            onPress={() => void submit()}
            disabled={
              !selected?.trim() || submitting || restoring || essentialPending
            }
            accessibilityRole="button"
            accessibilityLabel={t("quiz.submit")}
            style={({ pressed }) => [
              styles.primaryAction,
              {
                opacity:
                  !selected?.trim() || submitting || essentialPending
                    ? 0.4
                    : pressed
                      ? 0.7
                      : 1,
              },
            ]}
          >
            {submitting ? (
              <ActivityIndicator size={12} color={theme.colors.accent} />
            ) : (
              <Send size={13} color={theme.colors.accent} />
            )}
            <Text
              style={[
                theme.type.label,
                { color: theme.colors.accent, marginLeft: 6 },
              ]}
            >
              {submitting ? t("quiz.grading") : t("quiz.submit")}
            </Text>
          </Pressable>
          <Pressable
            onPress={() => void loadHint()}
            disabled={hintLoading || !!hint}
            accessibilityRole="button"
            accessibilityLabel={t("quiz.hint")}
            style={({ pressed }) => [
              styles.subAction,
              { opacity: hint ? 0.5 : pressed ? 0.7 : 1 },
            ]}
          >
            {hintLoading ? (
              <ActivityIndicator size={11} color={theme.colors.muted} />
            ) : (
              <Lightbulb size={12} color={theme.colors.muted} />
            )}
            <Text
              style={[
                theme.type.caption,
                { color: theme.colors.muted, marginLeft: 4 },
              ]}
            >
              {t("quiz.hint")}
            </Text>
          </Pressable>
          <Pressable
            onPress={() => void revealAnswer()}
            accessibilityRole="button"
            accessibilityLabel={t("quiz.reveal")}
            style={({ pressed }) => [
              styles.subAction,
              { opacity: pressed ? 0.7 : 1 },
            ]}
          >
            <Eye size={12} color={theme.colors.muted} />
            <Text
              style={[
                theme.type.caption,
                { color: theme.colors.muted, marginLeft: 4 },
              ]}
            >
              {t("quiz.reveal")}
            </Text>
          </Pressable>
        </View>
      ) : null}
      {submitError ? (
        <Text
          style={[
            theme.type.caption,
            { color: theme.colors.danger, marginTop: 6 },
          ]}
        >
          {submitError}
        </Text>
      ) : null}
      {!hasIdentity ? (
        <Text
          style={[
            theme.type.caption,
            { color: alpha(theme.colors.muted, 0.7), marginTop: 6 },
          ]}
        >
          {t("quiz.legacy.note")}
        </Text>
      ) : null}

      {/* 关键步骤提示（服务端量规派生；不含答案） */}
      {hint && !submitted ? (
        <View
          style={[
            styles.hintBox,
            {
              borderColor: alpha(theme.colors.warning, 0.3),
              backgroundColor: alpha(theme.colors.warning, 0.05),
            },
          ]}
        >
          <Text
            style={[
              theme.type.caption,
              { color: theme.colors["fg-secondary"], lineHeight: 19 },
            ]}
          >
            {hint}
          </Text>
        </View>
      ) : null}

      {/* 两层反馈：本题结果 + 学习反馈 */}
      {submitted && (outcome || savedResult) ? (
        <View style={{ marginTop: 10 }}>
          <View style={styles.feedbackRow}>
            <Badge
              label={
                verdict === "correct"
                  ? t("quiz.verdict.correct")
                  : verdict === "partial"
                    ? t("quiz.verdict.partial")
                    : t("quiz.verdict.wrong")
              }
              tone={
                verdict === "correct"
                  ? "success"
                  : verdict === "partial"
                    ? "warning"
                    : "danger"
              }
              dot
            />
            {evaluationPending ? (
              <Text
                style={[
                  theme.type.caption,
                  { color: theme.colors.muted, marginLeft: 8 },
                ]}
              >
                {t("quiz.evaluation.pending")}
              </Text>
            ) : null}
          </View>
          {outcome?.feedback ? (
            <Text
              style={[
                theme.type.caption,
                {
                  color: theme.colors["fg-secondary"],
                  marginTop: 6,
                  lineHeight: 19,
                },
              ]}
            >
              {outcome.feedback}
            </Text>
          ) : null}
          {canRefresh ? (
            <Pressable
              onPress={() => {
                setCanRefresh(false);
                setRefreshVersion((v) => v + 1);
              }}
              accessibilityRole="button"
              style={{
                marginTop: 6,
                alignSelf: "flex-start",
                minHeight: 32,
                justifyContent: "center",
              }}
            >
              <Text
                style={[theme.type.caption, { color: theme.colors.accent }]}
              >
                {t("quiz.refresh.feedback")}
              </Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}

      {/* 解析折叠（提交/揭晓后可见） */}
      {(submitted || revealed) && !!(revealed?.explanation || q.explanation) ? (
        <View
          style={[
            styles.explanation,
            { borderTopColor: theme.colors["border-light"] },
          ]}
        >
          <Pressable
            onPress={() => setExpOpen((v) => !v)}
            accessibilityRole="button"
            accessibilityState={{ expanded: expOpen }}
            style={styles.expHeader}
          >
            <Lightbulb size={12} color={theme.colors.warning} />
            <Text
              style={[
                theme.type.caption,
                {
                  color: theme.colors.muted,
                  marginLeft: 4,
                  flex: 1,
                  fontWeight: "500",
                },
              ]}
            >
              {t("quiz.explanation")}
            </Text>
            <ChevronDown
              size={12}
              color={theme.colors.muted}
              style={{ transform: [{ rotate: expOpen ? "0deg" : "-90deg" }] }}
            />
          </Pressable>
          {expOpen ? (
            <View style={{ marginTop: 6 }}>
              <RichContentRenderer
                text={revealed?.explanation || q.explanation}
              />
              {sourceRefs.length > 0 ? (
                <View style={{ marginTop: 8, gap: 6 }}>
                  {(sourcesOpen ? sourceRefs : sourceRefs.slice(0, 2)).map(
                    (ref, i) => (
                      <SourceRefRow
                        key={`${ref.chunk_id ?? i}`}
                        sourceRef={ref}
                      />
                    ),
                  )}
                  {sourceRefs.length > 2 ? (
                    <Pressable
                      onPress={() => setSourcesOpen((v) => !v)}
                      accessibilityRole="button"
                      style={[styles.subAction, { alignSelf: "flex-start" }]}
                    >
                      <ChevronDown
                        size={11}
                        color={theme.colors.muted}
                        style={{
                          transform: [
                            { rotate: sourcesOpen ? "0deg" : "-90deg" },
                          ],
                        }}
                      />
                      <Text
                        style={[
                          theme.type.caption,
                          { color: theme.colors.muted, marginLeft: 4 },
                        ]}
                      >
                        {sourcesOpen
                          ? t("quiz.grounding.collapse")
                          : t("quiz.grounding.more").replace(
                              "%n",
                              String(sourceRefs.length - 2),
                            )}
                      </Text>
                    </Pressable>
                  ) : null}
                </View>
              ) : null}
            </View>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { padding: 12 },
  metaRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  indexBadge: {
    width: 20,
    height: 20,
    borderRadius: 5,
    alignItems: "center",
    justifyContent: "center",
  },
  indexText: { fontSize: 11, fontWeight: "600" },
  illustrationPending: {
    marginTop: 10,
    height: 72,
    borderWidth: 1,
    borderStyle: "dashed",
    borderRadius: 8,
    alignItems: "center",
    justifyContent: "center",
  },
  option: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    minHeight: 44,
  },
  optionKey: {
    width: 20,
    height: 20,
    borderRadius: 10,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  submittedAnswer: {
    marginTop: 10,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  actionRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 16,
    marginTop: 10,
  },
  primaryAction: { flexDirection: "row", alignItems: "center", minHeight: 36 },
  subAction: { flexDirection: "row", alignItems: "center", minHeight: 36 },
  hintBox: {
    marginTop: 8,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  feedbackRow: { flexDirection: "row", alignItems: "center" },
  explanation: { marginTop: 10, borderTopWidth: 1, paddingTop: 8 },
  expHeader: { flexDirection: "row", alignItems: "center", minHeight: 28 },
  sourceRef: {
    borderWidth: 1,
    borderRadius: 6,
    paddingHorizontal: 10,
    paddingVertical: 6,
  },
  sourceRefHead: { flexDirection: "row", alignItems: "center" },
});
