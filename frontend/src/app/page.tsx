"use client";
import { useEffect } from "react";
// 落地页英文品牌行的自托管展示衬线（.hero-en-title，globals.css）
import "@fontsource/playfair-display/700.css";
import { useUIStore } from "@/lib/store";
import { useAuthStore, hydrateAuth } from "@/lib/auth-store";
import { makePageT } from "@/lib/i18n-page";
import { useAssistantPage } from "@/lib/assistant/useAssistantPage";
import { currentRouteEpoch } from "@/lib/assistant/page-context";
import { LANDING_STRINGS } from "./landing-strings";
import { LandingNav } from "@/components/landing/LandingNav";
import { Hero } from "@/components/landing/Hero";
import { Marquee } from "@/components/landing/Marquee";
import { Features } from "@/components/landing/Features";
import { Modules } from "@/components/landing/Modules";
import { HowItWorks } from "@/components/landing/HowItWorks";
import { CtaBanner } from "@/components/landing/CtaBanner";
import { Footer } from "@/components/landing/Footer";
import { useLandingSnap } from "@/components/landing/useLandingSnap";

/**
 * 项目主页面（/）：介绍 + 开始使用 + 登录/注册入口。
 * UIProvider 恢复语言偏好；这里恢复登录态，决定主 CTA 的目标。
 */
export default function Home() {
  const lang = useUIStore((s) => s.lang);
  const user = useAuthStore((s) => s.user);
  // §20.2 上下文适配器（首页无表单字段，§19.9）。
  useAssistantPage({
    context: () => ({
      schema_version: 1,
      route_id: "home",
      route_epoch: currentRouteEpoch(),
    }),
  });

  useEffect(() => {
    void hydrateAuth();
    // 锚点平滑滚动仅在本页生效，离开时还原
    if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      document.documentElement.classList.add("scroll-smooth");
    }
    return () => document.documentElement.classList.remove("scroll-smooth");
  }, []);

  const tr = makePageT(lang, LANDING_STRINGS);
  useLandingSnap();

  return (
    <div className="min-h-screen bg-bg text-fg">
      {/* 纸纹颗粒质感覆盖层 */}
      <div aria-hidden className="grain pointer-events-none fixed inset-0 z-[60]" />
      <LandingNav tr={tr} loggedIn={!!user} />
      <main>
        <Hero tr={tr} loggedIn={!!user} />
        <Marquee tr={tr} />
        <Features tr={tr} />
        <Modules tr={tr} />
        <HowItWorks tr={tr} />
        <CtaBanner tr={tr} loggedIn={!!user} />
      </main>
      <Footer tr={tr} />
    </div>
  );
}
