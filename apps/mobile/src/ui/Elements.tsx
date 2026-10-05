import React, { useState } from "react";
import {
  ActivityIndicator,
  ScrollView,
  Text,
  View,
  type StyleProp,
  type ViewStyle,
} from "react-native";
import {
  ArrowLeft,
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  X,
} from "lucide-react-native";
import { useRouter } from "expo-router";
import { useCopy } from "@/lib/copy";
import { errorMessage } from "@/lib/feedback";
import { useTheme } from "./ThemeProvider";
import { Card } from "./Card";
import { Button, IconButton } from "./Button";
import { EmptyState, ErrorState } from "./EmptyState";
import { TextField } from "./Input";
import { ScreenHeader } from "./ScreenHeader";
export function Body({
  children,
  style,
}: {
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
}) {
  return (
    <View
      style={[
        {
          padding: 20,
          paddingTop: 8,
          gap: 24,
          width: "100%",
          maxWidth: 1280,
          alignSelf: "center",
        },
        style,
      ]}
    >
      {children}
    </View>
  );
}
export function Section({
  title,
  caption,
  action,
  onAction,
  children,
}: {
  title: string;
  caption?: string;
  action?: string;
  onAction?: () => void;
  children: React.ReactNode;
}) {
  const { theme } = useTheme();
  return (
    <View style={{ gap: 12 }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <View style={{ flex: 1 }}>
          <Text
            accessibilityRole="header"
            style={[theme.type.titleSmall, { color: theme.colors.fg }]}
          >
            {title}
          </Text>
          {caption ? (
            <Text
              style={[
                theme.type.caption,
                { color: theme.colors.muted, marginTop: 4 },
              ]}
            >
              {caption}
            </Text>
          ) : null}
        </View>
        {action && onAction ? (
          <Button title={action} variant="ghost" size="sm" onPress={onAction} />
        ) : null}
      </View>
      {children}
    </View>
  );
}
export function Label({
  children,
  muted = false,
}: {
  children: React.ReactNode;
  muted?: boolean;
}) {
  const { theme } = useTheme();
  return (
    <Text
      style={[
        theme.type.body,
        { color: muted ? theme.colors.muted : theme.colors.fg },
      ]}
    >
      {children}
    </Text>
  );
}
export function Hint({ children }: { children: React.ReactNode }) {
  const { theme } = useTheme();
  return (
    <Text
      style={[
        theme.type.caption,
        {
          color: theme.colors.muted,
          lineHeight: Number(theme.type.caption.lineHeight ?? 19),
        },
      ]}
    >
      {children}
    </Text>
  );
}
export function Tile({
  title,
  subtitle,
  icon,
  onPress,
  style,
}: {
  title: string;
  subtitle: string;
  icon: React.ReactNode;
  onPress: () => void;
  style?: StyleProp<ViewStyle>;
}) {
  const { theme } = useTheme();
  return (
    <Card
      onPress={onPress}
      accessibilityLabel={title}
      style={[{ gap: 12, padding: 20 }, style]}
    >
      <View
        style={{
          flexDirection: "row",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <View
          style={{
            width: 44,
            height: 44,
            borderRadius: 14,
            backgroundColor: theme.colors["accent-soft"],
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          {icon}
        </View>
        <ArrowRight size={18} color={theme.colors.muted} />
      </View>
      <Text style={[theme.type.bodyStrong, { color: theme.colors.fg }]}>
        {title}
      </Text>
      <Hint>{subtitle}</Hint>
    </Card>
  );
}
export function BackButton() {
  const router = useRouter();
  const c = useCopy();
  const { theme } = useTheme();
  return (
    <IconButton
      icon={<ArrowLeft size={22} color={theme.colors.fg} />}
      accessibilityLabel={c("返回", "Back")}
      onPress={() => (router.canGoBack() ? router.back() : router.replace("/"))}
    />
  );
}
export function SheetHeader({
  title,
  onClose,
}: {
  title: string;
  onClose: () => void;
}) {
  const c = useCopy();
  const { theme } = useTheme();
  return (
    <ScreenHeader
      assistant={false}
      title={title}
      right={
        <IconButton
          icon={<X size={22} color={theme.colors.fg} />}
          onPress={onClose}
          accessibilityLabel={c("关闭", "Close")}
        />
      }
    />
  );
}
export function QueryState({
  query,
  empty,
  children,
}: {
  query: {
    isPending: boolean;
    isError: boolean;
    error: unknown;
    refetch: () => unknown;
  };
  empty?: boolean;
  children: React.ReactNode;
}) {
  const c = useCopy();
  const { theme } = useTheme();
  if (query.isPending)
    return (
      <View
        accessibilityLabel={c("加载中", "Loading")}
        style={{ padding: 32, alignItems: "center" }}
      >
        <ActivityIndicator color={theme.colors.accent} />
      </View>
    );
  if (query.isError)
    return (
      <ErrorState
        title={errorMessage(query.error, c)}
        retryLabel={c("重试", "Retry")}
        onRetry={() => void query.refetch()}
      />
    );
  if (empty)
    return (
      <EmptyState
        title={c("还没有内容", "Nothing here yet")}
        hint={c(
          "添加内容后，就可以从这里继续。",
          "Add something to get started.",
        )}
      />
    );
  return <>{children}</>;
}
export function Pager({
  page,
  total,
  pageSize = 10,
  onChange,
}: {
  page: number;
  total: number;
  pageSize?: number;
  onChange: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.max(1, Math.min(page, pages));
  const [input, setInput] = useState("");
  const c = useCopy();
  const { theme } = useTheme();
  if (pages <= 1) return null;
  const submit = () => {
    onChange(Math.max(1, Math.min(pages, Number(input) || current)));
    setInput("");
  };
  return (
    <View
      style={{
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
      }}
    >
      <IconButton
        icon={<ChevronLeft size={20} color={theme.colors.fg} />}
        disabled={current === 1}
        onPress={() => onChange(current - 1)}
        accessibilityLabel={c("上一页", "Previous page")}
      />
      <TextField
        accessibilityLabel={c("页码", "Page number")}
        value={input || String(current)}
        keyboardType="number-pad"
        onChangeText={setInput}
        onBlur={submit}
        onSubmitEditing={submit}
        style={{ minWidth: 60, textAlign: "center" }}
      />
      <Hint>/ {pages}</Hint>
      <IconButton
        icon={<ChevronRight size={20} color={theme.colors.fg} />}
        disabled={current === pages}
        onPress={() => onChange(current + 1)}
        accessibilityLabel={c("下一页", "Next page")}
      />
    </View>
  );
}
export function SheetBody({ children }: { children: React.ReactNode }) {
  return (
    <ScrollView
      keyboardShouldPersistTaps="handled"
      style={{ flexShrink: 1 }}
      contentContainerStyle={{ padding: 20, gap: 16 }}
    >
      {children}
    </ScrollView>
  );
}
