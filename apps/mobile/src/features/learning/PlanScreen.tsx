import React, { useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { useAction, useServerQuery } from "@/lib/server-state";
import { confirm } from "@/lib/feedback";
import { useWorkspace } from "@/providers/WorkspaceProvider";
import { domainRoute } from "@/shell/routes";
import {
  Button,
  Card,
  Chip,
  Field,
  TextField,
  TextArea,
  ListRow,
  SegmentedControl,
} from "@/ui";
import { Body, Hint, Label, QueryState, Section } from "@/ui/Elements";
import { FeatureShell } from "@/ui/FeatureShell";
import { EditSheet } from "@/ui/EditSheet";
function localDate(date: Date) {
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ].join("-");
}
function validDate(value: string) {
  return (
    /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    localDate(new Date(value + "T12:00:00")) === value
  );
}
type Edit = {
  kind:
    "goal" | "task" | "schedule" | "week" | "weekTask" | "subtask" | "concept";
  id?: string;
  week?: number;
};
export function PlanScreen() {
  const c = useCopy();
  const router = useRouter();
  const scope = useWorkspace();
  const action = useAction();
  const plan = useServerQuery(["plan"], (s) => apiClient().learning.plan(s));
  const today = useServerQuery(["today"], (s) => apiClient().learning.today(s));
  const review = useServerQuery(["review"], (s) =>
    apiClient().learning.review(s),
  );
  const [tab, setTab] = useState("today");
  const [edit, setEdit] = useState<Edit | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [minutes, setMinutes] = useState("25");
  const [day, setDay] = useState(localDate(new Date()));
  const [deadline, setDeadline] = useState("");
  const [conceptSearch, setConceptSearch] = useState("");
  const [conceptId, setConceptId] = useState("");
  const concepts = useServerQuery(
    ["plan-concept-search", scope.id, conceptSearch],
    (signal) =>
      apiClient().knowledge.graph(
        {
          view: "search",
          q: conceptSearch,
          ...(scope.id ? { workspaceId: scope.id } : {}),
        },
        signal,
      ),
    { enabled: edit?.kind === "concept" && conceptSearch.trim().length >= 2 },
  );
  const begin = (value: Edit, t = "") => {
    setTitle(t);
    setConceptSearch("");
    setConceptId("");
    setDescription("");
    setDeadline("");
    setEdit(value);
  };
  const remove = (title: string, work: () => Promise<unknown>) =>
    void confirm(
      c("移除此项？", "Remove this item?"),
      title,
      c("移除", "Remove"),
    ).then((ok) => {
      if (ok) void action.run(work);
    });
  const launch = (id: string) =>
    void action.run(
      () => apiClient().learning.launchTask(id),
      (r) => {
        if (r.session_id)
          router.push(domainRoute({ kind: "chat", id: r.session_id }));
      },
    );
  const save = () =>
    void action.run(
      async () => {
        if (!edit) return;
        if (
          (edit.kind === "task" && !validDate(day)) ||
          (deadline && !validDate(deadline))
        )
          throw new Error(
            c("请输入有效日期 YYYY-MM-DD", "Enter a valid YYYY-MM-DD date"),
          );
        switch (edit.kind) {
          case "goal": {
            const payload = {
              title: title.trim(),
              description,
              workspace_id: scope.id ?? "",
              ...(deadline
                ? { deadline: Math.floor(new Date(deadline).getTime() / 1000) }
                : {}),
            };
            return edit.id
              ? apiClient().learning.patchGoal(edit.id, payload)
              : apiClient().learning.setGoal(payload);
          }
          case "task": {
            const payload = {
              title: title.trim(),
              day,
              estimate_minutes: Number(minutes) || 25,
            };
            return edit.id
              ? apiClient().learning.updateTask(edit.id, payload)
              : apiClient().learning.addTask(payload);
          }
          case "schedule":
            return apiClient().learning.patchSchedule(
              Math.max(5, Math.min(480, Number(minutes) || 25)),
            );
          case "week":
            return apiClient().learning.addWeek({ focus: title.trim() });
          case "weekTask":
            return apiClient().learning.addWeekTask(edit.week!, {
              title: title.trim(),
            });
          case "concept":
            return apiClient().learning.addWeekConcept(edit.week!, {
              concept_id: conceptId,
              name: title,
            });
          case "subtask":
            return apiClient().learning.addSubtask(edit.week!, edit.id!, {
              title: title.trim(),
              estimate_minutes: Number(minutes) || 15,
            });
        }
      },
      () => setEdit(null),
    );
  return (
    <FeatureShell title={c("计划与学习编排", "Your learning plan")}>
      <Body>
        <Card style={{ gap: 12, padding: 24 }}>
          <Label>
            {c("给目标一个可以抵达的节奏", "Give your goal a rhythm")}
          </Label>
          <Hint>
            {c(
              "计划可以调整。留一点空间给练习、回顾，也留给新的好奇。",
              "Plans can change. Make room for practice, review and new curiosity.",
            )}
          </Hint>
          <Button
            title={c("设定目标", "Set a goal")}
            onPress={() => begin({ kind: "goal" })}
          />
        </Card>
        <SegmentedControl
          items={[
            { key: "today", label: c("今天", "Today") },
            { key: "weeks", label: c("每周", "Weeks") },
            { key: "goals", label: c("目标", "Goals") },
            { key: "review", label: c("复习", "Review") },
          ]}
          active={tab}
          onChange={setTab}
        />
        <QueryState query={plan}>
          {tab === "today" ? (
            <>
              <Section
                title={c("今日任务", "Today's tasks")}
                action={c("添加", "Add")}
                onAction={() => begin({ kind: "task" })}
              >
                {today.data
                  ?.filter(
                    (t) =>
                      !scope.id ||
                      !t.workspace_id ||
                      t.workspace_id === scope.id,
                  )
                  .map((t) => (
                    <Card key={t.id} style={{ gap: 12 }}>
                      <Label>
                        {t.title ||
                          t.concept_name ||
                          c("学习任务", "Learning task")}
                      </Label>
                      <Hint>{t.reason}</Hint>
                      <Hint>
                        {c(
                          `${t.estimate_minutes ?? 25} 分钟 · ${t.status === "completed" ? "已完成" : "待学习"}`,
                          `${t.estimate_minutes ?? 25} min · ${t.status === "completed" ? "Done" : "To do"}`,
                        )}
                      </Hint>
                      <View
                        style={{
                          flexDirection: "row",
                          flexWrap: "wrap",
                          gap: 8,
                        }}
                      >
                        {t.status !== "completed" ? (
                          <>
                            <Button
                              title={c("开始", "Start")}
                              loading={action.pending}
                              onPress={() => launch(t.id)}
                            />
                            <Button
                              title={c("完成", "Done")}
                              variant="outline"
                              onPress={() =>
                                void action.run(() =>
                                  apiClient().learning.completeTask(t.id),
                                )
                              }
                            />
                          </>
                        ) : null}
                        <Button
                          title={c("编辑", "Edit")}
                          variant="ghost"
                          onPress={() => {
                            begin(
                              { kind: "task", id: t.id },
                              t.title || t.concept_name,
                            );
                            setDay(t.day);
                            setMinutes(String(t.estimate_minutes ?? 25));
                          }}
                        />
                        <Button
                          title={c("移除", "Remove")}
                          variant="ghost"
                          onPress={() =>
                            remove(t.title ?? "", () =>
                              apiClient().learning.deleteTask(t.id),
                            )
                          }
                        />
                      </View>
                    </Card>
                  ))}
              </Section>
              <Button
                title={c("调整每日学习时间", "Adjust daily learning time")}
                variant="outline"
                onPress={() => begin({ kind: "schedule" })}
              />
            </>
          ) : tab === "goals" ? (
            <>
              {plan.data?.goals?.map((g) => (
                <Card key={g.id} style={{ gap: 12 }}>
                  <Label>{g.title}</Label>
                  <Hint>{g.description}</Hint>
                  <View style={{ flexDirection: "row", gap: 8 }}>
                    <Button
                      title={c("编辑", "Edit")}
                      variant="outline"
                      onPress={() => {
                        begin({ kind: "goal", id: g.id }, g.title);
                        setDescription(g.description ?? "");
                        setDeadline(
                          g.deadline
                            ? new Date(g.deadline * 1000)
                                .toISOString()
                                .slice(0, 10)
                            : "",
                        );
                      }}
                    />
                    <Button
                      title={c("移除", "Remove")}
                      variant="ghost"
                      onPress={() =>
                        remove(g.title, () =>
                          apiClient().learning.deleteGoal(g.id),
                        )
                      }
                    />
                  </View>
                </Card>
              ))}
            </>
          ) : tab === "weeks" ? (
            <>
              <Button
                title={c("添加学习周", "Add a week")}
                variant="outline"
                onPress={() => begin({ kind: "week" })}
              />
              <Button
                title={c("根据目标重新编排", "Regenerate from goals")}
                variant="ghost"
                loading={action.pending}
                onPress={() =>
                  void action.run(() => apiClient().learning.regenerate())
                }
              />
              {plan.data?.weekly_plan?.map((w) => (
                <Section
                  key={w.week_index}
                  title={c(
                    `第 ${w.week_index + 1} 周 · ${w.focus ?? ""}`,
                    `Week ${w.week_index + 1} · ${w.focus ?? ""}`,
                  )}
                  action={c("添加任务", "Add task")}
                  onAction={() =>
                    begin({ kind: "weekTask", week: w.week_index })
                  }
                >
                  <Button
                    title={c("添加知识点", "Add concept")}
                    variant="outline"
                    onPress={() =>
                      begin({ kind: "concept", week: w.week_index })
                    }
                  />
                  <View
                    style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}
                  >
                    {w.concepts?.map((n) => (
                      <View key={n.concept_id} style={{ gap: 4 }}>
                        <Chip
                          label={n.name}
                          onPress={() =>
                            router.push(
                              domainRoute({
                                kind: "concept",
                                id: n.concept_id,
                              }),
                            )
                          }
                        />
                        <Button
                          title={c("移除知识点", "Remove concept")}
                          variant="ghost"
                          onPress={() =>
                            remove(n.name, () =>
                              apiClient().learning.removeWeekConcept(
                                w.week_index,
                                n.concept_id,
                              ),
                            )
                          }
                        />
                      </View>
                    ))}
                  </View>
                  {w.tasks?.map((t) => (
                    <Card key={t.id} style={{ gap: 12 }}>
                      <Label>{t.title}</Label>
                      {t.subtasks?.map((s) => (
                        <View key={s.id} style={{ gap: 4 }}>
                          <ListRow
                            title={s.title}
                            badge={{
                              label: s.done
                                ? c("已完成", "Done")
                                : c("待办", "To do"),
                            }}
                            onPress={() =>
                              void action.run(() =>
                                apiClient().learning.toggleSubtask(
                                  w.week_index,
                                  t.id,
                                  s.id,
                                ),
                              )
                            }
                          />
                          <Button
                            title={c("移除步骤", "Remove step")}
                            variant="ghost"
                            onPress={() =>
                              remove(s.title, () =>
                                apiClient().learning.deleteSubtask(
                                  w.week_index,
                                  t.id,
                                  s.id,
                                ),
                              )
                            }
                          />
                        </View>
                      ))}
                      <View
                        style={{
                          flexDirection: "row",
                          flexWrap: "wrap",
                          gap: 8,
                        }}
                      >
                        <Button
                          title={c("添加小步骤", "Add a step")}
                          variant="outline"
                          onPress={() =>
                            begin({
                              kind: "subtask",
                              week: w.week_index,
                              id: t.id,
                            })
                          }
                        />
                        <Button
                          title={c("建议步骤", "Suggest steps")}
                          variant="ghost"
                          loading={action.pending}
                          onPress={() =>
                            void action.run(() =>
                              apiClient().learning.suggestSubtasks(
                                w.week_index,
                                t.id,
                              ),
                            )
                          }
                        />
                        <Button
                          title={c("删除任务", "Remove task")}
                          variant="ghost"
                          onPress={() =>
                            remove(t.title, () =>
                              apiClient().learning.deleteWeekTask(
                                w.week_index,
                                t.id,
                              ),
                            )
                          }
                        />
                      </View>
                    </Card>
                  ))}
                  <Button
                    title={c("删除此周", "Remove week")}
                    variant="ghost"
                    onPress={() =>
                      remove(w.focus ?? "", () =>
                        apiClient().learning.deleteWeek(w.week_index),
                      )
                    }
                  />
                </Section>
              ))}
            </>
          ) : (
            <QueryState query={review} empty={!review.data?.length}>
              {review.data?.map((r) => (
                <Card key={r.concept_id} style={{ padding: 8 }}>
                  <ListRow
                    title={r.concept_name || r.concept_id}
                    subtitle={c(
                      "到期知识点，适合再次练习。",
                      "A concept ready to revisit.",
                    )}
                    onPress={() =>
                      router.push(
                        domainRoute({
                          kind: "chat",
                          text: c(
                            `带我复习 ${r.concept_name || r.concept_id}`,
                            `Help me review ${r.concept_name || r.concept_id}`,
                          ),
                          ...(r.workspace_id
                            ? { workspaceId: r.workspace_id }
                            : {}),
                        }),
                      )
                    }
                  />
                </Card>
              ))}
            </QueryState>
          )}
        </QueryState>
      </Body>
      <EditSheet
        open={!!edit}
        title={c("调整你的计划", "Adjust your plan")}
        onClose={() => setEdit(null)}
        onSave={save}
        pending={action.pending}
        disabled={
          (edit?.kind !== "schedule" && !title.trim()) ||
          (edit?.kind === "concept" && !conceptId) ||
          (!!deadline && !Number.isFinite(new Date(deadline).getTime()))
        }
      >
        {edit?.kind === "concept" ? (
          <>
            <Field label={c("寻找知识点", "Find a concept")}>
              <TextField
                value={conceptSearch}
                onChangeText={setConceptSearch}
                accessibilityLabel={c("寻找知识点", "Find a concept")}
              />
            </Field>
            <QueryState query={concepts} empty={!concepts.data?.nodes.length}>
              {concepts.data?.nodes
                .filter((n) => n.kind !== "chapter")
                .slice(0, 12)
                .map((n) => (
                  <ListRow
                    key={n.id}
                    title={n.name}
                    {...(conceptId === n.id
                      ? { badge: { label: c("已选择", "Selected") } }
                      : {})}
                    onPress={() => {
                      setConceptId(n.id);
                      setTitle(n.name);
                    }}
                  />
                ))}
            </QueryState>
          </>
        ) : null}
        {edit?.kind !== "schedule" && edit?.kind !== "concept" ? (
          <Field label={c("标题", "Title")}>
            <TextField
              value={title}
              onChangeText={setTitle}
              accessibilityLabel={c("标题", "Title")}
            />
          </Field>
        ) : null}
        {edit?.kind === "goal" ? (
          <>
            <Field label={c("目标说明", "Description")}>
              <TextArea
                value={description}
                onChangeText={setDescription}
                accessibilityLabel={c("目标说明", "Description")}
              />
            </Field>
            <Field label={c("截止日期（可选）", "Deadline (optional)")}>
              <TextField
                value={deadline}
                onChangeText={setDeadline}
                placeholder="YYYY-MM-DD"
                accessibilityLabel={c("截止日期", "Deadline")}
              />
            </Field>
          </>
        ) : null}
        {edit?.kind === "task" ? (
          <Field label={c("日期", "Date")}>
            <TextField
              value={day}
              onChangeText={setDay}
              placeholder="YYYY-MM-DD"
              accessibilityLabel={c("任务日期", "Task date")}
            />
          </Field>
        ) : null}
        {edit?.kind === "task" ||
        edit?.kind === "subtask" ||
        edit?.kind === "schedule" ? (
          <Field label={c("分钟", "Minutes")}>
            <TextField
              value={minutes}
              onChangeText={setMinutes}
              keyboardType="number-pad"
              accessibilityLabel={c("学习分钟", "Learning minutes")}
            />
          </Field>
        ) : null}
      </EditSheet>
    </FeatureShell>
  );
}
