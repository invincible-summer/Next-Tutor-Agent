import React, { useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { useAction, useServerQuery } from "@/lib/server-state";
import { domainRoute } from "@/shell/routes";
import { Button, Card, Chip, ListRow, EmptyState, ErrorState } from "@/ui";
import { Body, Hint, Label, QueryState, Section } from "@/ui/Elements";
import { FeatureShell } from "@/ui/FeatureShell";
export function MemoryScreen() {
  const c = useCopy();
  const router = useRouter();
  const action = useAction();
  const profile = useServerQuery(["prompt-memory"], (s) =>
    apiClient().memory.promptProfile(s),
  );
  const strategies = useServerQuery(["memory-strategies"], (s) =>
    apiClient().memory.procedural(s),
  );
  const summary = Object.entries(profile.data?.core_profile ?? {}).filter(
    ([, value]) => value.trim(),
  );
  return (
    <FeatureShell title={c("学习记忆", "Learning memory")}>
      <Body>
        <Card style={{ gap: 12, padding: 24 }}>
          <Label>
            {c("让对话有所延续", "Keep a thread across conversations")}
          </Label>
          <Hint>
            {c(
              "有界记忆保留对学习有用的摘要，不等同于完整聊天记录。",
              "Bounded memory keeps useful learning summaries. It is separate from full conversation history.",
            )}
          </Hint>
        </Card>
        <QueryState query={profile}>
          <Section title={c("记忆窗口", "Memory window")}>
            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
              {[5, 10, 15, 20, 25, 30]
                .filter((n) => n <= (profile.data?.max_window ?? 30))
                .map((n) => (
                  <Chip
                    key={n}
                    label={c(`${n} 个会话`, `${n} sessions`)}
                    active={profile.data?.window_size === n}
                    onPress={() =>
                      void action.run(() =>
                        apiClient().memory.setPromptWindow(n),
                      )
                    }
                  />
                ))}
            </View>
          </Section>
          <Section title={c("当前学习摘要", "Current learning summary")}>
            {summary.length ? (
              <Card style={{ gap: 12 }}>
                {summary.map(([key, value]) => (
                  <View key={key} style={{ gap: 6 }}>
                    <Hint>
                      {(
                        {
                          learning_summary: c("学习摘要", "Learning summary"),
                          tone_preference: c("交流语气", "Conversation tone"),
                          explanation_preference: c(
                            "讲解偏好",
                            "Explanation preference",
                          ),
                          current_level_retired: c(
                            "历史学习描述",
                            "Previous learning description",
                          ),
                        } as Record<string, string>
                      )[key] ?? key}
                    </Hint>
                    <Label>{value}</Label>
                  </View>
                ))}
              </Card>
            ) : (
              <EmptyState
                title={c("记忆还在慢慢形成", "Memory is taking shape")}
                hint={c(
                  "有帮助的学习对话会逐渐形成有界摘要。",
                  "Helpful learning conversations gradually build a bounded summary.",
                )}
              />
            )}
          </Section>
          <Section title={c("近期记忆来源", "Recent sources")}>
            {profile.data?.recent_sessions?.length ? (
              <Card>
                {profile.data?.recent_sessions?.map((s) => (
                  <ListRow
                    key={s.session_id}
                    title={c("打开源会话", "Open source conversation")}
                    subtitle={
                      s.has_contribution
                        ? c("有记忆贡献", "Contributes to memory")
                        : c("无记忆贡献", "No contribution")
                    }
                    onPress={() =>
                      router.push(
                        domainRoute({ kind: "chat", id: s.session_id }),
                      )
                    }
                  />
                ))}
              </Card>
            ) : (
              <Hint>
                {c(
                  "目前没有近期会话来源。",
                  "No recent source conversations yet.",
                )}
              </Hint>
            )}
            {(profile.data?.compacted_session_count ?? 0) > 0 ? (
              <Hint>
                {c(
                  "较早的记忆已合入整体摘要，无法按单个会话安全拆分。",
                  "Older memory is part of the combined summary and cannot be safely separated by conversation.",
                )}
              </Hint>
            ) : null}
          </Section>
        </QueryState>
        <Section title={c("教学策略观察", "Teaching strategy observations")}>
          <QueryState query={strategies}>
            {strategies.data?.status === "disabled" ? (
              <Hint>
                {c(
                  "此记忆层未启用。辅导仍可继续。",
                  "This memory layer is disabled. Tutoring can continue.",
                )}
              </Hint>
            ) : strategies.data?.status === "error" ? (
              <ErrorState
                title={c(
                  "暂时无法读取策略观察",
                  "Strategy observations could not be loaded",
                )}
                retryLabel={c("重试", "Retry")}
                onRetry={() => void strategies.refetch()}
              />
            ) : !strategies.data?.strategies?.length ? (
              <EmptyState
                title={c("还没有策略观察", "No strategy observations yet")}
                hint={c(
                  "完成更多学习后，会显示服务端记录的教学观察。",
                  "Server-recorded teaching observations will appear after more learning.",
                )}
              />
            ) : (
              strategies.data?.strategies?.map((s, i) => (
                <Card key={i} style={{ gap: 8 }}>
                  <Label>{s.strategy}</Label>
                  <Hint>
                    {s.subject} ·{" "}
                    {c(
                      `${s.trials ?? 0} 次观察`,
                      `${s.trials ?? 0} observations`,
                    )}
                  </Hint>
                </Card>
              ))
            )}
          </QueryState>
        </Section>
      </Body>
    </FeatureShell>
  );
}
