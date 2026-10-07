import {
  Activity,
  Archive,
  Brain,
  ClipboardCheck,
  FolderOpen,
  LayoutDashboard,
  MessageSquareText,
  Network,
  Shapes,
  NotebookPen,
  Presentation,
  ShieldCheck,
  Target,
  UserRound,
  Settings,
  Wrench,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  i18nKey: string;
  icon: LucideIcon;
  /** 仅 role=admin 显示（P6-B4 管理端入口） */
  adminOnly?: boolean;
  /** 依赖课堂特性开关（CLASSROOM_ENABLED），关闭时隐藏 */
  classroomOnly?: boolean;
}

export interface NavGroup {
  i18nKey: string;
  items: NavItem[];
}

/** 学习工作区主导航：八层智能模块的窗口。 */
export const NAV: NavGroup[] = [
  {
    i18nKey: "nav.group.learn",
    items: [
      { href: "/chat", i18nKey: "nav.chat", icon: MessageSquareText },
      { href: "/course", i18nKey: "nav.course", icon: Presentation, classroomOnly: true },
      { href: "/notes", i18nKey: "nav.notes", icon: NotebookPen },
      { href: "/dashboard", i18nKey: "nav.dashboard", icon: LayoutDashboard },
      { href: "/knowledge", i18nKey: "nav.knowledge", icon: Network },
      { href: "/orchestration", i18nKey: "nav.orchestration", icon: Target },
      { href: "/assessment", i18nKey: "nav.assessment", icon: ClipboardCheck },
    ],
  },
  {
    i18nKey: "nav.group.archive",
    items: [
      { href: "/memory", i18nKey: "nav.memory", icon: Brain },
      { href: "/resources", i18nKey: "nav.resources", icon: FolderOpen },
      { href: "/tools", i18nKey: "nav.tools", icon: Wrench },
      { href: "/diagram-library", i18nKey: "nav.diagramLibrary", icon: Shapes },
      { href: "/archive", i18nKey: "nav.archive", icon: Archive },
    ],
  },
  {
    i18nKey: "nav.group.system",
    items: [
      { href: "/admin", i18nKey: "nav.admin", icon: ShieldCheck, adminOnly: true },
    ],
  },
];

export const ACCOUNT_NAV: NavItem[] = [
  { href: "/account", i18nKey: "nav.account", icon: UserRound },
  { href: "/profile", i18nKey: "nav.profile", icon: UserRound },
  { href: "/insights", i18nKey: "nav.insights", icon: Activity },
  { href: "/settings", i18nKey: "settings.title", icon: Settings },
];

/** 二级页面标题：TopBar 以「一级 · 二级」复合标题显示（键为路由前缀，值为 i18n key）。 */
export const NAV_SUBTITLES: Record<string, string> = {
  "/tools/illustration": "nav.toolsIllustration",
  "/tools/worksheet": "nav.toolsWorksheet",
};

/** 由路径反查当前导航项（TopBar 标题用）。 */
export function navItemByPath(pathname: string): NavItem | null {
  if (/^\/workspaces\/[^/]+\/classroom(?:\/|$)/.test(pathname)) {
    return NAV.flatMap((g) => g.items).find((item) => item.href === "/course") ?? null;
  }
  const account = ACCOUNT_NAV.find((item) => pathname === item.href || pathname.startsWith(item.href + "/"));
  if (account) return account;
  for (const g of NAV) {
    for (const it of g.items) {
      if (pathname === it.href || pathname.startsWith(it.href + "/")) return it;
    }
  }
  return null;
}
