import React, { useEffect, useRef, useState } from "react";
import { ScrollView, View } from "react-native";
import type {
  QuestionPublic,
  CatReport,
  TaskResult,
} from "@next-tutor/api-client";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { useServerQuery, useAction, str, record } from "@/lib/server-state";
import { confirm } from "@/lib/feedback";
import { useWorkspace } from "@/providers/WorkspaceProvider";
import { useUiPrefs } from "@/stores/ui";
import {
  Button,
  Card,
  Chip,
  Field,
  TextArea,
  TextField,
  SegmentedControl,
  Sheet,
  ListRow,
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
import { FeatureShell, CapabilityGate } from "@/ui/FeatureShell";
import { EditSheet } from "@/ui/EditSheet";
import { AdaptivePane } from "@/shell/adaptive/AdaptivePane";
import { PublicQuestion, Feedback } from "./PublicQuestion";
import { useAdaptive } from "@/shell/adaptive/window-class";
export function AssessmentScreen() {
  const c = useCopy();
  const scope = useWorkspace();
  const action = useAction();
  const adaptive = useAdaptive();
  const grade = useUiPrefs((s) => s.grade);
  const [mode, setMode] = useState("v1");
  const modeChosen = useRef(false);
  const [purpose, setPurpose] = useState("adaptive");
  const [request, setRequest] = useState("auto");
  const [prompt, setPrompt] = useState("");
  const [count, setCount] = useState("6");
  const [id, setId] = useState("");
  const [question, setQuestion] = useState<QuestionPublic | null>(null);
  const [report, setReport] = useState<CatReport | null>(null);
  const [result, setResult] = useState<TaskResult | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [stopped, setStopped] = useState(false);
  const [setup, setSetup] = useState(false);
  const [records, setRecords] = useState(false);
  const [keys, setKeys] = useState<string[]>([]);
  const profile = useServerQuery(["profile"], (s) =>
    apiClient().profile.get(s),
  );
  useEffect(() => {
    if (!id && !modeChosen.current)
      setMode(profile.data?.profile.prefs?.quiz_illustration_mode ?? "v1");
  }, [profile.data, id]);
  const active = useServerQuery(["assessment", scope.id], (s) =>
    apiClient().assessment.active(scope.id ?? "", s),
  );
  const concepts = useServerQuery(
    ["assessment-concepts", scope.id],
    (s) => apiClient().evaluation.concepts(scope.id!, { limit: 100 }, s),
    { enabled: !!scope.id },
  );
  const history = useServerQuery(
    ["assessment-records", scope.id],
    () =>
      apiClient().assessment.records({
        limit: 50,
        ...(scope.id ? { workspaceId: scope.id } : {}),
      }),
    { enabled: records },
  );
  useEffect(() => {
    setId("");
    setQuestion(null);
    setSubmitted(false);
    setStopped(false);
    setReport(null);
    setResult(null);
    setKeys([]);
  }, [scope.id]);
  useEffect(() => {
    if (active.data?.session_status === "active" && !id) {
      setId(str(active.data.assessment_id));
      const q = record(active.data.question);
      if (typeof q.question_id === "string")
        setQuestion(q as unknown as QuestionPublic);
      else {
        setSubmitted(true);
        if (active.data.summary) setReport(active.data.summary as CatReport);
      }
      setMode(str(active.data.illustration_mode, "v1"));
    }
  }, [active.data, id]);
  const liveReport = useServerQuery(
    ["assessment-report", id],
    (s) => apiClient().assessment.report(id, s),
    {
      enabled: !!id && (submitted || stopped),
      poll: (submitted || stopped) && (report?.pending ?? 1) > 0 ? 4000 : 0,
    },
  );
  useEffect(() => {
    if (!liveReport.data) return;
    setReport(liveReport.data.summary);
    const item = liveReport.data.summary.items.find(
      (entry) => entry.question_id === question?.question_id,
    );
    if (item?.task_result) setResult(item.task_result);
  }, [liveReport.data, question?.question_id]);
  const start = () =>
    void action.run(
      () =>
        apiClient().assessment.start({
          concept_keys: keys,
          workspace_id: scope.id ?? "",
          illustration_mode: mode as "v1" | "v2" | "v3",
          illustration_request: request as "auto" | "none" | "required",
          goal: { purpose: purpose as "adaptive" | "diagnose" | "practice" },
          generation_hint: prompt,
          grade,
          count: Math.max(1, Math.min(20, Number(count) || 6)),
          evaluation_mode: scope.id ? "closed_loop" : "temporary",
        }),
      (r) => {
        setId(r.assessment_id);
        setQuestion(r.question);
        setSetup(false);
        setSubmitted(false);
        setStopped(false);
        setReport(null);
        setResult(null);
      },
    );
  const next = () =>
    void action.run(
      () => apiClient().assessment.next(id),
      (r) => {
        setQuestion(r.question);
        setReport(r.summary ?? null);
        setSubmitted(false);
        setResult(null);
        if (!r.question) setStopped(true);
      },
    );
  const inspector = (
    <Body>
      <Section title={c("测评上下文", "Assessment context")}>
        <Hint>{scope.name}</Hint>
        <Label>{mode.toUpperCase()}</Label>
        {question?.source_badge ? <Hint>{question.source_badge}</Hint> : null}
        {question?.concept_refs?.length ? (
          <Section title={c("相关知识", "Related concepts")}>
            {question.concept_refs.map((concept, i) => (
              <Hint key={concept.key ?? `${concept.concept_id}:${i}`}>
                {concept.display_name || concept.concept_id}
              </Hint>
            ))}
          </Section>
        ) : null}
        <Hint>
          {c(
            "答案只在服务端允许后展示。反馈仅反映当前问题。",
            "Answers are shown only when allowed by your server. Feedback applies to the current question.",
          )}
        </Hint>
      </Section>
      {submitted ? <Feedback result={result} /> : null}
    </Body>
  );
  return (
    <FeatureShell
      title={c("测评练习", "Assessment")}
      scroll={false}
      right={
        <Button
          title={c("记录", "History")}
          variant="ghost"
          onPress={() => setRecords(true)}
        />
      }
    >
      <AdaptivePane
        inspector={
          id ? (
            <ScrollView keyboardShouldPersistTaps="handled">
              {inspector}
            </ScrollView>
          ) : undefined
        }
      >
        <ScrollView
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={{ flexGrow: 1 }}
        >
          <Body>
            {!id ? (
              <>
                <Card style={{ gap: 16, padding: 24 }}>
                  <Label>
                    {c("看看你已经理解了什么", "Explore what you understand")}
                  </Label>
                  <Hint>
                    {c(
                      "选择知识点，或用文字描述练习方向。一次一个问题，按你的回答继续。",
                      "Choose concepts or describe your practice. One question at a time, adapting to your answers.",
                    )}
                  </Hint>
                  <Button
                    title={c("开始测评", "Start assessment")}
                    onPress={() => setSetup(true)}
                  />
                </Card>
                <QueryState query={active}>
                  <Hint>
                    {c(
                      "未完成的测评会从上次的问题继续。",
                      "An unfinished assessment resumes from your last question.",
                    )}
                  </Hint>
                </QueryState>
              </>
            ) : null}
            {question ? (
              <PublicQuestion
                question={question}
                allowEnrichment={
                  request !== "none" &&
                  profile.data?.quiz_svg_available !== false
                }
                active={!stopped}
                submitted={submitted}
                pending={action.pending}
                onSubmit={(answer) =>
                  action
                    .run(
                      () =>
                        apiClient().assessment.answer({
                          assessment_id: id,
                          question_id: question.question_id,
                          question_revision: question.question_revision,
                          student_answer: answer,
                        }),
                      (r) => {
                        setSubmitted(true);
                        setResult(r.task_result);
                        if (r.summary) {
                          setReport(r.summary);
                          if (r.summary.status && r.summary.status !== "active")
                            setStopped(true);
                        }
                      },
                    )
                    .then(() => {})
                }
                onHint={() =>
                  apiClient()
                    .assessment.hint(
                      question.question_id,
                      question.question_revision,
                    )
                    .then((r) => r.hint)
                }
              />
            ) : null}
            {submitted && !stopped ? (
              <>
                {adaptive.maxPanes === 1 ? <Feedback result={result} /> : null}
                <Hint>
                  {c(
                    "服务端正在整理本次回答的学习证据。可以继续等待或进入下一题。",
                    "Your server is processing evidence from this answer. Continue when the next question is ready.",
                  )}
                </Hint>
                <Button
                  title={c("下一题 / 查看报告", "Next question / Report")}
                  loading={action.pending}
                  onPress={next}
                />
              </>
            ) : null}
            {report ? (
              <Section title={c("本次测评", "Assessment report")}>
                <Card style={{ gap: 12 }}>
                  {report.stop_code ? (
                    <Label>
                      {(
                        {
                          sufficient_for_current_claim: c(
                            "本轮已有足够观察",
                            "Enough observations for this round",
                          ),
                          needs_clarification: c(
                            "还需要进一步确认",
                            "Further clarification is needed",
                          ),
                          max_questions: c(
                            "本轮题目已完成",
                            "This round is complete",
                          ),
                          max_time: c("本轮时间已结束", "This round has ended"),
                          user_stopped: c("本轮已结束", "You ended this round"),
                          generation_failed: c(
                            "题目生成暂未完成",
                            "Question generation did not complete",
                          ),
                        } as Record<string, string>
                      )[report.stop_code] ??
                        c("本轮已结束", "This round has ended")}
                    </Label>
                  ) : null}
                  <Hint>
                    {c(
                      `已作答 ${report.asked ?? report.items.length} 题 · 等待评估 ${report.pending ?? 0} 题`,
                      `${report.asked ?? report.items.length} questions · ${report.pending ?? 0} pending`,
                    )}
                  </Hint>
                  {report.items.map((item) => (
                    <View key={item.question_id} style={{ gap: 8 }}>
                      <Label>{item.question?.stem || item.question_id}</Label>
                      <Feedback
                        result={item.task_result}
                        feedback={item.feedback}
                      />
                    </View>
                  ))}
                </Card>
              </Section>
            ) : null}
            {id && !stopped ? (
              <Button
                title={c("结束本次测评", "End assessment")}
                variant="ghost"
                onPress={() =>
                  void confirm(
                    c("结束本次测评？", "End this assessment?"),
                    c(
                      "已提交的回答仍会保留。",
                      "Submitted answers will be kept.",
                    ),
                    c("结束", "End"),
                  ).then((ok) => {
                    if (ok)
                      void action.run(
                        async () => {
                          await apiClient().assessment.abandon(id);
                          return apiClient().assessment.report(id);
                        },
                        (r) => {
                          setStopped(true);
                          setReport(r.summary);
                        },
                      );
                  })
                }
              />
            ) : null}
            {stopped ? (
              <Button
                title={c("新的练习", "New practice")}
                onPress={() => {
                  setId("");
                  setQuestion(null);
                  setReport(null);
                  setSetup(true);
                }}
              />
            ) : null}
          </Body>
        </ScrollView>
      </AdaptivePane>
      <EditSheet
        open={setup}
        title={c("设计这次练习", "Set up your practice")}
        onClose={() => setSetup(false)}
        onSave={start}
        pending={action.pending}
        saveLabel={c("开始", "Begin")}
        disabled={!!scope.id && !keys.length && !prompt.trim()}
      >
        <Field label={c("配图方式", "Illustration mode")}>
          <SegmentedControl
            items={["v1", "v2", "v3"].map((key) => ({
              key,
              label: key.toUpperCase(),
            }))}
            active={mode}
            onChange={(value) => {
              modeChosen.current = true;
              setMode(value);
            }}
          />
        </Field>
        <Hint>
          {c(
            mode === "v1"
              ? "V1 使用组件链配图。"
              : "V2 / V3 的题图与题面须经服务端合并审核后发布。",
            mode === "v1"
              ? "V1 uses component-based illustrations."
              : "V2 / V3 are published after combined image and question review.",
          )}
        </Hint>
        <Field label={c("练习目的", "Purpose")}>
          <SegmentedControl
            items={[
              { key: "adaptive", label: c("自适应", "Adaptive") },
              { key: "diagnose", label: c("诊断", "Diagnose") },
              { key: "practice", label: c("练习", "Practice") },
            ]}
            active={purpose}
            onChange={setPurpose}
          />
        </Field>
        <Field label={c("题图需求", "Image preference")}>
          <SegmentedControl
            items={[
              { key: "auto", label: c("自动", "Auto") },
              { key: "none", label: c("纯文字", "Text") },
              { key: "required", label: c("需要图示", "Illustrated") },
            ]}
            active={request}
            onChange={setRequest}
          />
        </Field>
        <Field label={c("知识点", "Concepts")}>
          {scope.id ? (
            <QueryState query={concepts} empty={!concepts.data?.items.length}>
              <Hint>
                {c(
                  "选择要练习的知识点，也可以只填写下面的练习方向。",
                  "Choose concepts, or describe a practice direction below.",
                )}
              </Hint>
            </QueryState>
          ) : (
            <Hint>
              {c(
                "临时练习可以直接描述练习方向。",
                "Describe your practice direction for a temporary assessment.",
              )}
            </Hint>
          )}
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
            {concepts.data?.items.map((item) => {
              const key = item.concept_ref.key || item.concept_ref.concept_id;
              return (
                <Chip
                  key={key}
                  label={
                    item.concept_ref.display_name || item.concept_ref.concept_id
                  }
                  active={keys.includes(key)}
                  onPress={() =>
                    setKeys((old) =>
                      old.includes(key)
                        ? old.filter((k) => k !== key)
                        : [...old, key],
                    )
                  }
                />
              );
            })}
          </View>
        </Field>
        <Field label={c("练习方向（可选）", "Practice direction (optional)")}>
          <TextArea
            value={prompt}
            onChangeText={setPrompt}
            accessibilityLabel={c("练习方向", "Practice direction")}
          />
        </Field>
        <Field label={c("题目数量", "Question count")}>
          <TextField
            value={count}
            onChangeText={setCount}
            keyboardType="number-pad"
            accessibilityLabel={c("题目数量", "Question count")}
          />
        </Field>
        <Hint>
          {scope.id
            ? c(
                "本次练习写入此工作区的学习记录。",
                "This practice contributes to this workspace's learning record.",
              )
            : c(
                "未选择工作区：本次为临时练习。",
                "No workspace selected: this is temporary practice.",
              )}
        </Hint>
      </EditSheet>
      <Sheet
        open={records}
        onClose={() => setRecords(false)}
        label={c("作答记录", "Answer history")}
      >
        <SheetHeader
          title={c("作答记录", "Answer history")}
          onClose={() => setRecords(false)}
        />
        <SheetBody>
          <QueryState query={history} empty={!history.data?.items.length}>
            {history.data?.items.map((r, i) => (
              <Card key={str(r.question_id, String(i))} style={{ gap: 12 }}>
                <Label>
                  {str(
                    r.stem,
                    str(
                      record(r.question).stem,
                      c("练习记录", "Practice record"),
                    ),
                  )}
                </Label>
                <Hint>
                  {str(record(r.task_result).verdict) || str(r.verdict)}
                </Hint>
              </Card>
            ))}
          </QueryState>
        </SheetBody>
      </Sheet>
    </FeatureShell>
  );
}
