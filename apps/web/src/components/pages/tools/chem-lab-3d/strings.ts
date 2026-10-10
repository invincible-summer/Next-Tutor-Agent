/**
 * 化学实验台 3D（新）页面词条。纯前端娱乐玩具文案——不出现评分/目标/指导。
 */
import type { PageStrings } from "@/lib/i18n-page";

export const STRINGS: PageStrings = {
  zh: {
    "chem3d.back": "返回",
    "chem3d.backToCatalog": "返回关卡目录",
    "chem3d.stageMissing": "关卡不存在",
    "chem3d.stageMissingHint": "这个关卡不在目录里，请重新选择。",
    "chem3d.webglFailed": "当前浏览器不支持 WebGL2",
    "chem3d.webglFailedHint": "化学实验台需要 WebGL2 才能显示立体场景；请换用支持的浏览器或设备。",
    "chem3d.contextLost": "图形上下文中断，正在尝试恢复…",
    "chem3d.hint.firstVisit": "拖动仪器摆放 · 点接口接导管 · 或按 AUTO 一键拼好",
    "chem3d.loading": "正在搭建实验台…",
  },
  en: {
    "chem3d.back": "Back",
    "chem3d.backToCatalog": "Back to stages",
    "chem3d.stageMissing": "Stage not found",
    "chem3d.stageMissingHint": "This stage is not in the catalog. Please pick another one.",
    "chem3d.webglFailed": "WebGL2 is not available",
    "chem3d.webglFailedHint": "The chemistry bench needs WebGL2 to render its 3D scene. Try a browser that supports it.",
    "chem3d.contextLost": "Graphics context lost, trying to recover…",
    "chem3d.hint.firstVisit": "Drag equipment · click ports to connect tubes · or press AUTO",
    "chem3d.loading": "Setting up the bench…",
  },
};
