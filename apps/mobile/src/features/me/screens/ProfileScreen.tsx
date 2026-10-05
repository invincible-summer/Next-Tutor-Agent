import React, { useState } from "react";
import { Image, View } from "react-native";
import * as ImagePicker from "expo-image-picker";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { useAction, useServerQuery } from "@/lib/server-state";
import {
  Button,
  Card,
  Chip,
  Field,
  TextField,
  SegmentedControl,
  useToast,
} from "@/ui";
import { errorMessage } from "@/lib/feedback";
import { deletePickedFiles } from "@/platform/files";
import { Body, Hint, Label, QueryState, Section } from "@/ui/Elements";
import { FeatureShell } from "@/ui/FeatureShell";
import { EditSheet } from "@/ui/EditSheet";
function avatarDataUrl(bytes: Uint8Array): string {
  const alphabet =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  let encoded = "";
  for (let i = 0; i < bytes.length; i += 3) {
    const value =
      ((bytes[i] ?? 0) << 16) |
      ((bytes[i + 1] ?? 0) << 8) |
      (bytes[i + 2] ?? 0);
    encoded +=
      alphabet[(value >>> 18) & 63]! +
      alphabet[(value >>> 12) & 63]! +
      (i + 1 < bytes.length ? alphabet[(value >>> 6) & 63] : "=") +
      (i + 2 < bytes.length ? alphabet[value & 63] : "=");
  }
  return `data:image/png;base64,${encoded}`;
}
export function ProfileScreen() {
  const c = useCopy();
  const action = useAction();
  const toast = useToast();
  const query = useServerQuery(["profile"], (s) => apiClient().profile.get(s));
  const image = useServerQuery(
    ["avatar", query.data?.profile.avatar],
    async (signal) => {
      const bytes = new Uint8Array(
        await apiClient().profile.avatar(query.data?.profile.avatar, signal),
      );
      return avatarDataUrl(bytes);
    },
    { enabled: !!query.data?.profile.avatar },
  );
  const [edit, setEdit] = useState(false);
  const [name, setName] = useState("");
  const [school, setSchool] = useState("");
  const [grade, setGrade] = useState("自动");
  const [subjects, setSubjects] = useState("");
  async function avatar() {
    const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) {
      toast(
        c(
          "请在系统设置中允许访问照片，或保留现有头像。",
          "Allow photo access in system settings, or keep your current avatar.",
        ),
      );
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images"],
      allowsEditing: true,
      aspect: [1, 1],
      quality: 0.8,
    });
    if (result.canceled) return;
    const asset = result.assets[0]!;
    const form = new FormData();
    form.append("file", {
      uri: asset.uri,
      name: asset.fileName ?? "avatar.jpg",
      type: asset.mimeType ?? "image/jpeg",
    } as unknown as Blob);
    try {
      await action.run(() => apiClient().profile.uploadAvatar(form));
    } finally {
      deletePickedFiles([asset.uri]);
    }
  }
  return (
    <FeatureShell title={c("个人画像", "Your profile")}>
      <Body>
        <QueryState query={query}>
          <Card style={{ gap: 12, padding: 24 }}>
            <Label>
              {query.data?.profile.name ||
                c("你的学习画像", "Your learning profile")}
            </Label>
            <Hint>
              {[query.data?.profile.grade, query.data?.profile.school]
                .filter(Boolean)
                .join(" · ")}
            </Hint>
            <Hint>{query.data?.profile.subjects.join(" · ")}</Hint>
            <Button
              title={c("编辑个人信息", "Edit profile")}
              variant="outline"
              onPress={() => {
                const p = query.data?.profile;
                setName(p?.name ?? "");
                setSchool(p?.school ?? "");
                setGrade(p?.grade ?? "自动");
                setSubjects(p?.subjects.join(", ") ?? "");
                setEdit(true);
              }}
            />
          </Card>
          <Section title={c("头像", "Avatar")}>
            {image.data ? (
              <Image
                source={{ uri: image.data }}
                accessibilityLabel={c("当前头像", "Current avatar")}
                style={{ width: 88, height: 88, borderRadius: 28 }}
              />
            ) : (
              <Hint>
                {c(
                  "选择一个头像，让学习空间更有个人感。",
                  "Choose an avatar to make your learning space feel personal.",
                )}
              </Hint>
            )}
            <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
              <Button
                title={c("选择头像", "Choose avatar")}
                variant="outline"
                loading={action.pending}
                onPress={() =>
                  void avatar().catch((error) =>
                    toast(errorMessage(error, c), "error"),
                  )
                }
              />
              {query.data?.profile.avatar ? (
                <Button
                  title={c("移除头像", "Remove avatar")}
                  variant="ghost"
                  onPress={() =>
                    void action.run(() => apiClient().profile.deleteAvatar())
                  }
                />
              ) : null}
            </View>
          </Section>
          <Section title={c("题图偏好", "Illustration preference")}>
            <SegmentedControl
              items={["v1", "v2", "v3"].map((key) => ({
                key,
                label: key.toUpperCase(),
              }))}
              active={query.data?.profile.prefs?.quiz_illustration_mode ?? "v1"}
              onChange={(v) =>
                void action.run(() =>
                  apiClient().profile.update({
                    prefs: { quiz_illustration_mode: v as "v1" | "v2" | "v3" },
                  }),
                )
              }
            />
            <Hint>
              {c(
                "V2 / V3 的合并审核由服务端强制执行。",
                "Combined review for V2 / V3 is enforced by your server.",
              )}
            </Hint>
          </Section>
        </QueryState>
      </Body>
      <EditSheet
        open={edit}
        title={c("个人信息", "Profile details")}
        onClose={() => setEdit(false)}
        onSave={() =>
          void action.run(
            () =>
              apiClient().profile.update({
                name: name.trim(),
                school: school.trim(),
                grade,
                subjects: subjects
                  .split(/[,，]/)
                  .map((s) => s.trim())
                  .filter(Boolean),
              }),
            () => setEdit(false),
          )
        }
        pending={action.pending}
      >
        <Field label={c("姓名", "Name")}>
          <TextField
            value={name}
            onChangeText={setName}
            accessibilityLabel={c("姓名", "Name")}
          />
        </Field>
        <Field label={c("学校", "School")}>
          <TextField
            value={school}
            onChangeText={setSchool}
            accessibilityLabel={c("学校", "School")}
          />
        </Field>
        <Field label={c("学习阶段", "Level")}>
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
            {["自动", "小学", "初中", "高中", "本科"].map((g) => (
              <Chip
                key={g}
                label={g}
                active={grade === g}
                onPress={() => setGrade(g)}
              />
            ))}
          </View>
        </Field>
        <Field label={c("学科（逗号分隔）", "Subjects (comma separated)")}>
          <TextField
            value={subjects}
            onChangeText={setSubjects}
            accessibilityLabel={c("学科", "Subjects")}
          />
        </Field>
      </EditSheet>
    </FeatureShell>
  );
}
