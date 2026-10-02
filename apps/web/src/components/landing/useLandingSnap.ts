"use client";
import { useEffect } from "react";

/**
 * 落地页首屏翻页：仅作用于 Hero ↔ 功能区（前两个 [data-landing-snap]
 * 分节）这一条边界。Hero 可见时一次向下的轻滚意图（≥12px）立即平滑翻到
 * 功能区；在功能区顶部附近（25% 视口内）向上滚即翻回 Hero。其余页面
 * 一律原生滚动，不劫持。翻页后短锁定防连跳；锁定中新的明确意图（反向，
 * 或 450ms 后大幅滚动）立即中断动画交还原生滚动（忽略触控板惯性衰减的
 * 同向小增量）。prefers-reduced-motion 下退化为原生滚动。
 */
export function useLandingSnap() {
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const WHEEL_TRIGGER = 12; // 一次轻滚即翻页，不做时间窗累积
    const LOCK_MS = 700;
    const CANCEL_AFTER_MS = 450;
    const CANCEL_DELTA = 80;
    const SWIPE_MIN = 48;
    const UP_NEAR_TOP = 0.25; // 距功能区顶不足 25% 视口时，向上即翻回 Hero
    const HERO_VISIBLE = 0.2; // Hero 底部仍有 ≥20% 视口高度可见时视为"在首屏"

    const st = { locked: false, lockTimer: 0, flipAt: 0, dir: 0, touchY: 0, touchX: 0, touchFlipped: false };

    // scrollIntoView 的落点 = 元素顶 − scroll-margin（顶栏锚点偏移），
    // 判断边界位置用同一口径，翻页后才能精确对齐。
    const boundary = () => {
      const els = Array.from(document.querySelectorAll<HTMLElement>("[data-landing-snap]"));
      const hero = els[0];
      const features = els[1];
      if (!hero || !features) return null;
      const y = window.scrollY;
      const topOf = (el: HTMLElement) => {
        const r = el.getBoundingClientRect();
        const mt = parseFloat(getComputedStyle(el).scrollMarginTop) || 0;
        return r.top + y - mt;
      };
      return {
        hero,
        features,
        featTop: topOf(features),
        heroBottom: hero.getBoundingClientRect().bottom + y,
      };
    };

    /** dir>0：Hero → 功能区；dir<0：功能区顶 → Hero。不满足翻页条件返回 false。 */
    const flip = (dir: 1 | -1) => {
      const b = boundary();
      if (!b) return false;
      const y = window.scrollY;
      const vh = window.innerHeight;
      if (dir > 0) {
        // heroBottom 是文档坐标，需减去当前滚动位置再与视口阈值比较
        if (b.heroBottom - y <= vh * HERO_VISIBLE) return false; // 已离开首屏 → 原生滚动
        b.features.scrollIntoView({ behavior: "smooth", block: "start" });
      } else {
        if (y <= 4 || y > b.featTop + vh * UP_NEAR_TOP) return false; // 不在边界附近 → 原生滚动
        b.hero.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      st.locked = true;
      st.flipAt = Date.now();
      st.dir = dir;
      window.clearTimeout(st.lockTimer);
      st.lockTimer = window.setTimeout(() => {
        st.locked = false;
      }, LOCK_MS);
      return true;
    };

    const cancel = () => {
      window.clearTimeout(st.lockTimer);
      // 以瞬时定位打断进行中的平滑滚动，立刻交还原生滚动。
      // 注意本页 <html> 常驻 scroll-smooth，behavior:"auto" 会沿用 CSS
      // 平滑滚动而无法真正打断，必须显式 "instant"。
      window.scrollTo({ top: window.scrollY, behavior: "instant" });
      st.locked = false;
    };

    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey) return; // 缩放手势不干预
      if (e.deltaX !== 0 && Math.abs(e.deltaX) >= Math.abs(e.deltaY)) return; // 横向滚动不干预
      const delta = e.deltaMode === WheelEvent.DOM_DELTA_LINE ? e.deltaY * 16 : e.deltaY;
      if (st.locked) {
        const reversed = st.dir !== 0 && delta * st.dir < 0 && Math.abs(delta) >= WHEEL_TRIGGER;
        if (reversed || (Date.now() - st.flipAt > CANCEL_AFTER_MS && Math.abs(delta) > CANCEL_DELTA)) {
          cancel();
        } else {
          e.preventDefault();
        }
        return;
      }
      if (Math.abs(delta) < WHEEL_TRIGGER) return;
      if (flip(delta > 0 ? 1 : -1)) e.preventDefault();
    };

    const onTouchStart = (e: TouchEvent) => {
      st.touchY = e.touches[0]?.clientY ?? 0;
      st.touchX = e.touches[0]?.clientX ?? 0;
      st.touchFlipped = false;
      if (st.locked) cancel();
    };
    const onTouchMove = (e: TouchEvent) => {
      if (st.locked) {
        e.preventDefault();
        return;
      }
      const dy = st.touchY - (e.touches[0]?.clientY ?? 0);
      const dx = (e.touches[0]?.clientX ?? 0) - st.touchX;
      if (st.touchFlipped || Math.abs(dy) < SWIPE_MIN || Math.abs(dy) <= Math.abs(dx)) return;
      st.touchFlipped = true; // 一次手势只翻一页
      if (flip(dy > 0 ? 1 : -1)) e.preventDefault();
    };

    window.addEventListener("wheel", onWheel, { passive: false });
    window.addEventListener("touchstart", onTouchStart, { passive: true });
    window.addEventListener("touchmove", onTouchMove, { passive: false });
    return () => {
      window.removeEventListener("wheel", onWheel);
      window.removeEventListener("touchstart", onTouchStart);
      window.removeEventListener("touchmove", onTouchMove);
      window.clearTimeout(st.lockTimer);
    };
  }, []);
}
