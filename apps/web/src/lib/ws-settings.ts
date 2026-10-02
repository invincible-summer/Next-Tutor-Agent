// 工作区设置弹窗的全局开关 + 变更广播。
// 弹窗挂载在 AppShell（全局唯一），边栏/资料中心等任意入口通过 open() 唤起；
// 保存成功后 dispatch WS_CHANGED_EVENT，各列表自行监听刷新（解耦）。
import { create } from "zustand";

/** "new" = 创建模式；否则为要编辑的工作区 id；null = 关闭。 */
export type WsSettingsTarget = "new" | string | null;

interface WsSettingsState {
  target: WsSettingsTarget;
  open: (target: "new" | string) => void;
  close: () => void;
}

export const useWsSettings = create<WsSettingsState>((set) => ({
  target: null,
  open: (target) => set({ target }),
  close: () => set({ target: null }),
}));

export const WS_CHANGED_EVENT = "edu-agent:ws-changed";
export const SESSION_CHANGED_EVENT = "edu-agent:session-changed";

export function notifyWsChanged(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(WS_CHANGED_EVENT));
  }
}

export function notifySessionChanged(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(SESSION_CHANGED_EVENT));
  }
}
