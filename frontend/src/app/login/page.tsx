"use client";
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

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const { setAuth, authRequired } = useAuthStore();
  const { lang } = useUIStore();
  const tr = (k: string) => t(lang, k);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const redirect = params.get("redirect") || "/chat";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail === "invalid_credentials" ? tr("auth.error.invalid") : tr("auth.error.generic"));
        setLoading(false);
        return;
      }
      setAuth(data.token, data.user);
      router.push(redirect);
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
        <>
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
