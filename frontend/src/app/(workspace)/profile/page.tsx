"use client";

import { useEffect, useState } from "react";
import { Info, UserRound } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { EmptyState, ErrorNote, PageSkeleton } from "@/components/ui/EmptyState";
import { getUxMotivation, getUxProfile } from "@/lib/api";
import { getStudentProfile } from "@/lib/api-modules";
import { useAuthStore } from "@/lib/auth-store";
import { makePageT } from "@/lib/i18n-page";
import { useUIStore } from "@/lib/store";
import type { UxMotivation, UxProfileSummary } from "@/lib/types";
import type { StudentProfileResp } from "@/lib/types-modules";
import { AcademicCard } from "@/components/pages/profile/AcademicCard";
import { AccountCard } from "@/components/pages/profile/AccountCard";
import { IdentityCard } from "@/components/pages/profile/IdentityCard";
import { InteractionCard } from "@/components/pages/profile/InteractionCard";
import { MotivationCard } from "@/components/pages/profile/MotivationCard";
import { STRINGS } from "./strings";

type FetchKind = "profile" | "ux" | "moti";

const FETCHERS = {
  profile: getStudentProfile,
  ux: getUxProfile,
  moti: getUxMotivation,
} as const;

export default function ProfilePage() {
  const { lang } = useUIStore();
  const user = useAuthStore((s) => s.user);
  const tr = makePageT(lang, STRINGS);

  const [loading, setLoading] = useState(true);
  const [profile, setProfile] = useState<StudentProfileResp | null>(null);
  const [ux, setUx] = useState<UxProfileSummary | null>(null);
  const [moti, setMoti] = useState<UxMotivation | null>(null);
  const [err, setErr] = useState<Partial<Record<FetchKind, boolean>>>({});

  // 三路数据各自独立失败/重试：单个端点挂了不影响其他卡片。
  const apply = (kind: FetchKind, r: StudentProfileResp | UxProfileSummary | UxMotivation) => {
    if (kind === "profile") setProfile(r as StudentProfileResp);
    else if (kind === "ux") setUx(r as UxProfileSummary);
    else setMoti(r as UxMotivation);
    setErr((e) => ({ ...e, [kind]: false }));
  };

  const retry = (kind: FetchKind) => {
    FETCHERS[kind]()
      .then((r) => apply(kind, r))
      .catch(() => setErr((e) => ({ ...e, [kind]: true })));
  };

  useEffect(() => {
    let cancelled = false;
    const settle = (kind: FetchKind, r: StudentProfileResp | UxProfileSummary | UxMotivation) => {
      if (!cancelled) apply(kind, r);
    };
    const fail = (kind: FetchKind) => {
      if (!cancelled) setErr((e) => ({ ...e, [kind]: true }));
    };
    Promise.all([
      getStudentProfile().then((r) => settle("profile", r)).catch(() => fail("profile")),
      getUxProfile().then((r) => settle("ux", r)).catch(() => fail("ux")),
      getUxMotivation().then((r) => settle("moti", r)).catch(() => fail("moti")),
    ]).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const profileStatus = profile?.status ?? null;
  const uxDisabled = (ux as unknown as { status?: string } | null)?.status === "disabled";

  return (
    <div className="h-full overflow-y-auto p-6 page-in">
      <div className="mx-auto flex max-w-[1200px] flex-col gap-4">
        <header>
          <h1 className="font-serif text-xl font-semibold text-fg">{tr("profile.title")}</h1>
          <p className="mt-0.5 text-xs text-muted">{tr("profile.desc")}</p>
        </header>

        {/* M0 账户卡（仅登录态渲染，guest 模式自动隐藏） */}
        <AccountCard tr={tr} />

        {loading ? (
          <PageSkeleton />
        ) : (
          <>
            {/* 身份卡：仅游客态渲染——登录态顶部已有 M0 账户卡（同名同学段），
                再渲染一张就是重复身份；M2 活跃信息已并入学术卡页脚。 */}
            {!user && profileStatus === "ok" && profile?.profile && (
              <IdentityCard profile={profile.profile} lang={lang} tr={tr} />
            )}

            {/* M2 / M8 两栏 */}
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              {err.profile || profileStatus === "error" ? (
                <Card>
                  <ErrorNote message={tr("err.load")} retry={() => retry("profile")} />
                </Card>
              ) : profileStatus === "disabled" ? (
                <Card>
                  <EmptyState icon={<UserRound size={22} />} title={tr("m2.title")} desc={tr("disabled.m2")} />
                </Card>
              ) : profileStatus === "ok" && profile?.profile ? (
                <AcademicCard profile={profile.profile} lang={lang} tr={tr} />
              ) : (
                <Card>
                  <EmptyState
                    icon={<UserRound size={22} />}
                    title={tr("empty.profile")}
                    desc={tr("empty.profile.desc")}
                  />
                </Card>
              )}

              {err.ux ? (
                <Card>
                  <ErrorNote message={tr("err.load")} retry={() => retry("ux")} />
                </Card>
              ) : uxDisabled ? (
                <Card>
                  <EmptyState icon={<UserRound size={22} />} title={tr("m8.title")} desc={tr("disabled.m8")} />
                </Card>
              ) : ux && ux.event_count > 0 ? (
                <InteractionCard ux={ux} tr={tr} />
              ) : (
                <Card>
                  <EmptyState icon={<UserRound size={22} />} title={tr("m8.title")} desc={tr("m8.empty")} />
                </Card>
              )}
            </div>

            {/* 学习激励 */}
            {err.moti ? (
              <Card>
                <ErrorNote message={tr("err.load")} retry={() => retry("moti")} />
              </Card>
            ) : moti ? (
              <MotivationCard moti={moti} tr={tr} />
            ) : null}

            {/* 底部说明条 */}
            <div className="flex items-start gap-2 border-t border-border-light pt-3 text-[11px] leading-relaxed text-muted">
              <Info size={13} className="mt-0.5 shrink-0" />
              <div>
                <span className="font-medium text-fg-secondary">{tr("about.title")}：</span>
                {tr("about.body")}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
