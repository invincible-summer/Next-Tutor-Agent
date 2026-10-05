import React, { useEffect, useRef, useState } from "react";
import { View } from "react-native";
import { usePathname, useRouter } from "expo-router";
import { randomUUID } from "expo-crypto";
import type {
  AssistantAction,
  AssistantMessage,
  ActionPreview,
  AssistantRouteId,
} from "@next-tutor/contracts/assistant";
import { AssistantWorkflows, AssistantPreferences } from "./AssistantWorkflows";
import { useTheme } from "@/ui/ThemeProvider";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import {
  useAction,
  useServerQuery,
  record,
  rows,
  str,
} from "@/lib/server-state";
import { useAuth } from "@/providers/AuthProvider";
import { useI18n } from "@/providers/I18nProvider";
import { useWorkspace } from "@/providers/WorkspaceProvider";
import { useAssistantUi } from "@/stores/assistant-ui";
import { assistantTarget } from "@/shell/assistant-target";
import { prepareNativeDraft } from "@/stores/native-handoff";
import { domainRoute } from "@/shell/routes";
import {
  Button,
  Card,
  Field,
  TextArea,
  ListRow,
  RichContentRenderer,
  Sheet,
  SegmentedControl,
  useToast,
} from "@/ui";
import {
  Hint,
  Label,
  Pager,
  QueryState,
  Section,
  SheetBody,
  SheetHeader,
} from "@/ui/Elements";
export function AssistantSheet() {
  const c = useCopy();
  const router = useRouter();
  const pathname = usePathname();
  const { lang, setLang } = useI18n();
  const themePrefs = useTheme();
  const scope = useWorkspace();
  const { state, owner } = useAuth();
  const identity = useRef(owner);
  identity.current = owner;
  const { open, setOpen } = useAssistantUi();
  const action = useAction();
  const toast = useToast();
  const [conversation, setConversation] = useState<string | null>(null);
  const [turnId, setTurnId] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [tab, setTab] = useState("chat");
  const [preview, setPreview] = useState<{
    action: AssistantAction;
    value: ActionPreview;
  } | null>(null);
  const instance = useRef(randomUUID());
  const messageKey = useRef({ signature: "", id: randomUUID() });
  const creationKey = useRef(randomUUID());
  const invocations = useRef(new Map<string, string>());
  const pendingRoute = useRef<{ path: string; ready: () => void } | null>(null);
  useEffect(() => {
    const pending = pendingRoute.current;
    if (pending && pending.path === pathname) {
      pendingRoute.current = null;
      pending.ready();
    }
  }, [pathname]);
  const epoch = useRef(0);
  const pathRef = useRef(pathname);
  if (pathRef.current !== pathname) {
    pathRef.current = pathname;
    epoch.current++;
  }
  const capabilities = useServerQuery(
    ["assistant-capabilities"],
    (s) => apiClient().assistant.capabilities(s),
    { enabled: open, public: true },
  );
  const history = useServerQuery(
    ["assistant-conversations"],
    (s) => apiClient().assistant.listConversations(0, 30, s),
    { enabled: open && state.status === "signed-in" },
  );
  const detail = useServerQuery(
    ["assistant-conversation", conversation],
    (s) => apiClient().assistant.getConversation(conversation!, undefined, s),
    { enabled: open && !!conversation, public: true },
  );
  const turn = useServerQuery(
    ["assistant-turn", turnId],
    (s) => apiClient().assistant.getTurn(turnId!, s),
    {
      enabled: open && !!turnId,
      public: true,
      poll: (data) => (!data || data.state === "running" ? 1000 : false),
    },
  );
  const notifications = useServerQuery(
    ["assistant-notifications"],
    (s) => apiClient().assistant.listNotifications(0, 30, false, s),
    { enabled: open && tab === "inbox" },
  );
  useEffect(() => {
    setConversation(null);
    setTurnId(null);
    setText("");
    setPreview(null);
    pendingRoute.current = null;
    invocations.current.clear();
    creationKey.current = randomUUID();
  }, [owner]);
  const routeId: AssistantRouteId = pathname.startsWith("/tutor")
    ? "chat"
    : pathname.includes("/notes")
      ? "notes"
      : pathname.includes("courses")
        ? "course"
        : pathname.includes("assessment")
          ? "assessment"
          : pathname.includes("illustration")
            ? "tools_illustration"
            : pathname.includes("knowledge")
              ? "knowledge"
              : pathname.includes("plan")
                ? "orchestration"
                : pathname.includes("resources")
                  ? "resources_files"
                  : pathname.startsWith("/me")
                    ? "profile"
                    : "home";
  const messages = turn.data
    ? [
        ...(detail.data?.messages ?? []).filter((m) => m.turn_id !== turnId),
        turn.data.user_message,
        turn.data.assistant_message,
      ]
    : (detail.data?.messages ?? []);
  const busy = turn.data?.state === "running";
  const send = (choice?: { block_id: string; option_id: string }) =>
    void action.run(async () => {
      const startedOwner = owner;
      let cid = conversation,
        revision =
          turn.data?.conversation_revision ?? detail.data?.revision ?? 0;
      if (!cid) {
        const created = await apiClient().assistant.createConversation(
          creationKey.current,
        );
        if (identity.current !== startedOwner) return;
        cid = created.conversation_id;
        revision = created.revision;
        setConversation(cid);
      }
      const signature = JSON.stringify([cid, text.trim(), choice]);
      if (messageKey.current.signature !== signature)
        messageKey.current = { signature, id: randomUUID() };
      const accepted = await apiClient().assistant.submitTurn(cid, {
        client_message_id: messageKey.current.id,
        expected_conversation_revision: revision,
        text: text.trim(),
        lang,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        scope:
          state.status === "guest"
            ? { mode: "follow_page" }
            : scope.id
              ? { mode: "workspace", workspace_id: scope.id }
              : { mode: "all_workspaces" },
        page_context: {
          schema_version: 1,
          route_id: routeId,
          route_epoch: epoch.current,
          workspace_id: scope.id,
        },
        ...(choice ? { choice } : {}),
      });
      if (identity.current !== startedOwner) return;
      messageKey.current.signature = "";
      setText("");
      setTurnId(accepted.turn_id);
    });
  const navigate = (target: unknown) => {
    const mapped = assistantTarget(target);
    if (!mapped) return false;
    if (mapped.workspaceId) scope.select(mapped.workspaceId);
    setOpen(false);
    router.push(mapped.href);
    return true;
  };
  async function execute() {
    if (!preview) return;
    const current = preview;
    const startedOwner = owner;
    const routeEpoch = epoch.current;
    await action.run(async () => {
      const approval = await apiClient().assistant.approveAction(
        current.action.action_id,
        {
          preview_id: current.value.preview_id,
          parameter_hash: current.value.parameter_hash,
          decision: "approve",
        },
      );
      if (identity.current !== startedOwner) return;
      let invocationId = invocations.current.get(current.action.action_id);
      if (!invocationId) {
        invocationId = randomUUID();
        invocations.current.set(current.action.action_id, invocationId);
      }
      const result = await apiClient().assistant.executeAction(
        current.action.action_id,
        {
          invocation_id: invocationId,
          client_instance_id: instance.current,
          route_epoch: routeEpoch,
          approval_id: approval.approval_id,
        },
      );
      if (identity.current !== startedOwner) return;
      if (current.action.payload.kind === "set_local_preference") {
        const { key, value } = current.action.payload;
        if (key === "theme" && ["light", "dark", "system"].includes(value))
          themePrefs.setPreference(value as "light" | "dark" | "system");
        else if (
          key === "font_scale" &&
          Number(value) >= 1 &&
          Number(value) <= 1.75
        )
          themePrefs.setFontScale(Number(value));
        else if (key === "lang" && (value === "zh" || value === "en"))
          setLang(value);
      }
      if (result.command) {
        const command = result.command;
        let ok = false,
          deferred = false;
        const ack = (success: boolean) => {
          if (
            identity.current === startedOwner &&
            result.command_id &&
            result.ack_token
          )
            void apiClient()
              .assistant.ackAction(current.action.action_id, {
                command_id: result.command_id,
                ack_token: result.ack_token,
                result: success ? "succeeded" : "failed",
                ...(success ? {} : { error_code: "page_not_ready" as const }),
              })
              .catch(() => {});
        };
        const push = (href: Parameters<typeof router.push>[0]) => {
          const path = (
            typeof href === "string"
              ? href.split("?")[0]!
              : String(href.pathname).replace(/\[([^\]]+)\]/g, (_, key) =>
                  String(href.params?.[key] ?? ""),
                )
          ).replace("/(main)", "");
          if (path === pathname) {
            requestAnimationFrame(() => ack(true));
          } else pendingRoute.current = { path, ready: () => ack(true) };
          deferred = true;
          router.push(href);
          setOpen(false);
        };
        if (routeEpoch === epoch.current) {
          if (command.kind === "navigate") {
            const mapped = assistantTarget(command.target);
            if (mapped) {
              if (mapped.workspaceId) scope.select(mapped.workspaceId);
              push(mapped.href);
              ok = true;
            }
          } else if (command.kind === "workspace_form") {
            scope.open();
            ok = true;
          } else if (
            command.kind === "chat_draft" ||
            command.kind === "note_draft" ||
            command.kind === "lesson_form" ||
            command.kind === "classroom_question"
          ) {
            const draft = await apiClient().assistant.getDraft(
              command.draft_id,
            );
            if (draft.consumed || Date.parse(draft.expires_at) <= Date.now())
              throw new Error("draft_expired");
            if (identity.current !== startedOwner) return;
            if (routeEpoch !== epoch.current) {
              ack(false);
              setPreview(null);
              return;
            }
            const prefill = draft.prefill;
            if (prefill.kind === "chat") {
              if (prefill.workspace_id) scope.select(prefill.workspace_id);
              push(
                domainRoute({
                  kind: "chat",
                  text: prefill.text,
                  ...(prefill.session_id ? { id: prefill.session_id } : {}),
                  ...(prefill.workspace_id
                    ? { workspaceId: prefill.workspace_id }
                    : {}),
                }),
              );
              ok = true;
            } else if (prefill.kind === "note" || prefill.kind === "lesson") {
              if (prefill.kind === "lesson") scope.select(prefill.workspace_id);
              prepareNativeDraft(prefill.kind, draft, () => ack(true));
              router.push(
                prefill.kind === "note"
                  ? "/(main)/library/notes"
                  : "/(main)/learn/courses",
              );
              deferred = true;
              setOpen(false);
              ok = true;
            } else if (prefill.kind === "classroom_question") {
              const qa = await apiClient().classroom.ensureQaSession(
                prefill.workspace_id,
                prefill.lesson_id,
                prefill.run_id,
                "assistant-" + draft.draft_id,
              );
              if (identity.current !== startedOwner) return;
              if (routeEpoch !== epoch.current) {
                ack(false);
                setPreview(null);
                return;
              }
              scope.select(prefill.workspace_id);
              push(
                domainRoute({
                  kind: "chat",
                  id: qa.session_id,
                  text: prefill.question,
                  workspaceId: prefill.workspace_id,
                }),
              );
              ok = true;
            }
          }
        }
        if (!deferred) ack(ok);
        if (!ok)
          toast(
            c(
              "此动作需要原页面继续操作。",
              "Continue this action from its original page.",
            ),
            "info",
          );
      }
      setPreview(null);
      void detail.refetch();
      if (turnId) void turn.refetch();
    });
  }
  function renderMessage(message: AssistantMessage) {
    return (
      <Card
        key={message.message_id}
        style={{
          gap: 12,
          backgroundColor: message.role === "user" ? undefined : undefined,
        }}
      >
        {message.blocks?.map((block) => {
          if (block.type === "markdown" || block.type === "notice")
            return (
              <RichContentRenderer
                key={block.block_id}
                text={block.text}
                streaming={message.status === "streaming"}
              />
            );
          if (block.type === "actions")
            return (
              <View key={block.block_id} style={{ gap: 8 }}>
                {block.items.map((a) => (
                  <Button
                    key={a.action_id}
                    title={a.label}
                    variant="outline"
                    disabled={busy || action.pending}
                    onPress={() =>
                      void action.run(
                        () =>
                          apiClient().assistant.getActionPreview(a.action_id),
                        (value) => setPreview({ action: a, value }),
                      )
                    }
                  />
                ))}
              </View>
            );
          if (block.type === "choices")
            return (
              <View key={block.block_id} style={{ gap: 8 }}>
                <Label>{block.prompt}</Label>
                {block.options.map((o) => (
                  <Button
                    key={o.option_id}
                    title={o.label}
                    variant="outline"
                    onPress={() =>
                      send({ block_id: block.block_id, option_id: o.option_id })
                    }
                  />
                ))}
              </View>
            );
          if (block.type === "metrics")
            return (
              <View key={block.block_id} style={{ gap: 8 }}>
                {block.items.map((m) => (
                  <Hint key={m.key}>
                    {m.label}: {m.value ?? c("暂不可用", "Unavailable")}{" "}
                    {m.unit}
                  </Hint>
                ))}
              </View>
            );
          if (block.type === "learning_report")
            return (
              <View key={block.block_id} style={{ gap: 8 }}>
                {block.report.facts?.map((s, i) => (
                  <Label key={i}>
                    {s.label}: {s.value ?? c("暂不可用", "Unavailable")}
                  </Label>
                ))}
              </View>
            );
          return null;
        })}
        {message.sources?.length ? (
          <Section title={c("依据", "Sources")}>
            {message.sources.map((s) => (
              <ListRow
                key={s.source_id}
                title={s.title}
                onPress={() => navigate(s.locator)}
              />
            ))}
          </Section>
        ) : null}
      </Card>
    );
  }
  return (
    <>
      <Sheet
        open={open}
        onClose={() => setOpen(false)}
        label={c("学习助手", "Learning assistant")}
      >
        <SheetHeader
          title={c("学习助手", "Learning assistant")}
          onClose={() => setOpen(false)}
        />
        <SheetBody>
          <SegmentedControl
            items={[
              { key: "chat", label: c("对话", "Conversation") },
              { key: "history", label: c("历史", "History") },
              { key: "inbox", label: c("提醒", "Inbox") },
              { key: "workflows", label: c("任务", "Workflows") },
              { key: "preferences", label: c("偏好", "Preferences") },
            ]}
            active={tab}
            onChange={setTab}
          />
          <QueryState query={capabilities}>
            {!capabilities.data?.enabled ? (
              <Hint>
                {c("学习助手暂未启用。", "The learning assistant is disabled.")}
              </Hint>
            ) : tab === "chat" ? (
              <>
                {!messages.length ? (
                  <Card style={{ gap: 12, padding: 20 }}>
                    <Label>{c("一起找到下一步", "Find your next step")}</Label>
                    <Hint>
                      {c(
                        "可以帮你寻找资料、回顾学习证据，或准备下一次学习。涉及修改的动作会先展示预览。",
                        "Find materials, explore evidence or prepare the next learning activity. Changes are previewed before execution.",
                      )}
                    </Hint>
                  </Card>
                ) : null}
                {messages.map(renderMessage)}
                <Field
                  label={c("你想了解什么？", "What would you like to explore?")}
                >
                  <TextArea
                    value={text}
                    onChangeText={setText}
                    accessibilityLabel={c("助手消息", "Assistant message")}
                    editable={!busy}
                  />
                </Field>
                <Button
                  title={c("发送", "Send")}
                  disabled={!text.trim() || busy}
                  loading={action.pending}
                  onPress={() => send()}
                />
                {busy ? (
                  <Button
                    title={c("停止本轮", "Stop this turn")}
                    variant="ghost"
                    onPress={() =>
                      void action.run(() =>
                        apiClient().assistant.cancelTurn(turnId!, randomUUID()),
                      )
                    }
                  />
                ) : null}
              </>
            ) : tab === "history" ? (
              <QueryState query={history} empty={!history.data?.items?.length}>
                <Button
                  title={c("新对话", "New conversation")}
                  variant="outline"
                  onPress={() => {
                    creationKey.current = randomUUID();
                    setConversation(null);
                    setTurnId(null);
                    setTab("chat");
                  }}
                />
                {history.data?.items?.map((item) => (
                  <ListRow
                    key={item.conversation_id}
                    title={
                      item.title || c("助手对话", "Assistant conversation")
                    }
                    onPress={() => {
                      setConversation(item.conversation_id);
                      setTurnId(null);
                      setTab("chat");
                    }}
                  />
                ))}
              </QueryState>
            ) : tab === "workflows" ? (
              <AssistantWorkflows navigate={navigate} />
            ) : tab === "preferences" ? (
              <AssistantPreferences />
            ) : (
              <QueryState
                query={notifications}
                empty={!notifications.data?.items.length}
              >
                {notifications.data?.items.map((n) => (
                  <Card key={n.notification_id} style={{ gap: 8 }}>
                    <Label>{n.title}</Label>
                    <Hint>{n.summary}</Hint>
                    <Button
                      title={c("已读", "Mark read")}
                      variant="ghost"
                      onPress={() =>
                        void action.run(() =>
                          apiClient().assistant.markNotificationRead(
                            n.notification_id,
                          ),
                        )
                      }
                    />
                    {n.target ? (
                      <Button
                        title={c("查看", "Open")}
                        variant="outline"
                        onPress={() => navigate(n.target)}
                      />
                    ) : null}
                    <Button
                      title={c("隐藏", "Dismiss")}
                      variant="ghost"
                      onPress={() =>
                        void action.run(() =>
                          apiClient().assistant.dismissNotification(
                            n.notification_id,
                          ),
                        )
                      }
                    />
                  </Card>
                ))}
              </QueryState>
            )}
          </QueryState>
        </SheetBody>
      </Sheet>
      <Sheet
        open={!!preview}
        onClose={() => setPreview(null)}
        label={c("操作预览", "Action preview")}
      >
        <SheetHeader
          title={preview?.value.title ?? ""}
          onClose={() => setPreview(null)}
        />
        <SheetBody>
          <Label>{preview?.value.summary}</Label>
          {preview?.value.changes?.map((change, i) => (
            <Hint key={i}>
              {change.label}: {change.before ?? ""} → {change.after ?? ""}
            </Hint>
          ))}
          {preview?.value.side_effects?.map((t) => (
            <Hint key={t}>{t}</Hint>
          ))}
          <Button
            title={
              preview?.value.approval === "original_page_required"
                ? c("在原页面继续", "Continue in the original page")
                : c("确认执行", "Confirm action")
            }
            loading={action.pending}
            onPress={() =>
              preview?.value.approval === "original_page_required"
                ? navigate(preview.value.affected_entities?.[0]) &&
                  setPreview(null)
                : void execute()
            }
          />
          <Button
            title={c("取消", "Cancel")}
            variant="ghost"
            onPress={() => setPreview(null)}
          />
        </SheetBody>
      </Sheet>
    </>
  );
}
