"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import { UserRound } from "lucide-react";
import { getUserAvatar } from "@/lib/api-modules";
import { type AuthUser, useAuthStore } from "@/lib/auth-store";
import { cn } from "@/lib/cn";

/** Authenticated fetch + ephemeral blob URL; never exposes a public avatar URL. */
export function UserAvatar({ user, className }: { user: AuthUser | null; className?: string }) {
  const [image, setImage] = useState<{ key: string; url: string } | null>(null);
  const key = `${user?.id}:${user?.profile.avatar}`;
  const owner = user?.id;
  const revision = user?.profile.avatar;
  useEffect(() => {
    if (!owner || !revision?.startsWith("avatar:")) return;
    const controller = new AbortController();
    let url: string | undefined;
    getUserAvatar(revision, controller.signal).then((blob) => {
      if (controller.signal.aborted || useAuthStore.getState().user?.id !== owner) return;
      url = URL.createObjectURL(blob);
      setImage({ key, url });
    }).catch(() => { /* Keep the initials if the private image is unavailable. */ });
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [key, owner, revision]);
  return <span aria-hidden="true" className={cn("relative flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded-full bg-accent-soft text-accent", className)}>
    {image?.key === key ? <Image src={image.url} alt="" fill unoptimized sizes="56px" className="object-cover" />
      : user ? (user.profile.name || user.username || user.email).slice(0, 1).toUpperCase() : <UserRound size={16} />}
  </span>;
}
