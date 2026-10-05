import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AppState,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  Text,
  View,
  type ListRenderItemInfo,
} from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  ArrowDown,
  CirclePlus,
  Files,
  MessageSquareText,
  Rows3,
} from "lucide-react-native";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { useServerQuery } from "@/lib/server-state";
import { useAuth } from "@/providers/AuthProvider";
import { useWorkspace, WorkspaceChip } from "@/providers/WorkspaceProvider";
import { useI18n } from "@/providers/I18nProvider";
import { AdaptivePane } from "@/shell/adaptive/AdaptivePane";
import { useAdaptive } from "@/shell/adaptive/window-class";
import { domainRoute } from "@/shell/routes";
import { SourceContent, SourcePicker } from "@/features/resources/SourcePicker";
import { Button, IconButton, ListRow, ScreenHeader, useTheme } from "@/ui";
import { Body, Hint, QueryState, Section } from "@/ui/Elements";
import { CapabilityGate } from "@/ui/FeatureShell";
import { EmptyState } from "@/ui/EmptyState";
import { useReducedMotion } from "@/ui/useReducedMotion";
import { useChatStore } from "../store/chatStore";
import { useChatStream } from "../hooks/useChatStream";
import { useChatSession } from "../hooks/useChatSession";
import { useComposerDraft } from "../hooks/useComposerDraft";
import { ChatMessageItem } from "../components/ChatMessageItem";
import { StreamingMessage } from "../components/StreamingMessage";
import { Composer } from "../components/Composer";
import { SessionsSheet } from "../components/SessionsSheet";
import { ChatEmpty } from "../components/ChatEmpty";
import type {
  AttachmentMeta,
  ChatSessionDetail,
  SessionItem,
} from "../model/types";
import "../strings";

type ListItem = { kind: "message"; index: number } | { kind: "streaming" };
function first(value: string | string[] | undefined) {
  return Array.isArray(value) ? (value[0] ?? null) : (value ?? null);
}

