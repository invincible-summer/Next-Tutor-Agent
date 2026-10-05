import React, { useEffect, useState } from "react";
import { ScrollView, View } from "react-native";
import { ApiError } from "@next-tutor/api-client";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import {
  useAction,
  useServerQuery,
  record,
  rows,
  str,
} from "@/lib/server-state";
import { useWorkspace } from "@/providers/WorkspaceProvider";
import {
  Button,
  Card,
  Field,
  TextArea,
  ListRow,
  SegmentedControl,
  Sheet,
  RichContentRenderer,
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
import { AdaptivePane } from "@/shell/adaptive/AdaptivePane";
import { useAdaptive } from "@/shell/adaptive/window-class";
export function InsightsScreen() {
  const c = useCopy();
  const scope = useWorkspace();
  const action = useAction();
  const adaptive = useAdaptive();
  const [tab, setTab] = useState("concepts");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [observeJob, setObserveJob] = useState(true);
  const [reviewConflict, setReviewConflict] = useState(false);
  useEffect(() => {
    setSelected(null);
    setReason("");
    setJobId(null);
    setPage(1);
  }, [scope.id]);
  useEffect(() => {
    setObserveJob(true);
  }, [jobId]);
  const summary = useServerQuery(
    ["evaluation", scope.id],
    (s) => apiClient().evaluation.workspace(scope.id!, s),
    { enabled: !!scope.id, poll: 10000 },
  );
  const concepts = useServerQuery(
    ["evaluation-concepts", scope.id, page],
    (s) =>
      apiClient().evaluation.concepts(
        scope.id!,
        { offset: (page - 1) * 12, limit: 12 },
        s,
      ),
    { enabled: !!scope.id },
  );
  const evidence = useServerQuery(
    ["evidence", scope.id, page],
    (s) =>
      apiClient().evaluation.evidence(
        scope.id!,
        { offset: (page - 1) * 12, limit: 12 },
        s,
      ),
    { enabled: !!scope.id },
  );
  const detail = useServerQuery(
    ["evidence-detail", selected],
    (s) => apiClient().evaluation.evidenceDetail(selected!, s),
    { enabled: !!selected },
  );
  const job = useServerQuery(
    ["evaluation-job", jobId],
    (s) => apiClient().evaluation.job(jobId!, s),
    { enabled: !!jobId, poll: observeJob ? 3000 : 0 },
  );
  useEffect(() => {
    if (
      job.data &&
      [
        "succeeded",
        "completed",
        "committed",
        "failed",
        "cancelled",
        "unavailable",
      ].includes(job.data.state)
    ) {
      setObserveJob(false);
      void summary.refetch();
      void evidence.refetch();
      if (selected) void detail.refetch();
    }
  }, [job.data?.state, job.data?.job_id]);
  const label = (state: string | null) =>
    ({
      not_observed: c("尚未观察", "Not yet observed"),
      emerging: c("正在形成理解", "Understanding is emerging"),
      supported_in_scope: c("在当前范围内有支持", "Supported in this scope"),
      fragile: c("仍需进一步验证", "Needs further validation"),
      conflicting: c("证据有分歧", "Conflicting evidence"),
    })[state ?? "not_observed"] ?? c("等待整理", "Pending");
  const details = (
    <Body>
      <QueryState query={detail}>
        <Section title={c("这条学习证据", "This learning evidence")}>
          <Hint>{detail.data?.observed_at}</Hint>
          <RichContentRenderer text={detail.data?.canonical_text ?? ""} />
          <Hint>{str(record(detail.data?.interpretation).statement)}</Hint>
          {detail.data?.revealed ? (
            <Card style={{ gap: 8 }}>
              <Label>{c("作答后解析", "Post-answer explanation")}</Label>
              <RichContentRenderer text={detail.data.revealed.explanation} />
            </Card>
          ) : null}
        </Section>
        <Field
          label={c("请求重新评估", "Request a review")}
          hint={c(
            "说明哪里不准确。新的评估由服务端完成。",
            "Explain what is inaccurate. Your server performs the review.",
          )}
        >
          <TextArea
            value={reason}
            onChangeText={setReason}
            accessibilityLabel={c("复核原因", "Review reason")}
          />
        </Field>
        {reviewConflict ? (
          <Hint>
            {c(
              "这条来源已更新，服务端版本已重新读取。你的原因仍保留，请核对后再次提交。",
              "The source changed and its current version was reloaded. Your reason is preserved; review it before submitting again.",
            )}
          </Hint>
        ) : null}
        <Button
          title={c("提交复核", "Submit review")}
          disabled={!reason.trim() || !detail.data?.interpretation_id}
          loading={action.pending}
          onPress={() =>
            void action.run(
              async () => {
                try {
                  return await apiClient().evaluation.createReview(selected!, {
                    reason: reason.trim(),
                    interpretation_id: detail.data!.interpretation_id!,
                    expected_revision: detail.data!.source_revision ?? 0,
                  });
                } catch (error) {
                  if (error instanceof ApiError && error.status === 409) {
                    setReviewConflict(true);
                    await detail.refetch();
                  }
                  throw error;
                }
              },
              (r) => {
                setJobId(r.job_id);
                setReason("");
                setReviewConflict(false);
              },
            )
          }
        />
        {job.data ? (
          <Hint>
            {c("复核状态", "Review status")}: {job.data.state}
          </Hint>
        ) : null}
        {job.data?.state === "failed" && job.data.retryable ? (
          <Button
            title={c("重试复核", "Retry review")}
            onPress={() =>
              void action.run(
                () => apiClient().evaluation.retryJob(job.data!.job_id),
                (r) => setJobId(r.job_id),
              )
            }
          />
        ) : null}
      </QueryState>
    </Body>
  );
  return (
    <FeatureShell title={c("学习洞察", "Learning insights")} scroll={false}>
      <AdaptivePane
        inspector={
          selected ? (
            <ScrollView keyboardShouldPersistTaps="handled">
              {details}
            </ScrollView>
          ) : undefined
        }
        inspectorWidth={340}
      >
        <ScrollView
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={{ flexGrow: 1 }}
        >
          <Body>
            {!scope.id ? (
              <Card style={{ gap: 12, padding: 24 }}>
                <Label>
                  {c("从一个工作区开始观察", "Choose a workspace to explore")}
                </Label>
                <Hint>
                  {c(
                    "结论与证据属于具体资料范围，选择后查看对应学习记录。",
                    "Observations and evidence belong to a specific material scope.",
                  )}
                </Hint>
                <Button
                  title={c("选择工作区", "Choose workspace")}
                  onPress={scope.open}
                />
              </Card>
            ) : (
              <>
                <QueryState query={summary}>
                  <Card style={{ gap: 12, padding: 24 }}>
                    <Label>
                      {c(
                        "理解，从证据中浮现",
                        "Understanding, through evidence",
                      )}
                    </Label>
                    <RichContentRenderer
                      text={
                        summary.data?.synthesis?.statement ||
                        c(
                          "还没有可展示的综合观察。完成学习后可以重新整理。",
                          "No synthesis is available yet. Return after a learning activity.",
                        )
                      }
                    />
                    {summary.data?.synthesis?.limits?.map((t) => (
                      <Hint key={t}>{t}</Hint>
                    ))}
                    <Button
                      title={c("整理有效证据", "Synthesize evidence")}
                      variant="outline"
                      loading={action.pending}
                      onPress={() =>
                        void action.run(
                          () =>
                            apiClient().evaluation.requestSynthesis(
                              scope.id!,
                              summary.data?.scope_revision,
                            ),
                          (r) => setJobId(r.job_id),
                        )
                      }
                    />
                  </Card>
                </QueryState>
                <SegmentedControl
                  items={[
                    { key: "concepts", label: c("知识点", "Concepts") },
                    { key: "evidence", label: c("证据时间线", "Evidence") },
                  ]}
                  active={tab}
                  onChange={(v) => {
                    setTab(v);
                    setPage(1);
                  }}
                />
                {tab === "concepts" ? (
                  <QueryState
                    query={concepts}
                    empty={!concepts.data?.items.length}
                  >
                    {concepts.data?.items.map((item) => (
                      <Card
                        key={
                          item.concept_ref.key ?? item.concept_ref.concept_id
                        }
                        style={{ gap: 10 }}
                      >
                        <Label>
                          {item.concept_ref.display_name ||
                            item.concept_ref.concept_id}
                        </Label>
                        <Hint>{label(item.state)}</Hint>
                        {item.statement ? (
                          <RichContentRenderer text={item.statement} />
                        ) : null}
                        {item.next_probe?.instruction ? (
                          <Hint>
                            {c("建议下一步：", "Next step: ")}
                            {item.next_probe.instruction}
                          </Hint>
                        ) : null}
                        {(Array.isArray(item.limits) ? item.limits : []).map(
                          (t: unknown) => (
                            <Hint key={String(t)}>{String(t)}</Hint>
                          ),
                        )}
                      </Card>
                    ))}
                    <Pager
                      page={page}
                      total={concepts.data?.total ?? 0}
                      pageSize={12}
                      onChange={setPage}
                    />
                  </QueryState>
                ) : (
                  <QueryState
                    query={evidence}
                    empty={!evidence.data?.items.length}
                  >
                    {evidence.data?.items.map((item) => (
                      <Card key={item.source_id} style={{ padding: 8 }}>
                        <ListRow
                          title={
                            item.summary || c("学习活动", "Learning activity")
                          }
                          subtitle={item.observed_at}
                          badge={{
                            label:
                              item.availability === "available"
                                ? c("可查看", "Available")
                                : c("来源已变化", "Source changed"),
                          }}
                          onPress={() => {
                            setSelected(item.source_id);
                            setReason("");
                            setReviewConflict(false);
                          }}
                        />
                      </Card>
                    ))}
                    <Pager
                      page={page}
                      total={evidence.data?.total ?? 0}
                      pageSize={12}
                      onChange={setPage}
                    />
                  </QueryState>
                )}
              </>
            )}
          </Body>
        </ScrollView>
      </AdaptivePane>
      <Sheet
        open={!!selected && adaptive.maxPanes === 1}
        onClose={() => setSelected(null)}
        label={c("学习证据", "Learning evidence")}
      >
        <SheetHeader
          title={c("学习证据", "Learning evidence")}
          onClose={() => setSelected(null)}
        />
        <SheetBody>{details}</SheetBody>
      </Sheet>
    </FeatureShell>
  );
}
