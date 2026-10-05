/**
 * 富文本渲染器：把 parser.ts 的 AST 渲染为纯 RN 视图（聊天 AI 消息的降级渲染路径）。
 * 带数学的内容交给离线 KaTeX 分组排版，其余 Markdown 使用原生组件。
 */
import React, { memo, useEffect, useMemo, useRef, useState } from "react";
import {
  AccessibilityInfo,
  Animated,
  Easing,
  Linking,
  ScrollView,
  StyleSheet,
  Text,
  View,
  type TextStyle,
} from "react-native";

import { MathContent } from "./MathContent";
import { hasMath, safeContentLink } from "./math-document";
import { useTheme } from "../ThemeProvider";
import { alpha, type AppTheme } from "../theme";
import {
  parseRichText,
  type RichBlock,
  type RichInline,
  type RichListItem,
} from "./parser";

export interface RichContentRendererProps {
  text: string;
  streaming?: boolean;
  onLinkPress?: (url: string) => void;
}

interface RenderCtx {
  theme: AppTheme;
  onLinkPress: (url: string) => void;
  /** 正文颜色；引用块内覆盖为 muted。 */
  color: string;
}

/** RN 0.86 未导出 useReducedMotion 钩子，用 AccessibilityInfo 自实现等价物。 */
function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    let alive = true;
    AccessibilityInfo.isReduceMotionEnabled()
      .then((v) => {
        if (alive) setReduced(v);
      })
      .catch(() => undefined);
    const sub = AccessibilityInfo.addEventListener(
      "reduceMotionChanged",
      setReduced,
    );
    return () => {
      alive = false;
      sub.remove();
    };
  }, []);
  return reduced;
}

/** 流式块形光标：1s 周期闪烁（500ms 淡出 + 500ms 淡入）；减弱动态时为静态。 */
function BlinkCursor({ color }: { color: string }) {
  const reduced = useReducedMotion();
  const opacity = useRef(new Animated.Value(1)).current;
  useEffect(() => {
    if (reduced) {
      opacity.setValue(1);
      return;
    }
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, {
          toValue: 0.1,
          duration: 500,
          easing: Easing.linear,
          useNativeDriver: true,
        }),
        Animated.timing(opacity, {
          toValue: 1,
          duration: 500,
          easing: Easing.linear,
          useNativeDriver: true,
        }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [reduced, opacity]);
  return <Animated.Text style={{ opacity, color }}>▍</Animated.Text>;
}

// ---------- 行内渲染 ----------

function renderSpans(
  spans: RichInline[],
  ctx: RenderCtx,
  keyPrefix: string,
): React.ReactNode[] {
  return spans.map((span, idx) => renderSpan(span, ctx, `${keyPrefix}-${idx}`));
}

function renderSpan(
  span: RichInline,
  ctx: RenderCtx,
  key: string,
): React.ReactNode {
  const { theme } = ctx;
  switch (span.type) {
    case "text":
      return <Text key={key}>{span.text}</Text>;
    case "break":
      return <Text key={key}>{"\n"}</Text>;
    case "bold":
      return (
        <Text key={key} style={styles.bold}>
          {renderSpans(span.children, ctx, key)}
        </Text>
      );
    case "italic":
      return (
        <Text key={key} style={styles.italic}>
          {renderSpans(span.children, ctx, key)}
        </Text>
      );
    case "strike":
      return (
        <Text key={key} style={styles.strike}>
          {renderSpans(span.children, ctx, key)}
        </Text>
      );
    case "code":
      return (
        <Text
          key={key}
          style={{
            fontFamily: theme.fonts.mono,
            fontSize: theme.type.code.fontSize ?? 13,
            backgroundColor: theme.colors["accent-soft"],
            paddingHorizontal: 4,
            borderRadius: theme.radius.xs,
          }}
        >
          {span.text}
        </Text>
      );
    case "math":
      // TeX 占位：mono + accent2 着色的行内徽章
      return (
        <Text
          key={key}
          style={{
            fontFamily: theme.fonts.mono,
            fontSize: theme.type.code.fontSize ?? 13,
            color: theme.colors["accent2-strong"],
            backgroundColor: theme.colors["accent2-soft"],
            paddingHorizontal: 3,
            borderRadius: theme.radius.xs,
          }}
        >
          {span.tex}
        </Text>
      );
    case "link":
      return (
        <Text
          key={key}
          style={{ color: theme.colors["accent-strong"] }}
          accessibilityRole="link"
          onPress={() => ctx.onLinkPress(span.url)}
        >
          {renderSpans(span.label, ctx, key)}
        </Text>
      );
  }
}

// ---------- 块级渲染 ----------

function headingStyle(theme: AppTheme, level: 1 | 2 | 3 | 4): TextStyle {
  switch (level) {
    case 1:
      return theme.type.display;
    case 2:
      return theme.type.title;
    case 3:
      return theme.type.titleSmall;
    case 4:
      return { ...theme.type.bodyStrong, fontFamily: theme.fonts.sans };
  }
}

function ListItemView({
  item,
  ctx,
  depth,
  cursor,
}: {
  item: RichListItem;
  ctx: RenderCtx;
  depth: number;
  cursor: boolean;
}) {
  const { theme } = ctx;
  const markerWidth = depth === 0 ? 22 : 18;
  const glyph = item.ordered ? item.marker : depth === 0 ? "•" : "◦";
  return (
    <View style={{ marginBottom: theme.space(1) }}>
      <View style={styles.listRow}>
        <Text
          style={[
            theme.type.body,
            {
              width: markerWidth,
              textAlign: "right",
              color: theme.colors.accent,
            },
          ]}
        >
          {glyph}
        </Text>
        <Text
          style={[theme.type.body, styles.listContent, { color: ctx.color }]}
        >
          {renderSpans(item.content, ctx, "li")}
          {cursor && item.children.length === 0 ? (
            <BlinkCursor color={ctx.color} />
          ) : null}
        </Text>
      </View>
      {item.children.length > 0 ? (
        <View style={{ paddingLeft: markerWidth, marginTop: theme.space(1) }}>
          {item.children.map((child, k) => (
            <ListItemView
              key={k}
              item={child}
              ctx={ctx}
              depth={depth + 1}
              cursor={cursor && k === item.children.length - 1}
            />
          ))}
        </View>
      ) : null}
    </View>
  );
}

function TableRow({
  cells,
  aligns,
  ctx,
  header = false,
  last = false,
  keyPrefix,
}: {
  cells: RichInline[][];
  aligns: readonly ("left" | "center" | "right")[];
  ctx: RenderCtx;
  header?: boolean;
  last?: boolean;
  keyPrefix: string;
}) {
  const { theme } = ctx;
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      style={[
        !last && {
          borderBottomWidth: StyleSheet.hairlineWidth,
          borderBottomColor: theme.colors["border-light"],
        },
        header && { backgroundColor: theme.colors["accent-soft"] },
      ]}
    >
      <View style={styles.tableRowInner}>
        {cells.map((cell, c) => (
          <View
            key={c}
            style={[
              styles.tableCell,
              {
                borderRightColor: theme.colors["border-light"],
                paddingHorizontal: theme.space(2.5),
                paddingVertical: theme.space(1.5),
              },
              c === cells.length - 1 && styles.tableCellLast,
            ]}
          >
            <Text
              style={[
                header ? theme.type.bodyStrong : theme.type.caption,
                { color: theme.colors.fg, textAlign: aligns[c] ?? "left" },
              ]}
            >
              {renderSpans(cell, ctx, `${keyPrefix}-${c}`)}
            </Text>
          </View>
        ))}
      </View>
    </ScrollView>
  );
}

