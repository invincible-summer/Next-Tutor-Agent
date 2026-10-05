import React from "react";
import { View, Text } from "react-native";
import { useRouter } from "expo-router";
import {
  Archive,
  Brain,
  ChevronRight,
  Settings,
  ShieldCheck,
  UserRound,
  ChartNoAxesCombined,
} from "lucide-react-native";
import { useAuth } from "@/providers/AuthProvider";
import { useCopy } from "@/lib/copy";
import { Button, Card, ListRow, useTheme } from "@/ui";
import { Body, Hint, Label, Section } from "@/ui/Elements";
import { FeatureShell } from "@/ui/FeatureShell";
export function MeScreen() {
  const c = useCopy();
  const { theme } = useTheme();
  const router = useRouter();
  const { state } = useAuth();
  const name =
    state.status === "signed-in"
      ? state.user.profile.name || state.user.username
      : c("学习体验者", "Guest learner");
  return (
    <FeatureShell title={c("我的", "Me")} back={false} auth={false}>
      <Body>
        <Card
          style={{
            padding: 24,
            gap: 16,
            flexDirection: "row",
            alignItems: "center",
          }}
        >
          <View
            style={{
              width: 64,
              height: 64,
              borderRadius: 22,
              backgroundColor: theme.colors["accent-soft"],
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Text style={[theme.type.title, { color: theme.colors.accent }]}>
              {name.slice(0, 1).toUpperCase()}
            </Text>
          </View>
          <View style={{ flex: 1, gap: 8 }}>
            <Label>{name}</Label>
            <Hint>
              {state.status === "signed-in"
                ? state.user.email
                : c(
                    "登录后，让学习持续积累。",
                    "Sign in to keep your learning with you.",
                  )}
            </Hint>
          </View>
        </Card>
        {state.status === "guest" ? (
          <Button
            title={c("登录账户", "Sign in")}
            onPress={() => router.push("/(auth)/sign-in")}
          />
        ) : null}
        <Section title={c("了解自己的学习", "Understand your learning")}>
          <Card style={{ padding: 4 }}>
            {[
              {
                path: "profile",
                title: c("个人画像", "Profile"),
                hint: c(
                  "学习阶段与偏好",
                  "Your learning level and preferences",
                ),
                icon: UserRound,
              },
              {
                path: "insights",
                title: c("学习洞察", "Insights"),
                hint: c("从证据中，看见理解", "Understand your evidence"),
                icon: ChartNoAxesCombined,
              },
              {
                path: "memory",
                title: c("学习记忆", "Memory"),
                hint: c(
                  "跨会话的有界记忆",
                  "Bounded memory across conversations",
                ),
                icon: Brain,
              },
            ].map(({ path, title, hint, icon: Icon }) => (
              <ListRow
                key={path}
                title={title}
                subtitle={hint}
                left={<Icon size={21} color={theme.colors.accent} />}
                right={<ChevronRight size={18} color={theme.colors.muted} />}
                onPress={() =>
                  router.push(`/(main)/me/${path}` as "/(main)/me/profile")
                }
              />
            ))}
          </Card>
        </Section>
        <Section title={c("管理你的空间", "Your space")}>
          <Card style={{ padding: 4 }}>
            {[
              { path: "archive", title: c("归档", "Archive"), icon: Archive },
              {
                path: "account",
                title: c("账户与设备", "Account and devices"),
                icon: ShieldCheck,
              },
              {
                path: "settings",
                title: c("外观与设置", "Appearance and settings"),
                icon: Settings,
              },
            ].map(({ path, title, icon: Icon }) => (
              <ListRow
                key={path}
                title={title}
                left={<Icon size={21} color={theme.colors.accent} />}
                right={<ChevronRight size={18} color={theme.colors.muted} />}
                onPress={() =>
                  router.push(`/(main)/me/${path}` as "/(main)/me/settings")
                }
              />
            ))}
          </Card>
        </Section>
      </Body>
    </FeatureShell>
  );
}
