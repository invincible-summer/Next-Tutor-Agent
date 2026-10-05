import React, { useState } from "react";
import { useRouter } from "expo-router";
import { apiClient } from "@/lib/api";
import { useCopy } from "@/lib/copy";
import { useAuth } from "@/providers/AuthProvider";
import { useAction, useServerQuery } from "@/lib/server-state";
import { confirm } from "@/lib/feedback";
import { Button, Card, Field, TextField, ListRow } from "@/ui";
import { Body, Hint, Label, QueryState, Section } from "@/ui/Elements";
import { FeatureShell } from "@/ui/FeatureShell";
import { EditSheet } from "@/ui/EditSheet";
export function AccountScreen() {
  const c = useCopy();
  const router = useRouter();
  const auth = useAuth();
  const action = useAction();
  const [deleting, setDeleting] = useState(false);
  const [password, setPassword] = useState("");
  const enterprise =
    auth.state.status === "signed-in" && !!auth.state.principal;
  const sessions = useServerQuery(
    ["auth-sessions"],
    () => apiClient().auth.sessions(),
    { enabled: enterprise },
  );
  return (
    <FeatureShell title={c("账户与设备", "Account and devices")}>
      <Body>
        <Card style={{ gap: 12 }}>
          <Label>
            {auth.state.status === "signed-in" ? auth.state.user.email : ""}
          </Label>
          <Hint>
            {c(
              "登录凭据使用系统安全存储。个人内容不会保存在普通偏好存储中。",
              "Credentials use secure system storage. Personal content is kept out of preference storage.",
            )}
          </Hint>
          <Button
            title={c("退出登录", "Sign out")}
            variant="outline"
            loading={action.pending}
            onPress={() =>
              void action.run(
                () => auth.signOut(),
                () => router.replace("/(auth)/welcome"),
              )
            }
          />
        </Card>
        {enterprise ? (
          <Section title={c("登录设备", "Signed-in devices")}>
            <QueryState
              query={sessions}
              empty={!sessions.data?.sessions.length}
            >
              {sessions.data?.sessions.map((s) => (
                <Card key={s.id} style={{ gap: 8 }}>
                  <Label>
                    {String(s.client.platform ?? c("设备", "Device"))}
                  </Label>
                  <Hint>{s.created_at}</Hint>
                  <Hint>
                    {s.revoked_at
                      ? c("已撤销", "Revoked")
                      : c("有效会话", "Active session")}
                  </Hint>
                  {!s.revoked_at ? (
                    <Button
                      title={c("撤销此会话", "Revoke session")}
                      variant="ghost"
                      onPress={() =>
                        void confirm(
                          c("撤销登录会话？", "Revoke this session?"),
                          c(
                            "该设备需要重新登录。",
                            "This device will need to sign in again.",
                          ),
                          c("撤销", "Revoke"),
                        ).then((ok) => {
                          if (ok)
                            void action.run(
                              () => apiClient().auth.revokeSession(s.id),
                              () => {
                                if (
                                  auth.state.status === "signed-in" &&
                                  auth.state.principal?.auth_session_id === s.id
                                )
                                  void auth.signOut();
                              },
                            );
                        })
                      }
                    />
                  ) : null}
                </Card>
              ))}
            </QueryState>
          </Section>
        ) : null}
        <Section title={c("账户数据", "Account data")}>
          <Hint>
            {c(
              "注销账户会永久删除名下学习内容。此操作需要重新确认密码。",
              "Deleting your account permanently removes its learning content. Password confirmation is required.",
            )}
          </Hint>
          <Button
            title={c("注销账户", "Delete account")}
            variant="ghost"
            onPress={() => setDeleting(true)}
          />
        </Section>
      </Body>
      <EditSheet
        open={deleting}
        title={c("注销账户", "Delete account")}
        onClose={() => {
          setDeleting(false);
          setPassword("");
        }}
        pending={action.pending}
        disabled={!password}
        saveLabel={c("永久删除账户", "Permanently delete account")}
        onSave={() =>
          void confirm(
            c(
              "永久删除账户与学习数据？",
              "Permanently delete your account and learning data?",
            ),
            c("此操作无法恢复。", "This action cannot be undone."),
            c("永久删除", "Delete permanently"),
          ).then((ok) => {
            if (ok)
              void action.run(
                async () => {
                  await apiClient().profile.deleteAccount(password);
                  await auth.signOut();
                },
                () => router.replace("/(auth)/welcome"),
              );
          })
        }
      >
        <Field label={c("当前密码", "Current password")}>
          <TextField
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            accessibilityLabel={c("确认当前密码", "Confirm current password")}
          />
        </Field>
      </EditSheet>
    </FeatureShell>
  );
}
