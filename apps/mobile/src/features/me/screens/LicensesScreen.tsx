import React, { useMemo, useState } from "react";
import {
  FlatList,
  Linking,
  Pressable,
  ScrollView,
  Text,
  View,
} from "react-native";
import { ChevronRight, ScrollText } from "lucide-react-native";
import licensesData from "../licenses.data.json";
import { useCopy } from "@/lib/copy";
import { Card, ListRow, Sheet, useTheme } from "@/ui";
import { Hint, Label } from "@/ui/Elements";
import { FeatureShell } from "@/ui/FeatureShell";

type LicensePackage = {
  name: string;
  version: string;
  license: string;
  homepage: string;
  copyrights: string[];
};

const PACKAGES = licensesData.packages as LicensePackage[];
const LICENSE_TEXTS = licensesData.license_texts as Record<string, string>;

export function LicensesScreen() {
  const c = useCopy();
  const { theme } = useTheme();
  const [selected, setSelected] = useState<LicensePackage | null>(null);

  const listHeader = useMemo(
    () => (
      <Card style={{ gap: 8 }}>
        <Label>
          {c(
            `第三方开源组件（${PACKAGES.length}）`,
            `Third-party open-source components (${PACKAGES.length})`,
          )}
        </Label>
        <Hint>
          {c(
            "本应用依托下列开源组件构建；数据由仓库许可清单生成，与发布物料同步。",
            "This app is built on the open-source components below; the data is generated from the repository license inventory and ships with each release.",
          )}
        </Hint>
      </Card>
    ),
    [c],
  );

  return (
    <FeatureShell
      title={c("第三方开源许可", "Third-party open-source licenses")}
      auth={false}
      scroll={false}
    >
      <View style={{ flex: 1 }}>
        <FlatList
          data={PACKAGES}
          keyExtractor={(item) => item.name}
          ListHeaderComponent={listHeader}
          contentContainerStyle={{ padding: 16, gap: 4, paddingBottom: 32 }}
          renderItem={({ item }) => (
            <ListRow
              title={item.name}
              subtitle={`${item.version} · ${item.license}`}
              left={<ScrollText size={21} color={theme.colors.accent} />}
              right={<ChevronRight size={18} color={theme.colors.muted} />}
              onPress={() => setSelected(item)}
            />
          )}
        />
      </View>
      <Sheet
        open={selected !== null}
        onClose={() => setSelected(null)}
        label={c("许可详情", "License details")}
      >
        {selected ? (
          <LicenseDetail pkg={selected} />
        ) : null}
      </Sheet>
    </FeatureShell>
  );
}

function LicenseDetail({ pkg }: { pkg: LicensePackage }) {
  const c = useCopy();
  const { theme } = useTheme();
  const text = LICENSE_TEXTS[pkg.license] ?? "";
  return (
    <View style={{ flex: 1, gap: 12, padding: 16 }}>
      <View style={{ gap: 4 }}>
        <Text style={{ color: theme.colors.fg, fontSize: 17, fontWeight: "600" }}>
          {pkg.name} {pkg.version}
        </Text>
        {pkg.homepage ? (
          <Pressable
            accessibilityRole="link"
            onPress={() => Linking.openURL(pkg.homepage)}
          >
            <Text
              style={{ color: theme.colors.accent, fontSize: 13 }}
              numberOfLines={1}
            >
              {pkg.homepage}
            </Text>
          </Pressable>
        ) : null}
      </View>
      {pkg.copyrights.length > 0 ? (
        <View style={{ gap: 2 }}>
          {pkg.copyrights.map((line) => (
            <Text
              key={line}
              style={{ color: theme.colors.muted, fontSize: 13 }}
            >
              {line}
            </Text>
          ))}
        </View>
      ) : null}
      <Text
        style={{
          color: theme.colors.muted,
          fontSize: 13,
          marginTop: 4,
          fontWeight: "600",
        }}
      >
        {pkg.license}
      </Text>
      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingBottom: 24 }}>
        {text ? (
          <Text style={{ color: theme.colors.fg, fontSize: 13, lineHeight: 19 }} selectable>
            {text}
          </Text>
        ) : (
          <Hint>
            {c(
              "该组件的许可原文未随清单入库，请通过上方仓库地址查阅。",
              "The full license text for this component is not vendored; see the repository link above.",
            )}
          </Hint>
        )}
      </ScrollView>
    </View>
  );
}
