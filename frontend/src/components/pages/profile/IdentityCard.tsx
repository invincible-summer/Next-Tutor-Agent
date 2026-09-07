import { UserRound } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { useAuthStore } from "@/lib/auth-store";
import type { Lang } from "@/lib/i18n";
import { relTime } from "@/lib/format";
import type { StudentProfileData } from "@/lib/types-modules";

type Tr = (key: string, fallback?: string) => string;

/** 顶部身份卡：头像位 + 身份名 + 学段/学科徽章 + 活跃信息。
 *  登录态标题显示账户名（M2 的内部 student_id「usr_xxx」不是给人看的身份，
 *  直接当标题渲染会被误认为"另一个用户"）；游客态没有账户，才回退显示 id。 */
export function IdentityCard({ profile, lang, tr }: { profile: StudentProfileData; lang: Lang; tr: Tr }) {
  const user = useAuthStore((s) => s.user);
  const displayName = user
    ? user.profile.name || user.username || user.email
    : profile.id;
  return (
    <Card className="flex items-center gap-4">
      <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent">
        <UserRound size={28} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-serif text-lg font-semibold text-fg">{displayName}</span>
          {profile.grade && <Badge tone="accent">{profile.grade}</Badge>}
          {profile.subjects.map((s) => (
            <Badge key={s} tone="outline">
              {s}
            </Badge>
          ))}
        </div>
        <div className="mt-1 text-xs text-muted">
          {tr("profile.lastActive")}{" "}
          {profile.last_active ? relTime(profile.last_active, lang) : tr("profile.never")}
          <span className="mx-1.5 text-border">·</span>
          <span className="tnum">{profile.events_processed}</span> {tr("profile.events")}
        </div>
      </div>
    </Card>
  );
}
