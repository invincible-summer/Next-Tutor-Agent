import React, { useEffect, useRef, useState } from "react";
import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import {
  BookOpen,
  ChevronDown,
  FileText,
  ScanSearch,
  Clock3,
  Wrench,
  type LucideIcon,
} from "lucide-react-native";

import { useTheme } from "@/ui/ThemeProvider";
import { alpha } from "@/ui/theme";
import { useI18n } from "@/providers/I18nProvider";
import type { QuizQuestion, ToolResultData } from "../model/types";
import { QuizQuestionCard } from "./QuizQuestionCard";

const TOOL_ICONS: Record<string, LucideIcon> = {
  knowledge_search: BookOpen,
  generate_quiz: FileText,
  fit_quiz: ScanSearch,
  recall_history: Clock3,
};

type ToolStatus = "running" | "success" | "partial" | "error";

/** 状态点：running 脉冲 / success 绿 / partial 黄 / error 红（对齐 Web）。 */
export function StatusDot({ status }: { status: ToolStatus }) {
  const { theme } = useTheme();
  const pulse = useRef(new Animated.Value(0.4)).current;

  useEffect(() => {
    if (status !== "running") return;
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, {
          toValue: 1,
          duration: 700,
          useNativeDriver: true,
        }),
        Animated.timing(pulse, {
          toValue: 0.4,
          duration: 700,
          useNativeDriver: true,
        }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [status, pulse]);

  if (status === "running") {
    return (
      <Animated.View
        style={[
          styles.dot,
          { backgroundColor: theme.colors.accent, opacity: pulse },
        ]}
      />
    );
  }
  const color =
    status === "success"
      ? theme.colors.success
      : status === "partial"
        ? theme.colors.warning
        : theme.colors.danger;
  return <View style={[styles.dot, { backgroundColor: color }]} />;
}

interface KnowledgeItem {
  chunk_id?: string;
  filename?: string;
  source?: string;
  printed_page?: number | string;
  page?: number | string;
  chapter?: string;
  section?: string;
  block_type?: string;
  confidence?: number;
  evidence_excerpt?: string;
  text?: string;
}

function KnowledgeSources({
  items,
  omittedCount,
}: {
  items: KnowledgeItem[];
  omittedCount: number;
}) {
  const { theme } = useTheme();
  const { t } = useI18n();
  return (
    <View style={{ gap: 8 }}>
      {/* 命中来源汇总条 */}
      <View
        style={[
          styles.sourcesSummary,
          {
            borderColor: alpha(theme.colors.accent, 0.2),
            backgroundColor: alpha(theme.colors["accent-soft"], 0.2),
          },
        ]}
      >
        <Text
          style={[
            theme.type.caption,
            { color: theme.colors["accent-strong"], fontWeight: "600" },
          ]}
        >
          {t("tool.knowledge.sources")}
          {omittedCount > 0 ? (
            <Text style={{ color: theme.colors.muted, fontWeight: "400" }}>
              {` · ${t("tool.knowledge.filtered")} ${omittedCount} ${t("tool.knowledge.items")}`}
            </Text>
          ) : null}
        </Text>
        <View style={{ marginTop: 4, gap: 2 }}>
          {items.map((item, i) => {
            const filename = String(
              item.filename || item.source || t("tool.knowledge.resource"),
            );
            const page = item.printed_page
              ? t("tool.knowledge.textbookPage").replace(
                  "%n",
                  String(item.printed_page),
                )
              : item.page
                ? t("tool.knowledge.pdfPage").replace("%n", String(item.page))
                : t("tool.knowledge.unpaged");
            const chapter = String(item.chapter ?? "").trim();
            const section = String(item.section ?? "").trim();
            const location = [chapter, section !== chapter ? section : ""]
              .filter(Boolean)
              .join(" · ");
            const blockBadge =
              item.block_type === "figure"
                ? ` · [${t("tool.knowledge.figure")}]`
                : item.block_type === "table"
                  ? ` · [${t("tool.knowledge.table")}]`
                  : "";
            return (
              <Text
                key={String(item.chunk_id ?? i)}
                style={[
                  theme.type.caption,
                  { color: theme.colors["fg-secondary"], lineHeight: 18 },
                ]}
                numberOfLines={2}
              >
                <Text style={{ color: theme.colors.fg, fontWeight: "500" }}>
                  {filename}
                </Text>
                <Text style={{ color: theme.colors.muted }}>
                  {` · ${page}${blockBadge}${location ? ` · ${location}` : ""}`}
                </Text>
                {item.confidence !== undefined && item.confidence !== null ? (
                  <Text style={{ color: theme.colors.muted }}>
                    {` · ${Number(item.confidence) >= 0.65 ? t("tool.knowledge.conf.high") : t("tool.knowledge.conf.medium")}`}
                  </Text>
                ) : null}
              </Text>
            );
          })}
        </View>
      </View>
      {/* 摘录卡 */}
      {items.map((item, i) => (
        <View
          key={`excerpt-${String(item.chunk_id ?? i)}`}
          style={[
            styles.excerpt,
            { borderColor: theme.colors["border-light"] },
          ]}
        >
          <Text
            style={[
              theme.type.caption,
              { color: theme.colors.muted, marginBottom: 3 },
            ]}
            numberOfLines={1}
          >
            {String(
              item.filename || item.source || t("tool.knowledge.resource"),
            )}
            {item.printed_page
              ? ` · ${t("tool.knowledge.textbookPage").replace("%n", String(item.printed_page))}`
              : item.page
                ? ` · ${t("tool.knowledge.pdfPage").replace("%n", String(item.page))}`
                : ""}
          </Text>
          <Text
            style={[
              theme.type.caption,
              { color: theme.colors["fg-secondary"], lineHeight: 18 },
            ]}
          >
            {String(item.evidence_excerpt || item.text || "")}
          </Text>
        </View>
      ))}
    </View>
  );
}

/** 教具卡片：折叠式工具调用卡（头部 icon + 名称 + 状态点，展开看结果）。 */
export function ToolCallCard({
  name,
  result,
  sessionId,
}: {
  name: string;
  result?: unknown;
  sessionId?: string | undefined;
}) {
  const { theme } = useTheme();
  const { t } = useI18n();
  const r = result as ToolResultData | undefined;
  // generate_quiz 成功时自动展开：交互题卡是主界面（避免老师再抄一遍题）。
  const questions = (r?.data?.questions as QuizQuestion[] | undefined) ?? [];
  const autoOpen =
    name === "generate_quiz" && r?.status !== "error" && questions.length > 0;
  const [open, setOpen] = useState(autoOpen);

  const Icon = TOOL_ICONS[name] ?? Wrench;
  const status: ToolStatus = !r
    ? "running"
    : r.status === "error"
      ? "error"
      : r.status === "partial"
        ? "partial"
        : "success";
  const isError = status === "error";
  const knowledgeResults =
    name === "knowledge_search" && Array.isArray(r?.data?.results)
      ? (r.data.results as KnowledgeItem[])
      : [];
  const omittedCount = Number(r?.data?.omitted_count ?? 0);

  return (
    <View
      style={[
        styles.card,
        {
          borderColor: isError
            ? alpha(theme.colors.danger, 0.3)
            : theme.colors.border,
          backgroundColor: isError
            ? alpha(theme.colors.danger, 0.05)
            : theme.colors.surface,
        },
        theme.shadow.sm,
      ]}
    >
      <Pressable
        onPress={() => setOpen((v) => !v)}
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        accessibilityLabel={t(`tool.${name}`, name)}
        style={({ pressed }) => [
          styles.header,
          {
            backgroundColor: pressed
              ? theme.colors["surface-hover"]
              : "transparent",
          },
        ]}
      >
        <View
          style={[
            styles.iconWrap,
            {
              backgroundColor: isError
                ? alpha(theme.colors.danger, 0.1)
                : theme.colors["accent-soft"],
            },
          ]}
        >
          <Icon
            size={13}
            color={
              isError ? theme.colors.danger : theme.colors["accent-strong"]
            }
          />
        </View>
        <Text
          style={[
            theme.type.label,
            { color: theme.colors["fg-secondary"], fontSize: 13 },
          ]}
        >
          {t(`tool.${name}`, name)}
        </Text>
        <View style={styles.statusWrap}>
          <StatusDot status={status} />
          <Text
            style={[
              theme.type.caption,
              { color: theme.colors.muted, marginLeft: 5 },
            ]}
          >
            {!r
              ? t("tool.running")
              : isError
                ? t("tool.failed")
                : t("tool.done")}
          </Text>
        </View>
        {questions.length > 0 ? (
          <Text style={[theme.type.caption, { color: theme.colors.muted }]}>
            · {questions.length} {t("quiz.questions.unit")}
          </Text>
        ) : null}
        <ChevronDown
          size={13}
          color={theme.colors.muted}
          style={[
            styles.chevron,
            { transform: [{ rotate: open ? "0deg" : "-90deg" }] },
          ]}
        />
      </Pressable>
      {open ? (
        <View
          style={[
            styles.body,
            { borderTopColor: theme.colors["border-light"] },
          ]}
        >
          {isError ? (
            <Text
              style={[
                theme.type.caption,
                { color: theme.colors.danger, lineHeight: 18 },
              ]}
            >
              {r?.error?.message || r?.text || t("tool.failed")}
            </Text>
          ) : questions.length > 0 ? (
            <View style={{ gap: 10 }}>
              {questions.map((q, i) => (
                <QuizQuestionCard
                  key={q.question_id ?? i}
                  question={q}
                  index={i}
                  sessionId={sessionId}
                />
              ))}
            </View>
          ) : knowledgeResults.length > 0 ? (
            <KnowledgeSources
              items={knowledgeResults}
              omittedCount={omittedCount}
            />
          ) : (
            <Text
              style={[
                theme.type.code,
                { color: theme.colors.muted, fontSize: 12 },
              ]}
              numberOfLines={12}
            >
              {r?.text || JSON.stringify(r?.data, null, 2)}
            </Text>
          )}
        </View>
      ) : null}
    </View>
  );
}

/** 流式进行中工具卡：状态点脉冲 + 最新进度 + 心跳计时。 */
export function ActiveToolCard({
  name,
  progress,
  heartbeatElapsed,
}: {
  name: string;
  progress: string[];
  heartbeatElapsed: number;
}) {
  const { theme } = useTheme();
  const { t } = useI18n();
  const Icon = TOOL_ICONS[name] ?? Wrench;
  const latest =
    progress.length > 0 ? progress[progress.length - 1] : undefined;

  return (
    <View
      style={[
        styles.activeCard,
        {
          borderColor: theme.colors.border,
          backgroundColor: theme.colors.surface,
        },
        theme.shadow.sm,
      ]}
    >
      <View
        style={[
          styles.iconWrap,
          { backgroundColor: theme.colors["accent-soft"] },
        ]}
      >
        <Icon size={13} color={theme.colors["accent-strong"]} />
      </View>
      <Text
        style={[
          theme.type.label,
          { color: theme.colors["fg-secondary"], fontSize: 13 },
        ]}
      >
        {t(`tool.${name}`, name)}
      </Text>
      <StatusDot status="running" />
      <Text
        style={[
          theme.type.caption,
          { color: theme.colors.muted, flex: 1, marginLeft: 4 },
        ]}
        numberOfLines={1}
      >
        {latest ?? `${t("tool.running")}…`}
      </Text>
      {heartbeatElapsed > 0 ? (
        <Text
          style={[
            theme.type.caption,
            { color: alpha(theme.colors.muted, 0.5) },
          ]}
        >
          {heartbeatElapsed}s
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: 1, borderRadius: 10, overflow: "hidden" },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    minHeight: 44,
  },
  iconWrap: {
    width: 24,
    height: 24,
    borderRadius: 6,
    alignItems: "center",
    justifyContent: "center",
  },
  statusWrap: { flexDirection: "row", alignItems: "center" },
  chevron: { marginLeft: "auto" },
  body: {
    borderTopWidth: 1,
    paddingHorizontal: 12,
    paddingTop: 10,
    paddingBottom: 12,
  },
  dot: { width: 8, height: 8, borderRadius: 4 },
  sourcesSummary: {
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
  },
  excerpt: {
    borderWidth: 1,
    borderRadius: 7,
    paddingHorizontal: 10,
    paddingVertical: 8,
  },
  activeCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
    marginVertical: 6,
    minHeight: 44,
  },
});
