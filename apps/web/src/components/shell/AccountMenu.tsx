"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ChevronDown, LogIn, LogOut } from "lucide-react";
import { UserAvatar } from "@/components/auth/UserAvatar";
import { useAuthStore } from "@/lib/auth-store";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { STRINGS } from "@/app/(workspace)/settings/strings";
import { ACCOUNT_NAV } from "@/lib/nav";

export function AccountMenu() {
  const { user, logout } = useAuthStore();
  const lang = useUIStore((s) => s.lang);
  const [open, setOpen] = useState(false);
  const [returnTo, setReturnTo] = useState("/chat");
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const tr = makePageT(lang, STRINGS);
  const openMenu = () => {
    setReturnTo(window.location.pathname + window.location.search);
    setOpen(true);
  };
  const close = () => { setOpen(false); trigger.current?.focus(); };

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    root.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);

  return <div ref={root} className="relative ml-1" onBlur={(event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
  }} onKeyDown={(event) => {
    if (event.key === "Escape") { event.preventDefault(); close(); }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    if (!open) { openMenu(); return; }
    const items = Array.from(root.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    const index = items.indexOf(document.activeElement as HTMLElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  }}>
    <button ref={trigger} type="button" aria-haspopup="menu" aria-expanded={open} aria-controls="account-menu"
      onClick={() => { if (open) close(); else openMenu(); }} className="flex h-8 items-center gap-1.5 rounded-full px-2 text-xs font-medium text-fg-secondary hover:bg-surface-hover focus-visible:outline-2 focus-visible:outline-accent">
      <UserAvatar user={user} className="h-5! w-5!" />
      {tr("account.menu")}<ChevronDown size={13} aria-hidden="true" />
    </button>
    {open && <div id="account-menu" role="menu" aria-label={tr("account.menu")} className="motion-pop absolute right-0 top-11 z-50 w-[280px] rounded-xl border border-border bg-surface p-1.5 shadow-lg">
      <div className="border-b border-border-light px-3 py-3">
        <p className="truncate text-sm font-semibold text-fg">{user ? user.profile.name || user.username : tr("account.guest")}</p>
        <p className="mt-1 truncate text-xs text-muted">{user?.email || (tr("account.notLoggedIn"))}</p>
        {user && <p className="mt-1 text-[11px] text-muted">{tr(user.role === "admin" ? "account.role.admin" : "account.role.user")}</p>}
      </div>
      <div className="py-1">
        {ACCOUNT_NAV.filter((item) => user && item.href !== "/settings").map(({ href, i18nKey, icon: Icon }) => <Link key={href} href={href} role="menuitem" onClick={close}
          className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-xs text-fg-secondary hover:bg-surface-hover focus-visible:bg-surface-hover focus:outline-none">
          <Icon size={16} aria-hidden="true" />{tr(i18nKey)}<span className="ml-auto text-muted" aria-hidden="true">›</span>
        </Link>)}
      </div>
      <div className="border-t border-border-light pt-1">
        {user ? <button role="menuitem" onClick={() => { close(); logout(); }} className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-xs text-danger hover:bg-surface-hover focus-visible:bg-surface-hover focus:outline-none"><LogOut size={16} aria-hidden="true" />{tr("auth.logout")}</button>
          : <Link role="menuitem" href={`/login?redirect=${encodeURIComponent(returnTo)}`} onClick={close} className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-xs text-accent hover:bg-surface-hover focus-visible:bg-surface-hover focus:outline-none"><LogIn size={16} aria-hidden="true" />{tr("account.login")}</Link>}
      </div>
    </div>}
  </div>;
}
