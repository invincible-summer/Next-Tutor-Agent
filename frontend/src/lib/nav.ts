import {
  Activity,
  Archive,
  Brain,
  ClipboardCheck,
  FolderOpen,
  LayoutDashboard,
  MessageSquareText,
  Network,
  NotebookPen,
  Presentation,
  ShieldCheck,
  Target,
  UserRound,
  Settings,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  i18nKey: string;
  icon: LucideIcon;
  /** 篆刻式模块徽章（M1–M8 / RAG），为空则不显示 */
  module?: string;
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
      { href: "/chat", i18nKey: "nav.chat", icon: MessageSquareText, module: "M1" },
      { href: "/course", i18nKey: "nav.course", icon: Presentation, classroomOnly: true },
      { href: "/notes", i18nKey: "nav.notes", icon: NotebookPen, module: "MN" },
      { href: "/dashboard", i18nKey: "nav.dashboard", icon: LayoutDashboard, module: "M2" },
      { href: "/knowledge", i18nKey: "nav.knowledge", icon: Network, module: "M5" },
      { href: "/orchestration", i18nKey: "nav.orchestration", icon: Target, module: "M9" },
      { href: "/assessment", i18nKey: "nav.assessment", icon: ClipboardCheck, module: "M4" },
    ],
  },
  {
    i18nKey: "nav.group.archive",
    items: [
      { href: "/memory", i18nKey: "nav.memory", icon: Brain, module: "M6" },
      { href: "/resources", i18nKey: "nav.resources", icon: FolderOpen, module: "RAG" },
      { href: "/archive", i18nKey: "nav.archive", icon: Archive },
    ],
  },
  {
    i18nKey: "nav.group.system",
    items: [
      { href: "/admin", i18nKey: "nav.admin", icon: ShieldCheck, module: "M0", adminOnly: true },
    ],
  },
];

export const ACCOUNT_NAV: NavItem[] = [
  { href: "/account", i18nKey: "nav.account", icon: UserRound },
  { href: "/profile", i18nKey: "nav.profile", icon: UserRound },
  { href: "/insights", i18nKey: "nav.insights", icon: Activity },
  { href: "/settings", i18nKey: "settings.title", icon: Settings },
];

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
