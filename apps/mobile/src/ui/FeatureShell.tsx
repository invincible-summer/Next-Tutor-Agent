import React from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { Sparkles } from "lucide-react-native";
import { apiClient } from "@/lib/api";
import { useServerQuery } from "@/lib/server-state";
import { useCopy } from "@/lib/copy";
import { useAuth } from "@/providers/AuthProvider";
import { WorkspaceChip } from "@/providers/WorkspaceProvider";
import { PageShell } from "./PageShell";
import { ScreenHeader } from "./ScreenHeader";
import { BackButton, Body, QueryState } from "./Elements";
import { Button, IconButton } from "./Button";
import { EmptyState } from "./EmptyState";
import { useTheme } from "./ThemeProvider";
import type { ProductCapabilities } from "@next-tutor/api-client";
export function FeatureShell({
  title,
  subtitle,
  children,
  back = true,
  auth = true,
  scroll = true,
  right,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  back?: boolean;
  auth?: boolean;
  scroll?: boolean;
  right?: React.ReactNode;
}) {
  const { state } = useAuth();
  const c = useCopy();
  const router = useRouter();
  return (
    <PageShell scroll={scroll}>
      <ScreenHeader
        title={title}
        subtitle={subtitle}
        left={back ? <BackButton /> : undefined}
        right={right ?? <WorkspaceChip />}
      />
      {auth && state.status !== "signed-in" ? (
        <Body>
          <EmptyState
            title={c("让学习延续下来", "Keep your learning with you")}
            hint={c(
              "登录后保存笔记、课程和学习记录。游客会话不会自动合并。",
              "Sign in to save notes, lessons and learning records. Guest conversations are kept separate.",
            )}
            actionLabel={c("登录账户", "Sign in")}
            onAction={() => router.push("/(auth)/sign-in")}
          />
        </Body>
      ) : (
        children
      )}
    </PageShell>
  );
}
export function CapabilityGate({
  name,
  children,
}: {
  name: keyof ProductCapabilities;
  children: React.ReactNode;
}) {
  const query = useServerQuery(
    ["capabilities"],
    (signal) => apiClient().capabilities.get(signal),
    { public: true },
  );
  const c = useCopy();
  return (
    <QueryState query={query}>
      {query.data?.[name]?.available ? (
        children
      ) : (
        <Body>
          <EmptyState
            title={c("此功能暂不可用", "This feature is unavailable")}
            hint={c(
              "服务未启用或当前账户无访问权限。可以稍后重试。",
              "The service is disabled or unavailable for this account. Try again later.",
            )}
            actionLabel={c("刷新状态", "Refresh")}
            onAction={() => void query.refetch()}
          />
        </Body>
      )}
    </QueryState>
  );
}
