import React from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { ChevronRight, ScrollText } from "lucide-react-native";
import { useCopy } from "@/lib/copy";
import { useI18n } from "@/providers/I18nProvider";
import { useUiPrefs } from "@/stores/ui";
import {
  APP_VERSION,
  BUILD_NUMBER,
  resolveApiBaseUrl,
} from "@/platform/config";
import { Card, Chip, ListRow, SegmentedControl, useTheme } from "@/ui";
import { Body, Hint, Label, Section } from "@/ui/Elements";
import { FeatureShell } from "@/ui/FeatureShell";
export function SettingsScreen() {
  const c = useCopy();
  const router = useRouter();
  const { theme } = useTheme();
  const { preference, setPreference, fontScale, setFontScale } = useTheme();
  const { lang, setLang } = useI18n();
  const prefs = useUiPrefs();
  return (
    <FeatureShell
      title={c("外观与设置", "Appearance and settings")}
      auth={false}
    >
      <Body>
        <Section title={c("外观", "Appearance")}>
          <Card>
            <SegmentedControl
              items={[
                { key: "system", label: c("跟随系统", "System") },
                { key: "light", label: c("浅色", "Light") },
                { key: "dark", label: c("深色", "Dark") },
              ]}
              active={preference}
              onChange={(v) => setPreference(v as "system" | "light" | "dark")}
            />
          </Card>
        </Section>
        <Section title={c("界面语言", "Interface language")}>
          <SegmentedControl
            items={[
              { key: "zh", label: "中文" },
              { key: "en", label: "English" },
            ]}
            active={lang}
            onChange={(v) => setLang(v as "zh" | "en")}
          />
        </Section>
        <Section title={c("阅读字号", "Reading size")}>
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
            {[1, 1.15, 1.3, 1.5, 1.75].map((s) => (
              <Chip
                key={s}
                label={`${Math.round(s * 100)}%`}
                active={fontScale === s}
                onPress={() => setFontScale(s)}
              />
            ))}
          </View>
          <Hint>
            {c(
              "同时支持系统字体缩放；内容会自动换行。",
              "System font scaling is also supported. Text wraps to fit.",
            )}
          </Hint>
        </Section>
        <Section title={c("回答语言", "Response language")}>
          <SegmentedControl
            items={[
              { key: "auto", label: c("自动", "Auto") },
              { key: "zh", label: "中文" },
              { key: "en", label: "English" },
            ]}
            active={prefs.outputLanguage}
            onChange={(v) => prefs.setOutputLanguage(v as "auto" | "zh" | "en")}
          />
        </Section>
        <Section title={c("默认学段", "Default learning level")}>
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
            {["自动", "小学", "初中", "高中", "本科"].map((g) => (
              <Chip
                key={g}
                label={g}
                active={prefs.defaultGrade === g}
                onPress={() =>
                  prefs.setDefaultGrade(g as typeof prefs.defaultGrade)
                }
              />
            ))}
          </View>
        </Section>
        <Section title={c("开源许可", "Open-source licenses")}>
          <Card style={{ padding: 4 }}>
            <ListRow
              title={c("第三方开源许可", "Third-party open-source licenses")}
              subtitle={c(
                "随本应用分发的开源组件与其许可条款",
                "Open-source components and license terms shipped with this app",
              )}
              left={<ScrollText size={21} color={theme.colors.accent} />}
              right={<ChevronRight size={18} color={theme.colors.muted} />}
              onPress={() => router.push("/(main)/me/licenses" as "/(main)/me/settings")}
            />
          </Card>
        </Section>
        <Card style={{ gap: 12 }}>
          <Label>Next Tutor {APP_VERSION}</Label>
          <Hint>
            {c(
              "学习内容来自你连接的服务端。退出账户时清理应用内的个人缓存。",
              "Learning content comes from your connected server. Signing out clears private in-app caches.",
            )}
          </Hint>
          <Hint>{c(`构建 ${BUILD_NUMBER}`, `Build ${BUILD_NUMBER}`)}</Hint>
        </Card>
      </Body>
    </FeatureShell>
  );
}
