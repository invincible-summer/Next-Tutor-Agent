"use client";
import { useAssistantPage } from "@/lib/assistant/useAssistantPage";
import { currentRouteEpoch } from "@/lib/assistant/page-context";
import { useState, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { API_BASE } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";
import { useUIStore } from "@/lib/store";
import { t } from "@/lib/i18n";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import { AuthShell } from "@/components/auth/AuthShell";
import { apiFetch } from "@/lib/api-fetch";
import { DEMO_MODE, SITE_BASE_PATH } from "@/lib/demo";

function LoginForm() {
  useAssistantPage({ context: () => ({ schema_version: 1, route_id: "login", route_epoch: currentRouteEpoch() }) });
  const router = useRouter();
  const params = useSearchParams();
  const { setAuth, authRequired } = useAuthStore();
  const { lang } = useUIStore();
  const tr = (k: string) => t(lang, k);
  const [email, setEmail] = useState(DEMO_MODE ? "example@example.com" : "");
  const [password, setPassword] = useState(DEMO_MODE ? "example" : "");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // 只允许站内路径：外链与协议相对 //evil.com 会把刚登录的用户重定向到钓鱼站。
  const rawRedirect = params.get("redirect");
  const redirect = rawRedirect && rawRedirect.startsWith("/")
    && !rawRedirect.startsWith("//") && !rawRedirect.startsWith("/\\")
    ? rawRedirect : "/chat";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await apiFetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
        // 接收刷新轨的 HttpOnly cookie（跨源部署下 include 必需）。
        credentials: "include",
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail === "invalid_credentials" ? tr("auth.error.invalid") : tr("auth.error.generic"));
        setLoading(false);
        return;
      }
      // 企业响应含 access_token/refresh_token：access token 只进内存，
      // 刷新凭据由服务端种进 HttpOnly cookie。
      setAuth(data.access_token ?? data.token, data.user, { enterprise: !!data.access_token });
      router.push(SITE_BASE_PATH && redirect.startsWith(SITE_BASE_PATH + "/") ? redirect.slice(SITE_BASE_PATH.length) : redirect);
    } catch {
      setError(tr("auth.error.network"));
      setLoading(false);
    }
  }

  return (
    <AuthShell
      title={tr("auth.login.title")}
      subtitle={tr("auth.login.subtitle")}
      footer={
        DEMO_MODE ? <span>{lang === "en" ? "Demo account: example@example.com / example" : "演示账户：example@example.com / example"}</span> : <>
          {tr("auth.noAccount")}{" "}
          <Link
            href={`/register?redirect=${encodeURIComponent(redirect)}`}
            className="font-medium text-accent hover:underline"
          >
            {tr("auth.toRegister")}
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && (
          <div className="motion-fade rounded-[8px] border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">
            {error}
          </div>
        )}
        <Field label={tr("auth.email")}>
          <Input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder={tr("auth.email.placeholder")}
            autoComplete="email"
          />
        </Field>
        <Field label={tr("auth.password")}>
          <Input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            autoComplete="current-password"
          />
        </Field>
        <Button type="submit" size="lg" className="w-full" disabled={loading}>
          {loading ? tr("auth.submit.logging") : tr("auth.submit.login")}
        </Button>
        {!authRequired && (
          <p className="text-center text-xs leading-relaxed text-fg-tertiary">
            {tr("auth.guestHint")}
          </p>
        )}
      </form>
    </AuthShell>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-bg" />}>
      <LoginForm />
    </Suspense>
  );
}
