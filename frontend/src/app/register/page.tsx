"use client";
import { useState, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { API_BASE } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";
import { useUIStore } from "@/lib/store";
import { t } from "@/lib/i18n";
import { gradeForApi, type Grade } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";
import { AuthShell } from "@/components/auth/AuthShell";

// 学段 token 是后端契约（写入 UserProfile 并同步 StudentModel），保持中文不翻译。
// 默认「本科」：产品默认学段；「自动」（后端事实源为空串）仍可手动选择，提交前经 gradeForApi 转换。
const GRADES = ["自动", "小学", "初中", "高中", "本科"] as const satisfies readonly Grade[];

function RegisterForm() {
  const router = useRouter();
  const params = useSearchParams();
  const redirect = params.get("redirect") || "/chat";
  const { setAuth } = useAuthStore();
  const { lang } = useUIStore();
  const tr = (k: string) => t(lang, k);
  const [step, setStep] = useState<1 | 2>(1);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [username, setUsername] = useState("");
  const [name, setName] = useState("");
  const [grade, setGrade] = useState<Grade>("本科");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      // 学科不在注册时采集——之后通过工作区/资料中心按实际学习添加。
      const res = await fetch(`${API_BASE}/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password, username, name, grade: gradeForApi(grade) }),
      });
      const data = await res.json();
      if (!res.ok) {
        if (data.detail === "email_already_registered") {
          setError(tr("auth.error.emailTaken"));
        } else {
          setError(typeof data.detail === "string" ? data.detail : tr("auth.error.register"));
        }
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
      title={tr("auth.register.title")}
      subtitle={tr("auth.register.subtitle")}
      footer={
        <>
          {tr("auth.haveAccount")}{" "}
          <Link
            href={`/login?redirect=${encodeURIComponent(redirect)}`}
            className="font-medium text-accent hover:underline"
          >
            {tr("auth.toLogin")}
          </Link>
        </>
      }
    >
      {/* 步骤指示：胶囊进度（当前 = 实心黛青，已完成 = 浅黛青） */}
      <div className="mb-6 flex items-center justify-center gap-2.5 text-xs">
        <span
          className={`rounded-full px-3 py-1 font-medium transition-colors ${
            step === 1 ? "bg-accent text-white" : "bg-accent-soft text-accent-strong"
          }`}
        >
          1 · {tr("auth.register.step1")}
        </span>
        <span aria-hidden className="h-px w-6 bg-border" />
        <span
          className={`rounded-full px-3 py-1 font-medium transition-colors ${
            step === 2 ? "bg-accent text-white" : "bg-surface-sunken text-fg-tertiary"
          }`}
        >
          2 · {tr("auth.register.step2")}
        </span>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        {error && (
          <div className="motion-fade rounded-[8px] border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">
            {error}
          </div>
        )}

        <div key={step} className="motion-fade space-y-4">
        {step === 1 && (
          <>
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
            <Field label={tr("auth.password.new")}>
              <Input
                type="password"
                required
                minLength={6}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                autoComplete="new-password"
              />
            </Field>
            <Field label={tr("auth.username")}>
              <Input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder={tr("auth.username.placeholder")}
                maxLength={40}
              />
            </Field>
            <Button
              type="button"
              size="lg"
              className="w-full"
              disabled={!email || password.length < 6}
              onClick={() => setStep(2)}
            >
              {tr("auth.next")}
            </Button>
          </>
        )}

        {step === 2 && (
          <>
            <Field label={tr("auth.name")}>
              <Input
                type="text"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={tr("auth.name.placeholder")}
                maxLength={40}
              />
            </Field>
            <Field label={tr("auth.grade")}>
              <div className="flex rounded-full bg-surface-sunken p-1">
                {GRADES.map((g) => (
                  <button
                    key={g}
                    type="button"
                    onClick={() => setGrade(g)}
                    className={`flex-1 rounded-full px-2 py-1.5 text-sm transition-colors ${
                      grade === g
                        ? "bg-surface font-medium text-accent shadow-sm"
                        : "text-muted hover:text-fg"
                    }`}
                  >
                    {g}
                  </button>
                ))}
              </div>
            </Field>
            <div className="flex gap-2">
              <Button type="button" variant="outline" size="lg" className="flex-1" onClick={() => setStep(1)}>
                {tr("auth.back")}
              </Button>
              <Button type="submit" size="lg" className="flex-1" disabled={loading || !name}>
                {loading ? tr("auth.register.creating") : tr("auth.register.submit")}
              </Button>
            </div>
          </>
        )}
        </div>
      </form>
    </AuthShell>
  );
}

export default function RegisterPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-bg" />}>
      <RegisterForm />
    </Suspense>
  );
}
