"use client";

// OverlayCoordinator（GAP-08）：层级与 Escape 协作。
// 层级：主页面 0（现有值）· 助手 40 · 全局 Modal/Drawer 50 · 轻提示 60。
// 相同层级有先后时按打开顺序判断最上层，不只依赖 z-index；高层
// Modal/Drawer 打开时助手保留状态但入口位于遮罩之后。
export type OverlayKind = "modal" | "drawer" | "assistant-panel" | "toast";

export interface OverlayRegistration {
  id: string;
  kind: OverlayKind;
  layer: number; // 40 | 50 | 60
  onClose?: () => void; // Escape 语义（仅最上层调用）
}

interface Entry extends OverlayRegistration {
  openedAt: number;
}

const _stack: Entry[] = [];
let _seq = 0;

export const ASSISTANT_LAYER = 40;
export const MODAL_LAYER = 50;
export const TOAST_LAYER = 60;

/** 注册打开的覆盖层；返回注销函数。 */
export function registerOverlay(reg: OverlayRegistration): () => void {
  const entry: Entry = { ...reg, openedAt: _seq++ };
  _stack.push(entry);
  return () => {
    const index = _stack.indexOf(entry);
    if (index >= 0) _stack.splice(index, 1);
  };
}

export function isTopmost(id: string): boolean {
  // 同层按打开顺序，最后打开者最上；高层永远压低层。
  const entry = _stack.find((e) => e.id === id);
  if (!entry) return true; // 未注册者保持既有行为
  const higher = _stack.filter(
    (e) => e.layer > entry.layer || (e.layer === entry.layer && e.openedAt > entry.openedAt));
  return higher.length === 0;
}

/** 助手是否被更高层遮挡（此时入口不可交互）。 */
export function hasHigherOverlay(): boolean {
  const assistant = _stack.filter((e) => e.kind === "assistant-panel");
  if (!assistant.length) {
    return _stack.some((e) => e.layer >= MODAL_LAYER);
  }
  return assistant.some((e) => !isTopmost(e.id));
}

/** Escape 顶层分发：只关最上层，不同时关闭多层（AC-30）。 */
export function handleEscape(): boolean {
  if (!_stack.length) return false;
  const top = [..._stack].sort(
    (a, b) => b.layer - a.layer || b.openedAt - a.openedAt)[0];
  top.onClose?.();
  return true;
}

/** 测试辅助。 */
export function resetOverlayStack(): void {
  _stack.length = 0;
}