function renderBlock(
  block: RichBlock,
  ctx: RenderCtx,
  keyPrefix: string,
  cursor: boolean,
): React.ReactNode {
  const { theme } = ctx;
  switch (block.type) {
    case "heading":
      return (
        <Text style={[headingStyle(theme, block.level), { color: ctx.color }]}>
          {renderSpans(block.children, ctx, keyPrefix)}
          {cursor ? <BlinkCursor color={ctx.color} /> : null}
        </Text>
      );
    case "paragraph":
      return (
        <Text style={[theme.type.body, { color: ctx.color }]}>
          {renderSpans(block.children, ctx, keyPrefix)}
          {cursor ? <BlinkCursor color={ctx.color} /> : null}
        </Text>
      );
    case "code":
      return (
        <View style={[theme.card, styles.codeCard]}>
          {block.language !== "" ? (
            <View
              style={[
                styles.codeHead,
                {
                  borderBottomColor: theme.colors["border-light"],
                  paddingHorizontal: theme.space(3),
                  paddingVertical: theme.space(1.5),
                },
              ]}
            >
              <Text
                style={[
                  theme.type.caption,
                  { color: theme.colors["fg-tertiary"] },
                ]}
              >
                {block.language}
              </Text>
            </View>
          ) : null}
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            <View style={{ padding: theme.space(3) }}>
              <Text
                selectable
                style={[theme.type.code, { color: theme.colors.fg }]}
              >
                {block.code}
                {cursor ? <BlinkCursor color={theme.colors.fg} /> : null}
              </Text>
            </View>
          </ScrollView>
        </View>
      );
    case "quote":
      // 引用：左 2px accent/35 边条 + muted 文字（对齐 AI 消息 rail 意象）
      return (
        <View
          style={{
            borderLeftWidth: 2,
            borderLeftColor: alpha(theme.colors.accent, 0.35),
            paddingLeft: theme.space(3),
          }}
        >
          {block.blocks.map((inner, k) => (
            <View
              key={k}
              style={
                k < block.blocks.length - 1
                  ? { marginBottom: theme.space(2) }
                  : undefined
              }
            >
              {renderBlock(
                inner,
                { ...ctx, color: theme.colors.muted },
                `${keyPrefix}-q${k}`,
                cursor && k === block.blocks.length - 1,
              )}
            </View>
          ))}
        </View>
      );
    case "hr":
      return (
        <View>
          <View
            style={{
              height: 1,
              backgroundColor: theme.colors["border-light"],
              marginVertical: theme.space(1),
            }}
          />
          {cursor ? (
            <Text style={[theme.type.body, { color: ctx.color }]}>
              <BlinkCursor color={ctx.color} />
            </Text>
          ) : null}
        </View>
      );
    case "list":
      return (
        <View>
          {block.items.map((item, k) => (
            <ListItemView
              key={k}
              item={item}
              ctx={ctx}
              depth={0}
              cursor={cursor && k === block.items.length - 1}
            />
          ))}
        </View>
      );
    case "table":
      return (
        <View>
          <View
            style={{
              borderWidth: 1,
              borderColor: theme.colors.border,
              borderRadius: theme.radius.md,
              overflow: "hidden",
            }}
          >
            <TableRow
              cells={block.header}
              aligns={block.align}
              ctx={ctx}
              header
              keyPrefix={`${keyPrefix}-th`}
              last={block.rows.length === 0}
            />
            {block.rows.map((row, r) => (
              <TableRow
                key={r}
                cells={row}
                aligns={block.align}
                ctx={ctx}
                keyPrefix={`${keyPrefix}-tr${r}`}
                last={r === block.rows.length - 1}
              />
            ))}
          </View>
          {cursor ? (
            <Text style={[theme.type.body, { color: ctx.color }]}>
              <BlinkCursor color={ctx.color} />
            </Text>
          ) : null}
        </View>
      );
    case "math":
      // 数学的可访问文字后备显示
      return (
        <View>
          <View
            style={{
              alignItems: "center",
              justifyContent: "center",
              backgroundColor: theme.colors.surface,
              borderWidth: 1,
              borderStyle: "dashed",
              borderColor: alpha(theme.colors.accent, 0.4),
              borderRadius: theme.radius.lg,
              paddingVertical: theme.space(3),
              paddingHorizontal: theme.space(4),
            }}
          >
            <Text
              selectable
              style={[
                theme.type.code,
                { color: theme.colors["fg-secondary"], textAlign: "center" },
              ]}
            >
              {block.tex}
            </Text>
          </View>
          {cursor ? (
            <Text style={[theme.type.body, { color: ctx.color }]}>
              <BlinkCursor color={ctx.color} />
            </Text>
          ) : null}
        </View>
      );
  }
}

