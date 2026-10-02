"use client";
import { navigationAnchor, navigationSucceeded } from "@/lib/assistant/navigation";


import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
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
import { useAssistantPage } from "@/lib/assistant/useAssistantPage";
import { currentRouteEpoch } from "@/lib/assistant/page-context";
import { DeepLinkQueryReader, focusDeepTarget } from "@/lib/assistant/deep-link";
import { AcademicCard } from "@/components/pages/profile/AcademicCard";
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

function ProfilePage() {
  const { lang } = useUIStore();
  const user = useAuthStore((s) => s.user);
  const tr = makePageT(lang, STRINGS);

  const [loading, setLoading] = useState(true);
  const [profile, setProfile] = useState<StudentProfileResp | null>(null);
  const [ux, setUx] = useState<UxProfileSummary | null>(null);
  const [moti, setMoti] = useState<UxMotivation | null>(null);
  const [err, setErr] = useState<Partial<Record<FetchKind, boolean>>>({});
  // Legacy account/preferences sections redirect; learning sections stay here.
  const [deepSection, setDeepSection] = useState("");
  const [deepNotice, setDeepNotice] = useState("");
  useAssistantPage({
    navigationStatus: (target) => {
      if (loading) return null;
      if (target.kind === "module") return navigationSucceeded;
      return target.kind === "profile_section" ? navigationAnchor("profile-section", target.section || "account") : null;
    },
    context: () => ({
      schema_version: 1,
      route_id: "profile",
      route_epoch: currentRouteEpoch(),
      view: deepSection || undefined,
    }),
  });
  const applyDeepLink = useCallback((params: Record<string, string>) => {
    setDeepSection(params.section || "");
  }, []);
  useEffect(() => {
    if (!deepSection || loading) return;
    const section = deepSection;
    // 消费走微任务：避免 effect 体内同步 setState 的级联渲染
    // （react-hooks/set-state-in-effect）。
    let alive = true;
    queueMicrotask(() => {
      if (!alive) return;
      setDeepSection("");
      if (!focusDeepTarget("profile-section", section)) {
        setDeepNotice(tr("deep.sectionMissing"));
      }
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deepSection, loading]);

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
      <Suspense><DeepLinkQueryReader keys={["section"]} onParams={applyDeepLink} /></Suspense>
      <div className="mx-auto flex max-w-[1200px] flex-col gap-4">
        <header>
          <h1 className="font-serif text-xl font-semibold text-fg">{tr("profile.title")}</h1>
          <p className="mt-0.5 text-xs text-muted">{tr("profile.desc")}</p>
        </header>
        {deepNotice && (
          <div className="rounded-[8px] border border-border bg-surface px-3 py-2 text-xs text-muted">{deepNotice}</div>
        )}

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
            <div data-profile-section="learning" className="grid grid-cols-1 gap-4 xl:grid-cols-2">
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
              <div data-profile-section="motivation"><MotivationCard moti={moti} tr={tr} /></div>
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

function ProfileRoute() {
  const params = useSearchParams();
  const router = useRouter();
  const section = params.get("section");
  const destination = section === "account" ? "/account"
    : section === "voice" || section === "assistant" ? `/settings?section=${section}` : null;
  useEffect(() => { if (destination) router.replace(destination); }, [destination, router]);
  return destination ? <PageSkeleton /> : <ProfilePage />;
}

export default function ProfileRoutePage() {
  return <Suspense fallback={<PageSkeleton />}><ProfileRoute /></Suspense>;
}
