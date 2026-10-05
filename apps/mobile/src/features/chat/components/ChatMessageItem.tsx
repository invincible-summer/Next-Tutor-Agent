import { MessageMenu } from "./MessageMenu";
import { useChatUi } from "@/stores/chat-ui";
import { SpeechButton } from "@/features/voice/SpeechButton";
import { Button } from "@/ui/Button";
import { useCopy } from "@/lib/copy";
import React, { memo, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import * as Clipboard from "expo-clipboard";
import { Check, Copy, FileText, RefreshCw } from "lucide-react-native";

import { useTheme } from "@/ui/ThemeProvider";
import { alpha } from "@/ui/theme";
import { RichContentRenderer } from "@/ui/rich-content";
import { useI18n } from "@/providers/I18nProvider";
import type { ChatMessage } from "../model/types";
import { ThinkingBlock } from "./ThinkingBlock";
import { ToolCallCard } from "./ToolCallCard";

/** 附件卡片：文件类型图标 + 文件名 + 字数（对齐 Web AttachmentCard）。 */
function AttachmentCard({
  filename,
  charCount,
}: {
  filename: string;
  charCount?: number;
}) {
  const { theme } = useTheme();
  const { t } = useI18n();
  return (
    <View
      style={[
        styles.attachment,
        {
          borderColor: theme.colors.border,
          backgroundColor: theme.colors.surface,
        },
        theme.shadow.sm,
      ]}
    >
      <View
        style={[
          styles.attachmentIcon,
          { backgroundColor: theme.colors["accent-soft"] },
        ]}
      >
        <FileText size={12} color={theme.colors["accent-strong"]} />
      </View>
      <View style={{ minWidth: 0 }}>
        <Text
          style={[
            theme.type.caption,
            {
              color: theme.colors["fg-secondary"],
              fontWeight: "500",
              fontSize: 11,
            },
          ]}
          numberOfLines={1}
        >
          {filename}
        </Text>
        {charCount ? (
          <Text
            style={[
              theme.type.caption,
              { color: alpha(theme.colors.muted, 0.7), fontSize: 10 },
            ]}
          >
            {charCount} {t("unit.chars")}
          </Text>
        ) : null}
      </View>
    </View>
  );
}

/** AI 消息操作行：复制 + 重新生成（对齐 Web hover actions，移动端常驻小字）。 */
function AssistantActions({
  msg,
  onRegenerate,
  disabled,
}: {
  msg: ChatMessage;
  onRegenerate?: (() => void) | undefined;
  disabled?: boolean | undefined;
}) {
  const { theme } = useTheme();
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    await Clipboard.setStringAsync(msg.content).catch(() => undefined);
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };

  return (
    <View style={styles.actions}>
      <Pressable
        onPress={() => void copy()}
        accessibilityRole="button"
        accessibilityLabel={copied ? t("msg.copied") : t("msg.copy")}
        style={({ pressed }) => [
          styles.actionBtn,
          { opacity: pressed ? 0.6 : 1 },
        ]}
        hitSlop={6}
      >
        {copied ? (
          <Check size={12} color={theme.colors.success} />
        ) : (
          <Copy size={12} color={theme.colors.muted} />
        )}
        <Text
          style={[
            theme.type.caption,
            {
              color: copied ? theme.colors.success : theme.colors.muted,
              marginLeft: 4,
              fontSize: 11,
            },
          ]}
        >
          {copied ? t("msg.copied") : t("msg.copy")}
        </Text>
      </Pressable>
      {onRegenerate ? (
        <Pressable
          onPress={onRegenerate}
          disabled={disabled}
          accessibilityRole="button"
          accessibilityLabel={t("chat.regenerate")}
          style={({ pressed }) => [
            styles.actionBtn,
            { opacity: disabled ? 0.4 : pressed ? 0.6 : 1 },
          ]}
          hitSlop={6}
        >
          <RefreshCw size={12} color={theme.colors.muted} />
          <Text
            style={[
              theme.type.caption,
              { color: theme.colors.muted, marginLeft: 4, fontSize: 11 },
            ]}
          >
            {t("chat.regenerate")}
          </Text>
        </Pressable>
      ) : null}
    </View>
  );
}

interface ChatMessageItemProps {
  msg: ChatMessage;
  /** 会话 id（题卡提交归属 source_session_ref）。 */
  sessionId?: string | null | undefined;
  onRegenerate?: (() => void) | undefined;
  disabled?: boolean | undefined;
}