// ---------- 入口 ----------

export const RichContentRenderer = memo(function RichContentRenderer({
  text,
  streaming = false,
  onLinkPress,
}: RichContentRendererProps) {
  const { theme } = useTheme();
  const blocks = useMemo(() => parseRichText(text), [text]);
  const ctx = useMemo<RenderCtx>(
    () => ({
      theme,
      color: theme.colors.fg,
      onLinkPress:
        onLinkPress ??
        ((url: string) => {
          if (safeContentLink(url))
            void Linking.openURL(url).catch(() => undefined);
        }),
    }),
    [theme, onLinkPress],
  );
  if (hasMath(blocks))
    return (
      <MathContent
        blocks={blocks}
        text={text}
        streaming={streaming}
        onLinkPress={ctx.onLinkPress}
      />
    );
  const lastIdx = blocks.length - 1;
  return (
    <View>
      {blocks.map((block, i) => (
        <View
          key={i}
          style={[
            i < lastIdx ? { marginBottom: theme.space(2) } : null,
            i > 0 && block.type === "heading"
              ? { marginTop: theme.space(1) }
              : null,
          ]}
        >
          {renderBlock(block, ctx, `b${i}`, streaming && i === lastIdx)}
        </View>
      ))}
      {streaming && blocks.length === 0 ? (
        <Text style={[theme.type.body, { color: theme.colors.fg }]}>
          <BlinkCursor color={theme.colors.fg} />
        </Text>
      ) : null}
    </View>
  );
});

const styles = StyleSheet.create({
  bold: { fontWeight: "700" },
  italic: { fontStyle: "italic" },
  strike: { textDecorationLine: "line-through" },
  listRow: { flexDirection: "row" },
  listContent: { flex: 1, paddingLeft: 6 },
  codeCard: { overflow: "hidden" },
  codeHead: { borderBottomWidth: StyleSheet.hairlineWidth },
  tableRowInner: { flexDirection: "row" },
  tableCell: {
    minWidth: 88,
    maxWidth: 240,
    borderRightWidth: StyleSheet.hairlineWidth,
  },
  tableCellLast: { borderRightWidth: 0 },
});
