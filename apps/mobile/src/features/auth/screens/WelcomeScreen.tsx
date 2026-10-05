import React, { useState } from "react";
import { Text, View } from "react-native";
import { useRouter } from "expo-router";
import {
  ArrowUpRight,
  BookOpen,
  Compass,
  MessageCircle,
} from "lucide-react-native";
import { useCopy } from "@/lib/copy";
import { useAuth } from "@/providers/AuthProvider";
import { errorMessage } from "@/lib/feedback";
import { PageShell } from "@/ui/PageShell";
import { Button, Card, useTheme } from "@/ui";
import { Body, Hint, Label } from "@/ui/Elements";
import { LearningArt } from "@/ui/LearningArt";
import { useAdaptive } from "@/shell/adaptive/window-class";
export function WelcomeScreen() {
  const { theme } = useTheme();
  const c = useCopy();
  const router = useRouter();
  const auth = useAuth();
  const adaptive = useAdaptive();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const wide = adaptive.maxPanes > 1;
  const guestAllowed =
    auth.state.status === "signed-out" && auth.state.guestAllowed;
  const items = [
    {
      icon: MessageCircle,
      title: c("随时提问", "Talk it through"),
      hint: c(
        "把复杂的知识，一步步理解。",
        "Understand something new, one step at a time.",
      ),
    },
    {
      icon: BookOpen,
      title: c("整理所学", "Make it yours"),
      hint: c(
        "让教材、笔记和图示各就其位。",
        "A home for materials, notes and ideas.",
      ),
    },
    {
      icon: Compass,
      title: c("找到节奏", "Find your rhythm"),
      hint: c(
        "从今天的一小步，走向你的目标。",
        "Small steps today, toward your own goals.",
      ),
    },
  ];
  return (
    <PageShell>
      <Body style={{ maxWidth: 1060, flexGrow: 1, gap: 32, paddingTop: 28 }}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
          <View
            style={{
              width: 38,
              height: 38,
              borderRadius: 13,
              backgroundColor: theme.colors.accent,
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <BookOpen size={21} color={theme.colors.onAccent} />
          </View>
          <Label>Next Tutor</Label>
        </View>
        <View
          style={{
            flexDirection: wide ? "row" : "column",
            alignItems: "center",
            gap: wide ? 48 : 12,
            paddingVertical: 12,
          }}
        >
          <View style={{ width: wide ? "47%" : "100%" }}>
            <LearningArt compact={adaptive.compactHeight} />
          </View>
          <View style={{ flex: 1, width: "100%", gap: 20 }}>
            <Hint>
              {c(
                "一点好奇，开启下一步",
                "A little curiosity. A new beginning.",
              )}
            </Hint>
            <Text
              accessibilityRole="header"
              style={[
                theme.type.display,
                {
                  fontSize: 36,
                  lineHeight: 46,
                  color: theme.colors.fg,
                  letterSpacing: -1,
                },
              ]}
            >
              {c("把不懂的，\n变成自己的。", "Make every\n“I wonder” count.")}
            </Text>
            <Text style={[theme.type.body, { color: theme.colors.muted }]}>
              {c(
                "带上你的教材与问题。从一段对话开始，找到适合自己的学习节奏。",
                "Bring your materials and questions. Start a conversation, and find your own learning rhythm.",
              )}
            </Text>
            <View style={{ gap: 10, marginTop: 8 }}>
              <Button
                testID="welcome-sign-in"
                title={c("开始学习", "Start learning")}
                size="lg"
                onPress={() => router.push("/(auth)/sign-in")}
                rightIcon={
                  <ArrowUpRight size={20} color={theme.colors.onAccent} />
                }
                fullWidth
              />
              <Button
                title={c("创建账户", "Create an account")}
                variant="outline"
                onPress={() => router.push("/(auth)/register")}
                fullWidth
              />
              {guestAllowed ? (
                <Button
                  title={c("先体验一下", "Explore as a guest")}
                  variant="ghost"
                  loading={pending}
                  onPress={() => {
                    setPending(true);
                    setError("");
                    void auth
                      .continueAsGuest()
                      .then(() => router.replace("/"))
                      .catch((e) => setError(errorMessage(e, c)))
                      .finally(() => setPending(false));
                  }}
                  fullWidth
                />
              ) : null}
              {error ? <Hint>{error}</Hint> : null}
            </View>
          </View>
        </View>
        <View style={{ flexDirection: wide ? "row" : "column", gap: 12 }}>
          {items.map(({ icon: Icon, title, hint }) => (
            <Card
              key={title}
              style={{
                flex: wide ? 1 : undefined,
                flexDirection: "row",
                gap: 14,
                padding: 18,
              }}
            >
              <Icon size={22} color={theme.colors.accent} />
              <View style={{ flex: 1, gap: 5 }}>
                <Label>{title}</Label>
                <Hint>{hint}</Hint>
              </View>
            </Card>
          ))}
        </View>
        <Hint>
          {c(
            "学习内容由服务端安全保存。你的学习，属于你。",
            "Your learning belongs to you. Content is securely stored by your server.",
          )}
        </Hint>
      </Body>
    </PageShell>
  );
}