/**
 * 消息行：用户 = 右对齐 accent-soft 气泡 + 右下小方角；
 * AI = 无气泡 + 左侧 accent/35 竖线（辅助阅读的细竖线）。
 * memo：流式期间整页重渲染，历史消息凭稳定引用跳过重渲染。
 */
function ChatMessageItemImpl({
  msg,
  sessionId,
  onRegenerate,
  disabled,
}: ChatMessageItemProps) {
  const { theme } = useTheme();
  const isUser = msg.role === "user";
  const [menu, setMenu] = useState(false);
  const c = useCopy();

  if (isUser) {
    return (
      <View style={styles.userRow}>
        <MessageMenu
          text={msg.content}
          open={menu}
          onClose={() => setMenu(false)}
          onQuote={() => useChatUi.getState().setQuote(msg.content)}
        />
        <View style={styles.userCol}>
          <Pressable
            onLongPress={() => setMenu(true)}
            accessibilityLabel={c(
              "消息，长按打开操作",
              "Message. Long press for actions",
            )}
            style={[
              styles.userBubble,
              { backgroundColor: theme.colors["accent-soft"] },
              theme.shadow.sm,
            ]}
          >
            <Text
              style={[
                theme.type.body,
                { color: theme.colors.fg, fontSize: 16, lineHeight: 24 },
              ]}
            >
              {msg.content}
            </Text>
          </Pressable>
          {msg.attachments && msg.attachments.length > 0 ? (
            <View style={styles.attachmentRow}>
              {msg.attachments.map((a, i) => (
                <AttachmentCard
                  key={`${a.id}-${i}`}
                  filename={a.filename}
                  charCount={a.char_count}
                />
              ))}
            </View>
          ) : null}
        </View>
      </View>
    );
  }

  return (
    <View style={styles.assistantRow}>
      <MessageMenu
        text={msg.content}
        open={menu}
        onClose={() => setMenu(false)}
        onQuote={() => useChatUi.getState().setQuote(msg.content)}
      />
      <View
        style={[
          styles.assistantRail,
          { borderLeftColor: alpha(theme.colors.accent, 0.35) },
        ]}
      >
        {msg.thinking ? <ThinkingBlock text={msg.thinking} /> : null}
        <Pressable
          onLongPress={() => setMenu(true)}
          style={{ paddingVertical: 2 }}
        >
          <RichContentRenderer text={msg.content} />
          {msg.content ? (
            <View
              style={{
                flexDirection: "row",
                alignItems: "center",
                flexWrap: "wrap",
              }}
            >
              <AssistantActions
                msg={msg}
                onRegenerate={onRegenerate}
                disabled={disabled}
              />
              <SpeechButton text={msg.content} />
              <Button
                title={c("更多", "More")}
                variant="ghost"
                size="sm"
                onPress={() => setMenu(true)}
              />
            </View>
          ) : null}
        </Pressable>
        {msg.toolCalls && msg.toolCalls.length > 0 ? (
          <View style={styles.toolCalls}>
            {msg.toolCalls.map((tc, i) => (
              <ToolCallCard
                key={`${tc.name}-${i}`}
                name={tc.name}
                result={tc.result}
                sessionId={sessionId ?? undefined}
              />
            ))}
          </View>
        ) : null}
      </View>
    </View>
  );
}

export const ChatMessageItem = memo(ChatMessageItemImpl);

const styles = StyleSheet.create({
  userRow: {
    flexDirection: "row",
    justifyContent: "flex-end",
    paddingHorizontal: 4,
    paddingVertical: 10,
  },
  userCol: { maxWidth: "82%", alignItems: "flex-end" },
  userBubble: {
    borderRadius: 12,
    borderBottomRightRadius: 4,
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  attachmentRow: {
    marginTop: 6,
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "flex-end",
    gap: 6,
  },
  attachment: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 6,
  },
  attachmentIcon: {
    width: 24,
    height: 24,
    borderRadius: 5,
    alignItems: "center",
    justifyContent: "center",
  },
  assistantRow: { paddingHorizontal: 4, paddingVertical: 12 },
  assistantRail: { borderLeftWidth: 2, paddingLeft: 14 },
  toolCalls: { marginTop: 10, gap: 8 },
  actions: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    marginTop: 6,
  },
  actionBtn: { flexDirection: "row", alignItems: "center", minHeight: 48 },
});