export function ChatScreen({ sessionId }: { sessionId: string | null }) {
  const { theme } = useTheme();
  const { t } = useI18n();
  const c = useCopy();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const auth = useAuth();
  const scope = useWorkspace();
  const adaptive = useAdaptive();
  const reduced = useReducedMotion();
  const params = useLocalSearchParams<{ q?: string; ws?: string }>();
  const session = useChatSession(sessionId, first(params.ws) ?? scope.id);
  const stream = useChatStream();
  const chat = useChatStore();
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [showDown, setShowDown] = useState(false);
  const listRef = useRef<FlatList<ListItem>>(null);
  const draft = useComposerDraft(auth.owner, sessionId, session.workspaceId);
  const sessions = useServerQuery(["chat-sessions"], () =>
    apiClient().chat.listSessions<{ sessions: SessionItem[] }>(),
  );
  const books = useServerQuery(["textbooks"], (s) =>
    apiClient().library.textbooks.list(s),
  );
  const context = useServerQuery(
    ["chat-context", chat.sessionId],
    () => apiClient().chat.loadSession<ChatSessionDetail>(chat.sessionId!, 1),
    { enabled: !!chat.sessionId },
  );
  const bind = useCallback(
    (id: string) =>
      id !== sessionId &&
      router.replace({
        pathname: "/(main)/tutor/[sessionId]",
        params: { sessionId: id },
      }),
    [router, sessionId],
  );
  const send = useCallback(
    (message: string, attachments?: AttachmentMeta[]) => {
      if (
        auth.state.status === "signed-in" &&
        scope.textbookIds.length > 0 &&
        !books.isSuccess
      )
        return;
      if (auth.state.status === "signed-in") {
        const fileIds = [
          ...new Set([
            ...scope.fileIds,
            ...scope.textbookIds.flatMap(
              (id) =>
                books.data?.textbooks.find((b) => b.id === id)?.file_ids ?? [],
            ),
          ]),
        ];
        const already = new Set(chat.files.map((f) => f.library_file_id));
        useChatStore
          .getState()
          .setPendingLibraryRefs(
            fileIds
              .filter((id) => !already.has(id))
              .map((id) => ({ id, filename: id })),
          );
      }
      void stream.send(message, attachments, {
        workspaceId: session.workspaceId,
        publicTextbookIds:
          auth.state.status === "guest" ? scope.textbookIds : [],
        onSessionBound: bind,
      });
    },
    [
      auth.state.status,
      scope.fileIds,
      scope.textbookIds,
      books.data,
      books.isSuccess,
      chat.files,
      stream,
      session.workspaceId,
      bind,
    ],
  );
  useEffect(() => {
    const listener = AppState.addEventListener("change", (status) => {
      if (
        status === "active" &&
        chat.sessionId &&
        !useChatStore.getState().streaming
      ) {
        const id = chat.sessionId;
        const generation = useChatStore.getState().generation;
        void apiClient()
          .chat.loadSession<ChatSessionDetail>(id, 40)
          .then((detail) => {
            if (
              useChatStore.getState().sessionId === id &&
              useChatStore.getState().generation === generation
            )
              useChatStore.getState().setMessages(detail.messages ?? []);
          })
          .catch(() => {});
        void context.refetch();
        void sessions.refetch();
      }
    });
    return () => listener.remove();
  }, [chat.sessionId]);
  const data = useMemo<ListItem[]>(
    () => [
      ...(chat.streaming ? [{ kind: "streaming" as const }] : []),
      ...chat.messages.map((_, i) => ({
        kind: "message" as const,
        index: chat.messages.length - 1 - i,
      })),
    ],
    [chat.messages, chat.streaming],
  );
  const render = useCallback(
    ({ item }: ListRenderItemInfo<ListItem>) => {
      if (item.kind === "streaming")
        return (
          <StreamingMessage
            thinking={chat.pendingThinking}
            answer={chat.pendingAnswer}
            activeTool={chat.activeTool}
            toolProgress={chat.toolProgress}
            toolCalls={chat.pendingToolCalls}
            currentStep={chat.currentStep}
            heartbeatElapsed={chat.heartbeatElapsed}
            retry={chat.retry}
            sessionId={chat.sessionId}
          />
        );
      const msg = chat.messages[item.index];
      if (!msg) return null;
      return (
        <ChatMessageItem
          msg={msg}
          sessionId={chat.sessionId}
          disabled={chat.streaming}
          onRegenerate={
            msg.role === "assistant" &&
            item.index === chat.messages.length - 1 &&
            !chat.streaming
              ? () =>
                  void stream.regenerate({
                    workspaceId: session.workspaceId,
                    publicTextbookIds: scope.textbookIds,
                    onSessionBound: bind,
                  })
              : undefined
          }
        />
      );
    },
    [chat, stream, session.workspaceId, scope.textbookIds, bind],
  );
  const sourceCount = scope.textbookIds.length + scope.fileIds.length;
  const sourcePanel = (
    <ScrollView keyboardShouldPersistTaps="handled">
      <SourceContent />
      <Body>
        <Section title={c("已绑定的上下文", "Attached context")}>
          {(context.data?.material_sources ?? chat.files).map((f, i) => (
            <ListRow
              key={f.id + String(i)}
              title={f.filename}
              subtitle={f.source_scope ?? ""}
            />
          ))}
          {!context.data?.material_sources?.length && !chat.files.length ? (
            <Hint>
              {c(
                "发送时会绑定所选资料；原有会话上下文由服务器提供。",
                "Selected sources attach when you send. Existing context comes from your server.",
              )}
            </Hint>
          ) : null}
        </Section>
      </Body>
    </ScrollView>
  );
  const master =
    auth.state.status === "signed-in" ? (
      <ScrollView>
        <Body>
          <Section
            title={c("会话", "Conversations")}
            action={c("管理", "Manage")}
            onAction={() => setSessionsOpen(true)}
          >
            <WorkspaceChip />
            <Button
              title={t("chat.new")}
              variant="outline"
              onPress={() => router.push("/(main)/tutor")}
            />
            <QueryState
              query={sessions}
              empty={!sessions.data?.sessions.length}
            >
              {sessions.data?.sessions
                .filter((s) => !scope.id || s.workspace_id === scope.id)
                .map((s) => (
                  <ListRow
                    key={s.session_id}
                    title={s.title || t("sessions.untitled")}
                    left={
                      <MessageSquareText
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
            </QueryState>
          </Section>
        </Body>
      </ScrollView>
    ) : undefined;
  const content =
    sessionId && session.loadErrorFor === sessionId ? (
      <Body>
        <EmptyState
          title={c("无法打开此会话", "Unable to open this conversation")}
          hint={c(
            "请检查网络或从会话列表重新打开。",
            "Check your connection or reopen it from the conversation list.",
          )}
          actionLabel={t("chat.notfound.back")}
          onAction={() => router.replace("/(main)/tutor")}
        />
      </Body>
    ) : (
      <KeyboardAvoidingView
        style={{ flex: 1, width: "100%", maxWidth: 820, alignSelf: "center" }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <View
          style={{
            flexDirection: "row",
            flexWrap: "wrap",
            paddingHorizontal: 16,
            alignItems: "center",
            gap: 4,
          }}
        >
          <WorkspaceChip />
          <Button
            title={
              sourceCount
                ? c(`${sourceCount} 份来源`, `${sourceCount} sources`)
                : c("选择资料", "Choose sources")
            }
            variant="ghost"
            size="sm"
            leftIcon={<Files size={18} color={theme.colors.accent} />}
            onPress={() => setSourcesOpen(true)}
          />
        </View>
        {auth.state.status === "guest" ? (
          <View style={{ paddingHorizontal: 20, paddingBottom: 8 }}>
            <Hint>
              {c(
                "临时辅导 · 离开账号后不保存学习记录",
                "Temporary tutoring · learning records are not saved",
              )}
            </Hint>
          </View>
        ) : null}
        {!chat.messages.length && !chat.streaming ? (
          <ScrollView
            contentContainerStyle={{ flexGrow: 1 }}
            keyboardShouldPersistTaps="handled"
          >
            <ChatEmpty onPick={send} />
          </ScrollView>
        ) : (
          <View style={{ flex: 1 }}>
            <FlatList
              ref={listRef}
              inverted
              data={data}
              renderItem={render}
              keyExtractor={(item) =>
                item.kind === "streaming"
                  ? "__streaming__"
                  : (chat.messages[item.index]?.message_id ??
                    `message-${item.index}`)
              }
              contentContainerStyle={{
                paddingHorizontal: 16,
                paddingVertical: 8,
              }}
              onScroll={(e) => setShowDown(e.nativeEvent.contentOffset.y > 120)}
              scrollEventThrottle={80}
              keyboardDismissMode="interactive"
              keyboardShouldPersistTaps="handled"
              initialNumToRender={12}
              maxToRenderPerBatch={8}
              windowSize={9}
              ListFooterComponent={
                session.earlierCount > 0 ? (
                  <Button
                    title={c("加载更早的消息", "Load earlier messages")}
                    variant="ghost"
                    loading={session.loadingEarlier}
                    onPress={() => void session.loadEarlier()}
                  />
                ) : null
              }
            />
            {showDown ? (
              <View
                style={{ position: "absolute", bottom: 8, alignSelf: "center" }}
              >
                <IconButton
                  icon={<ArrowDown size={20} color={theme.colors.accent} />}
                  accessibilityLabel={t("chat.scroll.bottom")}
                  onPress={() =>
                    listRef.current?.scrollToOffset({
                      offset: 0,
                      animated: !reduced,
                    })
                  }
                />
              </View>
            ) : null}
          </View>
        )}
        <Composer
          onSend={send}
          disabled={
            chat.streaming ||
            (scope.textbookIds.length > 0 &&
              auth.state.status === "signed-in" &&
              (books.isPending || books.isError))
          }
          onStop={chat.streaming ? stream.stop : undefined}
          prefill={first(params.q)}
          workspaceId={session.workspaceId}
          draft={draft}
        />
      </KeyboardAvoidingView>
    );
  return (
    <View
      style={{
        flex: 1,
        paddingTop: insets.top,
        paddingLeft: insets.left,
        paddingRight: insets.right,
        backgroundColor: theme.colors.bg,
      }}
    >
      <ScreenHeader
        title={
          chat.sessions.find((s) => s.session_id === sessionId)?.title ||
          t("chat.title")
        }
        left={
          <IconButton
            icon={<Rows3 size={21} color={theme.colors.fg} />}
            onPress={() => setSessionsOpen(true)}
            accessibilityLabel={t("chat.sessions")}
          />
        }
        right={
          <IconButton
            icon={<CirclePlus size={21} color={theme.colors.fg} />}
            onPress={() => router.push("/(main)/tutor")}
            accessibilityLabel={t("chat.new")}
          />
        }
      />
      <CapabilityGate name="chat">
        <AdaptivePane
          master={master}
          masterWidth={adaptive.width < 1200 ? 260 : 280}
          inspector={sourcePanel}
          inspectorWidth={320}
        >
          {content}
        </AdaptivePane>
      </CapabilityGate>
      <SessionsSheet
        open={sessionsOpen && auth.state.status === "signed-in"}
        onClose={() => setSessionsOpen(false)}
        currentSessionId={sessionId}
      />
      <SourcePicker open={sourcesOpen} onClose={() => setSourcesOpen(false)} />
    </View>
  );
}
