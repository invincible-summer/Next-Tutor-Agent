import React, { useEffect, useRef } from "react";
import {
  AccessibilityInfo,
  Animated,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { RotateCw } from "lucide-react-native";

import { useReducedMotion } from "@/ui/useReducedMotion";
import { useTheme } from "@/ui/ThemeProvider";
import { alpha } from "@/ui/theme";
import { RichContentRenderer } from "@/ui/rich-content";
import { useI18n } from "@/providers/I18nProvider";
import type { RetryState } from "../model/types";
import { ThinkingBlock } from "./ThinkingBlock";
import { ActiveToolCard, ToolCallCard } from "./ToolCallCard";

const STEP_ORDER = [
  "understanding",
  "planning",
  "thinking",
  "tool_executing",
] as const;

/** 步骤指示：理解 → 规划 → 思考 → 工具执行 的小圆点序列（对齐 Web）。 */
function StepIndicator({
  currentStep,
  heartbeatElapsed,
}: {
  currentStep: string;
  heartbeatElapsed: number;
}) {
  const { theme } = useTheme();
  const { t } = useI18n();
  const currentIdx = STEP_ORDER.indexOf(
    currentStep as (typeof STEP_ORDER)[number],
  );
  return (
    <View
      style={styles.stepRow}
      accessibilityLabel={t(`step.${currentStep}`, currentStep)}
    >
      {STEP_ORDER.map((s, i) => {
        const done = currentIdx > i;
        const active = currentIdx === i;
        return (
          <View key={s} style={styles.stepItem}>
            {i > 0 ? (
              <View
                style={[
                  styles.stepLine,
                  {
                    backgroundColor:
                      done || active
                        ? alpha(theme.colors.accent, 0.5)
                        : theme.colors.border,
                  },
                ]}
              />
            ) : null}
            <StepDot active={active} done={done} />
          </View>
        );
      })}
      <Text
        style={[
          theme.type.caption,
          { color: theme.colors.muted, marginLeft: 6 },
        ]}
      >
        {t(`step.${currentStep}`, currentStep)}…
      </Text>
      {heartbeatElapsed > 0 ? (
        <Text
          style={[
            theme.type.caption,
            { color: alpha(theme.colors.muted, 0.5), fontSize: 11 },
          ]}
        >
          {heartbeatElapsed}s
        </Text>
      ) : null}
    </View>
  );
}

function StepDot({ active, done }: { active: boolean; done: boolean }) {
  const { theme } = useTheme();
  const reduced = useReducedMotion();
  const pulse = useRef(new Animated.Value(1)).current;
  useEffect(() => {
    if (!active || reduced) {
      pulse.setValue(1);
      return;
    }
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, {
          toValue: 0.35,
          duration: 600,
          useNativeDriver: true,
        }),
        Animated.timing(pulse, {
          toValue: 1,
          duration: 600,
          useNativeDriver: true,
        }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [active, pulse, reduced]);
  const color = done || active ? theme.colors.accent : theme.colors.border;
  return (
    <Animated.View
      style={[
        styles.stepDot,
        { backgroundColor: color },
        active ? { opacity: pulse } : null,
      ]}
    />
  );
}

/** 等待首字节的三点弹跳 loader（对齐 Web dot-loader）。 */
function DotLoader() {
  const { theme } = useTheme();
  const values = useRef([0, 1, 2].map(() => new Animated.Value(0))).current;
  useEffect(() => {
    let mounted = true;
    let reduced = false;
    void AccessibilityInfo.isReduceMotionEnabled().then((v) => {
      reduced = v;
    });
    const loops = values.map((v, i) => {
      const anim = Animated.loop(
        Animated.sequence([
          Animated.delay(i * 160),
          Animated.timing(v, {
            toValue: -4,
            duration: 300,
            useNativeDriver: true,
          }),
          Animated.timing(v, {
            toValue: 0,
            duration: 300,
            useNativeDriver: true,
          }),
          Animated.delay(600),
        ]),
      );
      if (mounted && !reduced) anim.start();
      return anim;
    });
    return () => {
      mounted = false;
      loops.forEach((l) => l.stop());
    };
  }, [values]);
  return (
    <View style={styles.dotLoader} accessibilityLabel="loading">
      {values.map((v, i) => (
        <Animated.View
          key={i}
          style={[
            styles.loaderDot,
            {
              backgroundColor: theme.colors.muted,
              transform: [{ translateY: v }],
            },
          ]}
        />
      ))}
    </View>
  );
}

/** 流式光标：1s 步进闪烁的竖条（对齐 Web .streaming-cursor）。 */
export function StreamingCursor() {
  const { theme } = useTheme();
  const opacity = useRef(new Animated.Value(1)).current;
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, {
          toValue: 0,
          duration: 500,
          useNativeDriver: false,
        }),
        Animated.timing(opacity, {
          toValue: 1,
          duration: 500,
          useNativeDriver: false,
        }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [opacity]);
  return (
    <Animated.View
      style={[styles.cursor, { backgroundColor: theme.colors.accent, opacity }]}
    />
  );
}

export interface StreamingMessageProps {
  thinking: string;
  answer: string;
  activeTool: string | null;
  toolProgress: string[];
  toolCalls: { name: string; result?: unknown }[];
  currentStep: string | null;
  heartbeatElapsed: number;
  retry: RetryState | null;
  sessionId?: string | null;
}

/** 流式消息：AI 侧无气泡 + accent 竖线，内部按 Web 顺序排布各态。 */
export function StreamingMessage({
  thinking,
  answer,
  activeTool,
  toolProgress,
  toolCalls,
  currentStep,
  heartbeatElapsed,
  retry,
  sessionId,
}: StreamingMessageProps) {
  const { theme } = useTheme();
  const { t } = useI18n();
  const hasContent =
    thinking || answer || activeTool || toolCalls.length > 0 || currentStep;

  return (
    <View style={styles.row}>
      <View
        style={[
          styles.rail,
          { borderLeftColor: alpha(theme.colors.accent, 0.35) },
        ]}
      >
        {retry && retry.visible ? (
          <View
            style={[
              styles.retryBar,
              {
                backgroundColor: alpha(theme.colors.warning, 0.1),
              },
            ]}
          >
            <RotateCw size={12} color={theme.colors.warning} />
            <Text
              style={[
                theme.type.caption,
                { color: theme.colors.warning, marginLeft: 6, flex: 1 },
              ]}
            >
              {t("chat.retrying")} ·{" "}
              {t("chat.retry.attempt").replace("%n", String(retry.attempt))}:{" "}
              {retry.reason}…
            </Text>
          </View>
        ) : null}
        {currentStep && !answer ? (
          <StepIndicator
            currentStep={currentStep}
            heartbeatElapsed={activeTool ? 0 : heartbeatElapsed}
          />
        ) : null}
        {thinking ? <ThinkingBlock text={thinking} isStreaming /> : null}
        {activeTool ? (
          <ActiveToolCard
            name={activeTool}
            progress={toolProgress}
            heartbeatElapsed={heartbeatElapsed}
          />
        ) : null}
        {answer ? (
          <View style={{ paddingVertical: 2 }}>
            <RichContentRenderer text={answer} streaming />
          </View>
        ) : null}
        {toolCalls.length > 0 ? (
          <View style={styles.toolCalls}>
            {toolCalls.map((tc, i) => (
              <ToolCallCard
                key={`${tc.name}-${i}`}
                name={tc.name}
                result={tc.result}
                sessionId={sessionId ?? undefined}
              />
            ))}
          </View>
        ) : null}
        {!hasContent ? <DotLoader /> : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { paddingHorizontal: 4, paddingVertical: 12 },
  rail: { borderLeftWidth: 2, paddingLeft: 14 },
  stepRow: { flexDirection: "row", alignItems: "center", marginBottom: 8 },
  stepItem: { flexDirection: "row", alignItems: "center" },
  stepLine: { height: 1, width: 12, marginRight: 6 },
  stepDot: { width: 6, height: 6, borderRadius: 3 },
  dotLoader: { flexDirection: "row", gap: 5, paddingVertical: 8 },
  loaderDot: { width: 5, height: 5, borderRadius: 2.5 },
  cursor: { width: 2, height: 16, borderRadius: 1, marginLeft: 1 },
  retryBar: {
    flexDirection: "row",
    alignItems: "center",
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 6,
    marginBottom: 8,
  },
  toolCalls: { marginTop: 10, gap: 8 },
});
