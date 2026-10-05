import type { LucideIcon } from "lucide-react-native";
import {
  FolderOpen,
  GraduationCap,
  House,
  MessageSquareText,
  UserRound,
} from "lucide-react-native";

/** 一级导航：Compact 底部 5 项，Medium+ 同一逻辑入口进 NavRail。 */
export interface NavItem {
  /** (main) 下的 route name。 */
  name: string;
  i18nKey: string;
  icon: LucideIcon;
}

export const NAV_ITEMS: NavItem[] = [
  { name: "index", i18nKey: "nav.home", icon: House },
  { name: "tutor", i18nKey: "nav.tutor", icon: MessageSquareText },
  { name: "learn", i18nKey: "nav.learn", icon: GraduationCap },
  { name: "library", i18nKey: "nav.library", icon: FolderOpen },
  { name: "me", i18nKey: "nav.me", icon: UserRound },
];
