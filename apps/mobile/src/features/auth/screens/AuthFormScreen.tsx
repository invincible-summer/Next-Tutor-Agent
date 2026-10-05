import React, { useState } from "react";
import { KeyboardAvoidingView, Platform, View } from "react-native";
import { useRouter } from "expo-router";
import { ApiError } from "@next-tutor/api-client";
import { Eye, EyeOff } from "lucide-react-native";
import { useAuth } from "@/providers/AuthProvider";
import { useCopy } from "@/lib/copy";
import { consumeDestination } from "@/shell/routes";
import { errorMessage } from "@/lib/feedback";
import { PageShell } from "@/ui/PageShell";
import {
  Button,
  Field,
  TextField,
  ScreenHeader,
  Chip,
  IconButton,
  useTheme,
} from "@/ui";
import { BackButton, Body, Hint } from "@/ui/Elements";
export function AuthFormScreen({ register = false }: { register?: boolean }) {
  const { theme } = useTheme();
  const c = useCopy();
  const router = useRouter();
  const auth = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [grade, setGrade] = useState("自动");
  const [visible, setVisible] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const valid =
    !!email.trim() &&
    !!password &&
    (!register ||
      (password.length >= 8 &&
        new TextEncoder().encode(password).length <= 72 &&
        /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())));
  async function submit() {
    if (pending || !valid) return;
    setPending(true);
    setError("");
    try {
      if (register)
        await auth.register({
          email: email.trim(),
          password,
          name: name.trim(),
          grade,
        });
      else await auth.signIn(email.trim(), password);
      router.replace(consumeDestination());
    } catch (e) {
      setError(
        e instanceof ApiError && e.code === "invalid_credentials"
          ? c("邮箱或密码不正确。", "Email or password is incorrect.")
          : e instanceof ApiError && e.code === "email_already_registered"
            ? c("该邮箱已经注册。", "This email is already registered.")
            : errorMessage(e, c),
      );
    } finally {
      setPending(false);
    }
  }
  return (
    <KeyboardAvoidingView
      style={{ flex: 1 }}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <PageShell>
        <ScreenHeader title="Next Tutor" left={<BackButton />} />
        <Body style={{ maxWidth: 460, paddingTop: 36, gap: 24 }}>
          <ScreenHeader
            title={
              register
                ? c("开启你的学习空间", "Your space to learn")
                : c("欢迎回来", "Welcome back")
            }
            subtitle={
              register
                ? c(
                    "让每一次好奇，都留下成长的痕迹。",
                    "Let each new question become a step forward.",
                  )
                : c(
                    "继续上次的探索，或开启新的好奇。",
                    "Pick up where you left off, or start something new.",
                  )
            }
          />
          <Field label={c("邮箱", "Email")}>
            <TextField
              testID="auth-email"
              accessibilityLabel={c("邮箱", "Email")}
              value={email}
              onChangeText={setEmail}
              placeholder="you@example.com"
              keyboardType="email-address"
              autoCapitalize="none"
              autoComplete="email"
            />
          </Field>
          <Field
            label={c("密码", "Password")}
            hint={
              register
                ? c(
                    "至少 8 个字符，不超过 72 个 UTF-8 字节。",
                    "At least 8 characters, at most 72 UTF-8 bytes.",
                  )
                : undefined
            }
          >
            <View
              style={{ flexDirection: "row", gap: 4, alignItems: "center" }}
            >
              <TextField
                testID="auth-password"
                accessibilityLabel={c("密码", "Password")}
                value={password}
                onChangeText={setPassword}
                secureTextEntry={!visible}
                autoComplete={register ? "new-password" : "password"}
                style={{ flex: 1 }}
                onSubmitEditing={() => void submit()}
              />
              <IconButton
                icon={
                  visible ? (
                    <EyeOff size={20} color={theme.colors.muted} />
                  ) : (
                    <Eye size={20} color={theme.colors.muted} />
                  )
                }
                accessibilityLabel={c(
                  visible ? "隐藏密码" : "显示密码",
                  visible ? "Hide password" : "Show password",
                )}
                onPress={() => setVisible(!visible)}
              />
            </View>
          </Field>
          {register ? (
            <>
              <Field label={c("如何称呼你（可选）", "Your name (optional)")}>
                <TextField
                  value={name}
                  onChangeText={setName}
                  accessibilityLabel={c("姓名", "Name")}
                  maxLength={50}
                />
              </Field>
              <Field label={c("学习阶段", "Learning level")}>
                <View
                  style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}
                >
                  {["自动", "小学", "初中", "高中", "本科"].map((g, i) => (
                    <Chip
                      key={g}
                      label={c(
                        g,
                        [
                          "Auto",
                          "Primary",
                          "Middle",
                          "High school",
                          "University",
                        ][i]!,
                      )}
                      active={g === grade}
                      onPress={() => setGrade(g)}
                    />
                  ))}
                </View>
              </Field>
            </>
          ) : null}
          {error ? <Hint>{error}</Hint> : null}
          <Button
            testID="auth-submit"
            title={
              register ? c("创建账户", "Create account") : c("登录", "Sign in")
            }
            loading={pending}
            disabled={!valid}
            onPress={() => void submit()}
            size="lg"
            fullWidth
          />
          <Button
            title={
              register
                ? c("已有账户？登录", "Already a member? Sign in")
                : c("还没有账户？注册", "New here? Create an account")
            }
            variant="ghost"
            onPress={() =>
              router.replace(register ? "/(auth)/sign-in" : "/(auth)/register")
            }
            fullWidth
          />
        </Body>
      </PageShell>
    </KeyboardAvoidingView>
  );
}
