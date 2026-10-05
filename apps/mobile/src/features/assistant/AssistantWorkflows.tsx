import React, { useRef, useState } from "react";
import { randomUUID } from "expo-crypto";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { confirm } from "@/lib/feedback";
import { useAction, useServerQuery } from "@/lib/server-state";
import { Button, Card, Chip, ListRow } from "@/ui";
import { Hint, Label, Pager, QueryState, Section } from "@/ui/Elements";
export function AssistantWorkflows({
  navigate,
}: {
  navigate: (target: unknown) => boolean;
}) {
  const c = useCopy();
  const action = useAction();
  const [page, setPage] = useState(1);
  const [id, setId] = useState<string | null>(null);
  const request = useRef({ id: "", key: randomUUID() });
  const list = useServerQuery(
    ["assistant-workflows", page],
    (signal) =>
      apiClient().assistant.listWorkflows(
        (page - 1) * 10,
        10,
        undefined,
        signal,
      ),
    { poll: 6000 },
  );
  const detail = useServerQuery(
    ["assistant-workflow", id],
    (signal) => apiClient().assistant.getWorkflow(id!, signal),
    { enabled: !!id, poll: 3000 },
  );
  const workflow = detail.data?.workflow,
    preview = detail.data?.preview;
  return (
    <>
      <QueryState query={list} empty={!list.data?.items.length}>
        {list.data?.items.map((item) => (
          <Card key={item.workflow_id}>
            <ListRow
              title={item.objective}
              subtitle={item.state}
              onPress={() => setId(item.workflow_id)}
            />
          </Card>
        ))}
        <Pager
          page={page}
          total={list.data?.total ?? 0}
          pageSize={10}
          onChange={setPage}
        />
      </QueryState>
      {id ? (
        <QueryState query={detail}>
          {workflow && preview ? (
            <Card style={{ gap: 12 }}>
              <Label>{workflow.objective}</Label>
              <Hint>{workflow.state}</Hint>
              {preview.plan.will_create_or_modify.map((step) => (
                <Hint key={step.step_id}>{step.operation}</Hint>
              ))}
              {workflow.steps.map((step) => (
                <Section key={step.step_id} title={step.title}>
                  <Hint>{step.state}</Hint>
                  {step.error ? <Hint>{step.error.message}</Hint> : null}
                  {step.state === "failed" ? (
                    <Button
                      title={c("重试这一步", "Retry this step")}
                      variant="outline"
                      loading={action.pending}
                      onPress={() =>
                        void action.run(() =>
                          apiClient().assistant.retryWorkflowStep(
                            id,
                            step.step_id,
                            {
                              expected_revision: workflow.revision,
                              client_request_id: randomUUID(),
                            },
                          ),
                        )
                      }
                    />
                  ) : null}
                </Section>
              ))}
              {workflow.state === "proposed" ||
              workflow.state === "awaiting_approval" ? (
                <Button
                  title={c("确认步骤并开始", "Approve steps and start")}
                  loading={action.pending}
                  onPress={() =>
                    void confirm(
                      c("开始执行这些步骤？", "Start these steps?"),
                      preview.plan.will_create_or_modify
                        .map((step) => step.operation)
                        .join("\n"),
                      c("确认", "Approve"),
                    ).then((ok) => {
                      if (ok)
                        void action.run(async () => {
                          const approved =
                            await apiClient().assistant.approveWorkflow(id, {
                              expected_revision: workflow.revision,
                              plan_hash: preview.plan_hash,
                              approved_step_ids: preview.steps.map(
                                (step) => step.step_id,
                              ),
                            });
                          if (request.current.id !== id)
                            request.current = { id, key: randomUUID() };
                          return apiClient().assistant.startWorkflow(id, {
                            expected_revision: approved.revision,
                            client_request_id: request.current.key,
                          });
                        });
                    })
                  }
                />
              ) : null}
              {!["succeeded", "completed", "cancelled"].includes(
                workflow.state,
              ) ? (
                <Button
                  title={c("停止任务", "Cancel workflow")}
                  variant="ghost"
                  onPress={() =>
                    void action.run(() =>
                      apiClient().assistant.cancelWorkflow(id),
                    )
                  }
                />
              ) : null}
              {workflow.result_targets?.map((target, i) => (
                <Button
                  key={i}
                  title={c("查看结果", "Open result")}
                  variant="outline"
                  onPress={() => navigate(target)}
                />
              ))}
            </Card>
          ) : null}
        </QueryState>
      ) : null}
    </>
  );
}
export function AssistantPreferences() {
  const c = useCopy();
  const action = useAction();
  const prefs = useServerQuery(["assistant-preferences"], (signal) =>
    apiClient().assistant.getPreferences(signal),
  );
  const subs = useServerQuery(["assistant-subscriptions"], (signal) =>
    apiClient().assistant.listSubscriptions(signal),
  );
  const requestKeys = useRef(new Map<string, string>());
  return (
    <>
      <QueryState query={prefs}>
        <Section title={c("回答长度", "Response length")}>
          {(["short", "standard", "detailed"] as const).map((value, i) => (
            <Chip
              key={value}
              label={
                [
                  c("简短", "Short"),
                  c("标准", "Standard"),
                  c("详细", "Detailed"),
                ][i]!
              }
              active={prefs.data?.response_length === value}
              onPress={() =>
                void action.run(() =>
                  apiClient().assistant.putPreferences({
                    base_revision: prefs.data?.revision ?? 0,
                    response_length: value,
                    voice_policy: "cloud",
                    allow_local_fallback: false,
                  }),
                )
              }
            />
          ))}
        </Section>
      </QueryState>
      <Section title={c("学习提醒", "Learning reminders")}>
        <Hint>
          {c(
            "提醒会出现在助手收件箱中。",
            "Reminders appear in the assistant inbox.",
          )}
        </Hint>
        <QueryState query={subs}>
          {(
            [
              "daily_tasks",
              "due_reviews",
              "unfinished_course",
              "weekly_brief",
            ] as const
          ).map((kind, i) => {
            const existing = subs.data?.items.find(
              (item) => item.kind === kind,
            );
            return (
              <Card key={kind} style={{ gap: 8 }}>
                <Label>
                  {
                    [
                      c("今日任务", "Daily tasks"),
                      c("到期复习", "Due reviews"),
                      c("未完成课堂", "Unfinished lessons"),
                      c("每周回顾", "Weekly review"),
                    ][i]
                  }
                </Label>
                <Button
                  title={
                    existing?.enabled
                      ? c("关闭提醒", "Disable")
                      : c("开启提醒", "Enable")
                  }
                  variant="outline"
                  loading={action.pending}
                  onPress={() =>
                    void action.run(() => {
                      if (existing)
                        return apiClient().assistant.patchSubscription(
                          existing.subscription_id,
                          {
                            expected_revision: existing.revision,
                            enabled: !existing.enabled,
                          },
                        );
                      let key = requestKeys.current.get(kind);
                      if (!key) {
                        key = randomUUID();
                        requestKeys.current.set(kind, key);
                      }
                      return apiClient().assistant.createSubscription({
                        kind,
                        client_request_id: key,
                        timezone:
                          Intl.DateTimeFormat().resolvedOptions().timeZone,
                        local_time: "08:00",
                      });
                    })
                  }
                />
              </Card>
            );
          })}
        </QueryState>
      </Section>
    </>
  );
}
