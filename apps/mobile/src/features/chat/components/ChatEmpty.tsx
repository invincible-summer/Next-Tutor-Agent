import React, { useEffect, useState } from "react";
import { Text, View, useWindowDimensions } from "react-native";
import {
  BookOpen,
  ClipboardList,
  Search,
  Target,
  MessageCircle,
} from "lucide-react-native";
import { useTheme } from "@/ui/ThemeProvider";
import { Card } from "@/ui/Card";
import { useI18n } from "@/providers/I18nProvider";
import { useAuth } from "@/providers/AuthProvider";
import { apiClient } from "@/lib/api";
import { useUiPrefs } from "@/stores/ui";
import { useCopy } from "@/lib/copy";
import { gradeForApi } from "@/lib/grade";
export function ChatEmpty({ onPick }: { onPick: (text: string) => void }) {
  const { theme, fontScale } = useTheme();
  const { width } = useWindowDimensions();
  const { t, lang } = useI18n();
  const { state } = useAuth();
  const c = useCopy();
  const grade = useUiPrefs((s) => s.grade);
  const [greeting, setGreeting] = useState("");
  useEffect(() => {
    if (state.status !== "signed-in") return;
    let alive = true;
    void apiClient()
      .ux.greeting({ lang, grade: gradeForApi(grade) })
      .then((g) => {
        if (alive) setGreeting(g.greeting || "");
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [lang, grade, state.status]);
  const suggestions = [
    {
      icon: BookOpen,
      text: t("suggestion.explain.text"),
      desc: t("suggestion.explain"),
    },
    {
      icon: ClipboardList,
      text: t("suggestion.quiz.text"),
      desc: t("suggestion.quiz"),
    },
    {
      icon: Search,
      text: t("suggestion.error.text"),
      desc: t("suggestion.error"),
    },
    {
      icon: Target,
      text: t("suggestion.plan.text"),
      desc: t("suggestion.plan"),
    },
  ];
  return (
    <View
      style={{
        flex: 1,
        justifyContent: "center",
        width: "100%",
        maxWidth: 660,
        alignSelf: "center",
        padding: 24,
        gap: 24,
      }}
    >
      <View
        style={{
          width: 56,
          height: 56,
          borderRadius: 20,
          backgroundColor: theme.colors["accent-soft"],
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <MessageCircle size={28} color={theme.colors.accent} />
      </View>
      <View style={{ gap: 8 }}>
        <Text style={[theme.type.title, { color: theme.colors.fg }]}>
          {c("从你的问题开始", "Start with your question")}
        </Text>
        <Text style={[theme.type.body, { color: theme.colors.muted }]}>
          {greeting ||
            c(
              "一个没想通的概念，一道卡住的题，或者只是想多了解一点。",
              "A puzzling concept, a tricky question, or something you are curious about.",
            )}
        </Text>
      </View>
      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 12 }}>
        {suggestions.map((s) => (
          <Card
            key={s.text}
            onPress={() => onPick(s.text)}
            accessibilityLabel={s.text}
            style={{
              width: width > 600 && fontScale < 1.5 ? "47%" : "100%",
              flexGrow: 1,
              gap: 8,
              padding: 16,
            }}
          >
            <View
              style={{ flexDirection: "row", gap: 10, alignItems: "center" }}
            >
              <s.icon size={20} color={theme.colors.accent} />
              <Text
                style={[
                  theme.type.bodyStrong,
                  { color: theme.colors.fg, flex: 1 },
                ]}
              >
                {s.text}
              </Text>
            </View>
            <Text style={[theme.type.caption, { color: theme.colors.muted }]}>
              {s.desc}
            </Text>
          </Card>
        ))}
      </View>
    </View>
  );
}
