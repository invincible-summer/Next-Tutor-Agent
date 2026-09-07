"use client";
// 账号与数据面板：统计瓦片 + 账号表（搜索/角色筛选/分页/整行展开占用明细）
// + 清空聊天（两种策略）与彻底删除（键入邮箱强确认）。功能与重构前一致。
import { Fragment, useCallback, useMemo, useState } from "react";
import { ChevronDown, Eraser, FileText, HardDrive, MessagesSquare, Search, Trash2, UsersRound } from "lucide-react";
import { clearAdminUserChat, purgeAdminUser, type AdminUser, type AdminUserStorage, type AdminUsersResponse } from "@/lib/api";
import { fmtBytes, relTime } from "@/lib/format";
import { useAuthStore } from "@/lib/auth-store";
import { useUIStore } from "@/lib/store";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { EmptyState, ErrorNote, Skeleton } from "@/components/ui/EmptyState";
import { ConfirmModal, Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { Pager, paged, pageCount } from "@/components/ui/Pager";
import { Stat } from "@/components/ui/Stat";
import { cn } from "@/lib/cn";
import { inputCls, type Tr } from "./Field";

const PER_PAGE = 10;

export function AccountsPanel({
  tr,
  users,
  summary,
  loading,
  error,
  refresh,
}: {
  tr: Tr;
  users: AdminUser[];
  summary: AdminUsersResponse["summary"];
  loading: boolean;
  error: string | null;
  refresh: () => void;
}) {
  const lang = useUIStore((s) => s.lang);
  const me = useAuthStore((s) => s.user);

  const [query, setQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState("all");
  const [page, setPage] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ text: string; tone: "ok" | "err" } | null>(null);
  const [clearTarget, setClearTarget] = useState<AdminUser | null>(null);
  const [clearScope, setClearScope] = useState<"all" | "uploads_only">("all");
  const [purgeTarget, setPurgeTarget] = useState<AdminUser | null>(null);
  const [busy, setBusy] = useState(false);

  const flashNotice = useCallback((text: string, tone: "ok" | "err") => {
    setNotice({ text, tone });
    setTimeout(() => setNotice(null), 5000);
  }, []);

  const handleClearChat = async () => {
    if (!clearTarget || busy) return;
    setBusy(true);
    try {
      const report = await clearAdminUserChat(clearTarget.id, clearScope);
      setClearTarget(null);
      flashNotice(tr("adm.users.notice.cleared").replace("{size}", fmtBytes(report.freed_bytes)), "ok");
      refresh();
    } catch (e) {
      flashNotice(withReason(tr("adm.users.notice.fail"), e), "err");
    } finally {
      setBusy(false);
    }
  };

  const handlePurge = async () => {
    if (!purgeTarget || busy) return;
    setBusy(true);
    try {
      const report = await purgeAdminUser(purgeTarget.id);
      setPurgeTarget(null);
      flashNotice(tr("adm.users.notice.purged").replace("{size}", fmtBytes(report.freed_bytes)), "ok");
      refresh();
    } catch (e) {
      flashNotice(withReason(tr("adm.users.notice.fail"), e), "err");
    } finally {
      setBusy(false);
    }
  };

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return users.filter((u) =>
      (roleFilter === "all" || u.role === roleFilter) &&
      (!q || u.email.toLowerCase().includes(q) || (u.username || "").toLowerCase().includes(q)));
  }, [users, query, roleFilter]);
  const cur = Math.min(page, pageCount(filtered.length, PER_PAGE) - 1);
  const rows = paged(filtered, cur, PER_PAGE);

  const totalSessions = users.reduce((n, u) => n + (u.storage?.session_count ?? 0), 0);
  const totalFiles = users.reduce((n, u) => n + (u.storage?.file_count ?? 0), 0);

  // 清理弹窗的预估释放：all 连会话/工作区/资料库一起算；uploads_only 以
  // 上传字节为下限（资料库数据另计，无法从分桶中拆出）。
  const clearEstimate = clearTarget?.storage
    ? (clearScope === "all"
        ? clearTarget.storage.chat_bytes + clearTarget.storage.uploads_bytes + clearTarget.storage.trash_bytes
        : clearTarget.storage.uploads_bytes)
    : 0;

  const bucketRows = (st: AdminUserStorage) => ([
    [tr("adm.users.storage.chat"), fmtBytes(st.chat_bytes)],
    [tr("adm.users.storage.uploads"), fmtBytes(st.uploads_bytes)],
    [tr("adm.users.storage.notes"), fmtBytes(st.notes_bytes)],
    [tr("adm.users.storage.students"), fmtBytes(st.students_bytes)],
    [tr("adm.users.storage.knowledge"), fmtBytes(st.knowledge_bytes)],
    [tr("adm.users.storage.trashB"), fmtBytes(st.trash_bytes)],
    [tr("adm.users.storage.sessions"), String(st.session_count)],
    [tr("adm.users.storage.files"), String(st.file_count)],
  ] as const);

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Stat icon={<UsersRound size={16} />} label={tr("adm.stat.accounts")} value={summary.count} />
        <Stat icon={<HardDrive size={16} />} label={tr("adm.stat.total")} value={fmtBytes(summary.total_bytes)} tone="accent" />
        <Stat icon={<MessagesSquare size={16} />} label={tr("adm.stat.sessions")} value={totalSessions} />
        <Stat icon={<FileText size={16} />} label={tr("adm.stat.files")} value={totalFiles} />
      </div>

      {loading && <Skeleton className="h-64" />}
      {!loading && error && <ErrorNote message={error} retry={refresh} />}
      {!loading && !error && users.length === 0 && (
        <EmptyState icon={<UsersRound size={28} />} title={tr("adm.users.empty")} />
      )}

      {!loading && !error && users.length > 0 && (
        <Card pad={false} className="overflow-x-auto">
          <div className="px-4 pt-4">
            <CardHeader icon={<UsersRound size={16} />} title={tr("adm.users.title")} desc={tr("adm.users.desc")} />
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <div className="relative">
                <Search size={12} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-muted" />
                <input
                  value={query}
                  onChange={(e) => { setQuery(e.target.value); setPage(0); setOpenId(null); }}
                  placeholder={tr("adm.users.search")}
                  className={`${inputCls} w-52 pl-7`}
                />
              </div>
              <select
                value={roleFilter}
                onChange={(e) => { setRoleFilter(e.target.value); setPage(0); setOpenId(null); }}
                className={`${inputCls} w-32`}
              >
                {["all", "student", "parent", "teacher", "admin"].map((r) => (
                  <option key={r} value={r}>{tr(`adm.users.role.${r}`)}</option>
                ))}
              </select>
              <span className="tnum ml-auto text-xs text-muted">
                {tr("adm.users.summary")
                  .replace("{count}", String(summary.count))
                  .replace("{size}", fmtBytes(summary.total_bytes))}
              </span>
            </div>
            {notice && (
              <div className={`mt-2 rounded-[8px] border px-3 py-1.5 text-xs ${
                notice.tone === "ok" ? "border-accent/40 text-accent" : "border-danger/40 text-danger"}`}>
                {notice.text}
              </div>
            )}
          </div>
          {rows.length === 0 ? (
            <div className="px-4 pb-4 pt-2 text-xs text-muted">{tr("adm.users.noMatch")}</div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-y border-border-light bg-surface-sunken text-left text-xs text-muted">
                  <th className="px-4 py-2.5 font-medium">{tr("adm.users.email")}</th>
                  <th className="px-4 py-2.5 font-medium">{tr("adm.users.username")}</th>
                  <th className="px-4 py-2.5 font-medium">{tr("adm.users.role")}</th>
                  <th className="px-4 py-2.5 font-medium">{tr("adm.users.created")}</th>
                  <th className="px-4 py-2.5 font-medium">{tr("adm.users.lastLogin")}</th>
                  <th className="px-4 py-2.5 font-medium">{tr("adm.users.storage")}</th>
                  <th className="px-4 py-2.5 font-medium">{tr("adm.users.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((u) => {
                  const isSelf = me?.id === u.id;
                  const isTargetAdmin = u.role === "admin";
                  const open = openId === u.id;
                  return (
                    <Fragment key={u.id}>
                      <tr
                        onClick={() => setOpenId(open ? null : u.id)}
                        className={cn("cursor-pointer border-b border-border-light transition-colors hover:bg-surface-hover",
                          open && "bg-surface-hover")}>
                        <td className="px-4 py-2.5 text-fg">
                          {u.email}
                          {isSelf && <span className="text-muted">{tr("adm.users.self")}</span>}
                        </td>
                        <td className="px-4 py-2.5 text-fg-secondary">{u.username || "—"}</td>
                        <td className="px-4 py-2.5">
                          {isTargetAdmin ? (
                            <Badge tone="accent">{tr("adm.users.adminBadge")}</Badge>
                          ) : (
                            <span className="text-fg-secondary">{u.role}</span>
                          )}
                        </td>
                        <td className="tnum px-4 py-2.5 text-xs text-muted">{relTime(u.created_at, lang)}</td>
                        <td className="tnum px-4 py-2.5 text-xs text-muted">
                          {u.last_login_at ? relTime(u.last_login_at, lang) : tr("adm.users.never")}
                        </td>
                        <td className="px-4 py-2.5">
                          <span className="inline-flex items-center gap-1 text-xs text-fg-secondary">
                            <span className="tnum">{fmtBytes(u.storage?.total_bytes ?? 0)}</span>
                            <ChevronDown size={12} className={cn("text-muted transition-transform", open && "rotate-180")} />
                          </span>
                        </td>
                        <td className="px-4 py-2.5">
                          <div className="flex gap-1.5">
                            <Button size="sm" variant="outline" icon={<Eraser size={12} />}
                              className="hover:border-danger hover:text-danger"
                              onClick={(e) => { e.stopPropagation(); setClearTarget(u); setClearScope("all"); }}>
                              {tr("adm.users.clearChat")}
                            </Button>
                            {!isTargetAdmin && !isSelf && (
                              <Button size="sm" variant="danger" icon={<Trash2 size={12} />}
                                onClick={(e) => { e.stopPropagation(); setPurgeTarget(u); }}>
                                {tr("adm.users.purge")}
                              </Button>
                            )}
                          </div>
                        </td>
                      </tr>
                      {open && (
                        <tr className="border-b border-border-light bg-surface-sunken/50">
                          <td colSpan={7} className="px-4 py-3">
                            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                              {bucketRows(u.storage ?? emptyStorage()).map(([k, v]) => (
                                <div key={k} className="flex items-center justify-between gap-3 rounded-[8px] border border-border-light bg-surface px-3 py-1.5">
                                  <span className="text-xs text-muted">{k}</span>
                                  <span className="tnum text-xs font-medium text-fg">{v}</span>
                                </div>
                              ))}
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          )}
          <Pager page={cur} total={filtered.length} per={PER_PAGE} onPage={setPage} className="px-4 pb-3" />
        </Card>
      )}

      <Modal
        open={clearTarget !== null}
        onClose={() => setClearTarget(null)}
        title={tr("adm.users.clearChat.title")}
        width={480}
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setClearTarget(null)}>
              {tr("adm.users.cancel")}
            </Button>
            <Button variant="danger" size="sm" disabled={busy} onClick={() => void handleClearChat()}>
              {tr("adm.users.clearChat.confirm")}
            </Button>
          </>
        }
      >
        <p className="text-xs text-muted">{clearTarget?.email ?? ""}</p>
        <div className="mt-3 space-y-2">
          {(["all", "uploads_only"] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setClearScope(s)}
              className={`w-full rounded-[8px] border p-3 text-left transition-colors ${
                clearScope === s ? "border-danger bg-surface-sunken" : "border-border hover:border-danger/50"}`}
            >
              <span className="text-sm font-medium text-fg">{tr(`adm.users.clearChat.${s}`)}</span>
              <p className="mt-1 text-xs leading-relaxed text-fg-secondary">{tr(`adm.users.clearChat.${s}.desc`)}</p>
            </button>
          ))}
        </div>
        {clearEstimate > 0 && (
          <p className="mt-3 text-xs text-muted">
            {tr("adm.users.clearChat.estimate").replace("{size}", fmtBytes(clearEstimate))}
          </p>
        )}
      </Modal>
      <ConfirmModal
        open={purgeTarget !== null}
        onClose={() => setPurgeTarget(null)}
        onConfirm={() => void handlePurge()}
        title={tr("adm.users.purgeTitle")}
        desc={
          <div>
            <p>{tr("adm.users.purgeDesc")}</p>
            <p className="mt-2 text-sm font-medium text-fg">{purgeTarget?.email ?? ""}</p>
            {purgeTarget?.storage && purgeTarget.storage.total_bytes > 0 && (
              <p className="mt-1 text-xs text-muted">
                {tr("adm.users.clearChat.estimate").replace("{size}", fmtBytes(purgeTarget.storage.total_bytes))}
              </p>
            )}
          </div>
        }
        confirmPhrase={purgeTarget?.email}
        confirmText={tr("adm.users.purgeConfirm")}
        cancelText={tr("adm.users.cancel")}
      />
    </div>
  );
}

/** 失败通知附上后端可见的原因（api 抛错 message 含 HTTP 状态）。 */
function withReason(base: string, e: unknown): string {
  const detail = e instanceof Error ? e.message : "";
  return detail ? `${base}（${detail}）` : base;
}

/** 无数据账号的空分桶（展开明细时兜底）。 */
function emptyStorage(): AdminUserStorage {
  return { chat_bytes: 0, uploads_bytes: 0, notes_bytes: 0, students_bytes: 0,
    knowledge_bytes: 0, trash_bytes: 0, total_bytes: 0, session_count: 0, file_count: 0 };
}
