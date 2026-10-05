import React from "react";
import { View } from "react-native";
import { CircleAlert } from "lucide-react-native";

import { useI18n } from "@/providers/I18nProvider";
import { EmptyState } from "@/ui/EmptyState";
import { PageShell } from "@/ui/PageShell";
import { ScreenHeader } from "@/ui/ScreenHeader";
import { useTheme } from "@/ui/ThemeProvider";

/** 里程碑占位屏：统一空态，后续由正式 Screen 替换。 */
export function PlaceholderScreen({
  seal,
  titleKey,
}: {
  seal: string;
  titleKey: string;
}) {
  const { t } = useI18n();
  const { theme } = useTheme();
  return (
    <PageShell scroll={false}>
      <ScreenHeader title={t(titleKey)} />
      <View style={{ flex: 1, padding: 16, justifyContent: "center" }}>
        <EmptyState
          icon={<CircleAlert size={22} color={theme.colors.accent} />}
          title={t("empty.generic.title")}
          hint={t("empty.generic.hint")}
        />
      </View>
    </PageShell>
  );
}
