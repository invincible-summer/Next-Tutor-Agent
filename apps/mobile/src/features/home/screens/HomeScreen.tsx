import React from "react";
import { Text, View } from "react-native";
import { useRouter } from "expo-router";
import {
  ArrowRight,
  BookOpen,
  CalendarDays,
  Library,
  MessageCircle,
  NotebookPen,
  Play,
  Sparkles,
} from "lucide-react-native";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { useServerQuery, str } from "@/lib/server-state";
import { useAuth } from "@/providers/AuthProvider";
import { useWorkspace, WorkspaceChip } from "@/providers/WorkspaceProvider";
import { useAdaptive } from "@/shell/adaptive/window-class";
import { domainRoute } from "@/shell/routes";
import { Button, Card, ListRow, ScreenHeader, useTheme } from "@/ui";
import { Body, Hint, Label, QueryState, Section, Tile } from "@/ui/Elements";
import { PageShell } from "@/ui/PageShell";
import { LearningArt } from "@/ui/LearningArt";
import type { SessionItem } from "@/features/chat/model/types";
export function HomeScreen() {
  const c = useCopy();
  const { theme } = useTheme();
  const router = useRouter();
  const auth = useAuth();
  const scope = useWorkspace();
  const adaptive = useAdaptive();
  const sessions = useServerQuery(["sessions"], () =>
    apiClient().chat.listSessions<{ sessions: SessionItem[] }>(),
  );
  const tasks = useServerQuery(["today"], (s) => apiClient().learning.today(s));
  const review = useServerQuery(["review"], (s) =>
    apiClient().learning.review(s),
  );
  const vault = useServerQuery(["notes"], (s) => apiClient().notes.vault(s));
  const active = useServerQuery(["assessment", scope.id], (s) =>
    apiClient().assessment.active(scope.id ?? "", s),
  );
  const lessons = useServerQuery(
    ["home-lessons", scope.id, scope.workspaces.map((w) => w.workspace_id)],
    async (s) => {
      const ids = scope.id
        ? [scope.id]
        : scope.workspaces.map((w) => w.workspace_id);
      const lists = await Promise.all(
        ids.map((id) =>
          apiClient().classroom.listLessons(id, { page: 1, pageSize: 3 }, s),
        ),
      );
      return lists.flatMap((list, i) => [{ workspaceId: ids[i]!, list }]);
    },
    { enabled: scope.workspaces.length > 0 },
  );
  const evidence = useServerQuery(
    ["evaluation", scope.id],
    (s) => apiClient().evaluation.workspace(scope.id!, s),
    { enabled: !!scope.id },
  );
  const recent = (sessions.data?.sessions ?? []).filter(
    (s) => !scope.id || s.workspace_id === scope.id,
  );
  const resume = lessons.data?.find(
    (l) =>
      l.list.resume?.run?.status === "active" ||
      l.list.resume?.run?.status === "paused",
  );
  const assessmentId = str(active.data?.assessment_id);
  const last = recent[0];
  const next = resume?.list.resume
    ? {
        kind: "lesson" as const,
        id: resume.list.resume.lesson_id,
        runId: resume.list.resume.run.run_id,
        workspaceId: resume.workspaceId,
      }
    : assessmentId && active.data?.session_status === "active"
      ? { kind: "assessment" as const }
      : last
        ? { kind: "chat" as const, id: last.session_id }
        : { kind: "chat" as const };
  const nextTitle =
    resume?.list.resume?.title ||
    (assessmentId && active.data?.session_status === "active"
      ? c("继续上次测评", "Continue your assessment")
      : last?.title) ||
    c("从一个问题开始", "Start with a question");
  const name =
    auth.state.status === "signed-in"
      ? auth.state.user.profile.name || auth.state.user.username
      : "";
  const wide = adaptive.maxPanes > 1;
  const columnStyle = {
    width: wide ? ("48.5%" as const) : ("100%" as const),
    gap: 12,
  };
  return (
    <PageShell
      onRefresh={() => {
        void sessions.refetch();
        void tasks.refetch();
        void lessons.refetch();
      }}
      refreshing={sessions.isRefetching}
    >
      <ScreenHeader
        title={c(
          name ? `你好，${name}` : "今天，学点新东西",
          name ? `Hello, ${name}` : "A little learning, today",
        )}
        subtitle={c(
          "好奇心，就是一个好的开始。",
          "Curiosity is a good place to begin.",
        )}
        right={<WorkspaceChip />}
      />
      <Body>
        <Card
          style={{
            padding: 24,
            borderRadius: 24,
            backgroundColor: theme.colors["accent-soft"],
            flexDirection: wide ? "row" : "column",
            gap: 12,
          }}
        >
          <View style={{ flex: 1, gap: 14 }}>
            <Hint>{c("继续学习", "PICK UP WHERE YOU LEFT OFF")}</Hint>
            <Text
              accessibilityRole="header"
              style={[theme.type.title, { color: theme.colors.fg }]}
            >
              {nextTitle}
            </Text>
            <Label>
              {c(
                "留一点时间给自己，一步一步来。",
                "Make a little space for yourself. One step at a time.",
              )}
            </Label>
            <Button
              title={c("继续探索", "Continue learning")}
              rightIcon={<ArrowRight size={18} color={theme.colors.onAccent} />}
              onPress={() => {
                if (resume) scope.select(resume.workspaceId);
                router.push(domainRoute(next));
              }}
              style={{ alignSelf: "flex-start" }}
            />
          </View>
          {wide ? (
            <View style={{ width: 220 }}>
              <LearningArt compact />
            </View>
          ) : null}
        </Card>
        {auth.state.status === "guest" ? (
          <Card style={{ gap: 12 }}>
            <Label>
              {c("欢迎体验你的学习空间", "Welcome to your learning space")}
            </Label>
            <Hint>
              {c(
                "可以浏览公共教材并体验辅导。登录后保存正式学习记录。",
                "Explore public materials and try tutoring. Sign in to save your learning records.",
              )}
            </Hint>
            <Button
              title={c("登录并保存学习", "Sign in to save your learning")}
              variant="outline"
              onPress={() => router.push("/(auth)/sign-in")}
            />
          </Card>
        ) : (
          <View
            style={{
              flexDirection: "row",
              flexWrap: "wrap",
              gap: 20,
              justifyContent: "space-between",
            }}
          >
            <View style={columnStyle}>
              <Section
                title={c("今日计划", "Today's plan")}
                action={c("查看", "View")}
                onAction={() => router.push(domainRoute({ kind: "plan" }))}
              >
                <QueryState query={tasks} empty={!tasks.data?.length}>
                  <Card>
                    {tasks.data?.slice(0, 4).map((task) => (
                      <ListRow
                        key={task.id}
                        title={
                          task.title ||
                          task.concept_name ||
                          c("学习任务", "Learning task")
                        }
                        subtitle={task.reason}
                        badge={{
                          label:
                            task.status === "completed"
                              ? c("已完成", "Done")
                              : c("待学习", "To do"),
                        }}
                        onPress={() =>
                          router.push(domainRoute({ kind: "plan" }))
                        }
                      />
                    ))}
                  </Card>
                </QueryState>
              </Section>
            </View>
            <View style={columnStyle}>
              <Section title={c("最近的探索", "Recent learning")}>
                <QueryState query={sessions} empty={!recent.length}>
                  <Card>
                    {recent.slice(0, 3).map((s) => (
                      <ListRow
                        key={s.session_id}
                        title={
                          s.title || c("辅导会话", "Tutoring conversation")
                        }
                        subtitle={c(
                          `${s.message_count} 条消息`,
                          `${s.message_count} messages`,
                        )}
                        left={
                          <MessageCircle
                            size={18}
                            color={theme.colors.accent}
                          />
                        }
                        onPress={() =>
                          router.push(
                            domainRoute({ kind: "chat", id: s.session_id }),
                          )
                        }
                      />
                    ))}
                  </Card>
                </QueryState>
              </Section>
            </View>
            <View style={columnStyle}>
              <Section
                title={c("温故知新", "Ready for review")}
                action={c("笔记", "Notes")}
                onAction={() => router.push(domainRoute({ kind: "note" }))}
              >
                <QueryState query={review}>
                  <Card style={{ gap: 12 }}>
                    <Label>
                      {c(
                        `${review.data?.length ?? 0} 个知识点待复习`,
                        `${review.data?.length ?? 0} concepts ready for review`,
                      )}
                    </Label>
                    <Hint>
                      {c(
                        `${vault.data?.stats.due_review_count ?? 0} 篇笔记等待回顾`,
                        `${vault.data?.stats.due_review_count ?? 0} notes ready to revisit`,
                      )}
                    </Hint>
                    <Button
                      title={c("开始复习", "Start reviewing")}
                      variant="outline"
                      onPress={() => router.push(domainRoute({ kind: "plan" }))}
                    />
                  </Card>
                </QueryState>
              </Section>
            </View>
            <View style={columnStyle}>
              <Section title={c("学习观察", "Learning observations")}>
                <Card style={{ gap: 12 }}>
                  <Hint>
                    {evidence.data?.synthesis?.statement ||
                      c(
                        "完成学习活动后，这里会呈现有证据支持的观察。选择工作区可查看对应记录。",
                        "After learning activities, find observations supported by your evidence. Choose a workspace to see its records.",
                      )}
                  </Hint>
                  <Button
                    title={c("查看学习证据", "Explore your evidence")}
                    variant="ghost"
                    onPress={() =>
                      router.push(domainRoute({ kind: "insights" }))
                    }
                  />
                </Card>
              </Section>
            </View>
          </View>
        )}
        <Section
          title={c("下一步，想做什么？", "Where will curiosity take you?")}
        >
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 12 }}>
            {[
              {
                kind: "chat" as const,
                title: c("问一问", "Ask your tutor"),
                subtitle: c("一起拆解一个难题", "Work through a question"),
                icon: MessageCircle,
              },
              {
                kind: "lesson" as const,
                title: c("进入课堂", "Open a lesson"),
                subtitle: c("跟随自己的学习节奏", "Learn at your own pace"),
                icon: Play,
              },
              {
                kind: "resources" as const,
                title: c("我的资料", "My materials"),
                subtitle: c("把教材带在身边", "Keep materials close"),
                icon: Library,
              },
              {
                kind: "note" as const,
                title: c("记下所学", "Make a note"),
                subtitle: c("留住新的理解", "Capture an understanding"),
                icon: NotebookPen,
              },
            ].map(({ kind, title, subtitle, icon: Icon }) => (
              <Tile
                key={kind}
                title={title}
                subtitle={subtitle}
                icon={<Icon size={22} color={theme.colors.accent} />}
                onPress={() => router.push(domainRoute({ kind }))}
                style={{ width: wide ? "23.5%" : "48%", flexGrow: 1 }}
              />
            ))}
          </View>
        </Section>
      </Body>
    </PageShell>
  );
}
