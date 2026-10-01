"use client";

import { useEffect, useRef, useState } from "react";
import { Camera, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Modal } from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import { deleteUserAvatar, saveUserAvatar } from "@/lib/api-modules";
import { useAuthStore } from "@/lib/auth-store";
import { useUIStore } from "@/lib/store";
import { makePageT } from "@/lib/i18n-page";
import { STRINGS } from "@/app/(workspace)/settings/strings";

const SIZE = 256;
const clamp = (value: number) => Math.max(-1, Math.min(1, value));

export function AvatarEditor({ onDirtyChange }: { onDirtyChange: (value: boolean) => void }) {
  const user = useAuthStore((s) => s.user);
  const lang = useUIStore((s) => s.lang);
  const tr = makePageT(lang, STRINGS);
  const notify = useToast();
  const fileInput = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const request = useRef(0);
  const drag = useRef<{ x: number; y: number; position: { x: number; y: number } } | null>(null);
  const [image, setImage] = useState<ImageBitmap | null>(null);
  const [zoom, setZoom] = useState(1);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<"failed" | "invalid" | null>(null);

  useEffect(() => () => { request.current++; }, []);
  useEffect(() => () => image?.close(), [image]);
  useEffect(() => {
    onDirtyChange(!!image || saving || loading);
    return () => onDirtyChange(false);
  }, [image, saving, loading, onDirtyChange]);
  useEffect(() => {
    if (!image || !canvas.current) return;
    const side = Math.min(image.width, image.height) / zoom;
    const x = (image.width - side) * (position.x + 1) / 2;
    const y = (image.height - side) * (position.y + 1) / 2;
    const context = canvas.current.getContext("2d");
    context?.clearRect(0, 0, SIZE, SIZE);
    context?.drawImage(image, x, y, side, side, 0, 0, SIZE, SIZE);
  }, [image, zoom, position]);

  if (!user) return null;
  const close = () => {
    if (!saving && (!image || window.confirm(tr("settings.unsaved")))) setImage(null);
  };
  const chooseFile = async (file?: File) => {
    if (!file) return;
    setMessage(null);
    if (!file.size || file.size > 5 * 1024 * 1024 || !["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      setMessage("invalid"); return;
    }
    const sequence = ++request.current;
    setLoading(true);
    try {
      const decoded = await createImageBitmap(file, { imageOrientation: "from-image" });
      if (sequence !== request.current || useAuthStore.getState().user?.id !== user.id) { decoded.close(); return; }
      if (decoded.width * decoded.height > 20_000_000) { decoded.close(); setMessage("invalid"); return; }
      setZoom(1); setPosition({ x: 0, y: 0 }); setImage(decoded);
    } catch { setMessage("invalid"); }
    finally { if (sequence === request.current) setLoading(false); }
  };
  const save = async (remove = false) => {
    if (saving) return;
    setSaving(true); setMessage(null);
    try {
      let profile;
      if (remove) profile = await deleteUserAvatar();
      else {
        const blob = await new Promise<Blob | null>((resolve) => { if (canvas.current) canvas.current.toBlob(resolve, "image/png"); else resolve(null); });
        if (!blob) throw new Error("avatar_crop_failed");
        profile = await saveUserAvatar(blob);
      }
      useAuthStore.setState((state) => state.user?.id === user.id ? { user: { ...state.user, profile } } : {});
      setImage(null); notify(tr("settings.saved"));
    } catch { setMessage("failed"); }
    finally { setSaving(false); }
  };
  return <>
    <div ref={fileInput} className="mt-4 flex flex-wrap items-center gap-2 border-t border-border-light pt-3">
      <Input type="file" accept="image/png,image/jpeg,image/webp" aria-label={tr("avatar.upload")} className="sr-only" tabIndex={-1}
        onChange={(event) => { void chooseFile(event.target.files?.[0]); event.target.value = ""; }} />
      <Button variant="outline" size="sm" icon={<Camera size={13} />} disabled={saving || loading} onClick={() => fileInput.current?.querySelector<HTMLInputElement>("input[type=file]")?.click()}>{tr(loading ? "settings.loading" : "avatar.upload")}</Button>
      {user.profile.avatar?.startsWith("avatar:") && <Button variant="ghost" size="sm" disabled={saving || loading} onClick={() => void save(true)}>{tr("avatar.remove")}</Button>}
      {message && <p role="status" className="text-xs text-danger">{tr(message === "invalid" ? "avatar.invalid" : "settings.saveFailed")}</p>}
    </div>
    <Modal open={!!image} onClose={close} width={520} title={tr("avatar.crop")} footer={<><Button variant="ghost" size="sm" disabled={saving} onClick={close}>{tr("avatar.cancel")}</Button><Button size="sm" disabled={saving} onClick={() => void save()}>{tr(saving ? "settings.saving" : "avatar.save")}</Button></>}>
      <p className="mb-4 text-xs text-muted">{tr("avatar.hint")}</p>
      <div className="mx-auto w-64 max-w-full overflow-hidden rounded-xl bg-surface-sunken p-0.5">
        <canvas ref={canvas} width={SIZE} height={SIZE} role="img" aria-label={tr("avatar.preview")}
          className="block aspect-square w-full touch-none cursor-grab rounded-full active:cursor-grabbing"
          onPointerDown={(event) => { if (saving) return; event.currentTarget.setPointerCapture(event.pointerId); drag.current = { x: event.clientX, y: event.clientY, position }; }}
          onPointerMove={(event) => {
            if (!image || !drag.current || saving) return;
            const side = Math.min(image.width, image.height) / zoom;
            const width = event.currentTarget.getBoundingClientRect().width;
            const dx = (event.clientX - drag.current.x) * side / width;
            const dy = (event.clientY - drag.current.y) * side / width;
            setPosition({ x: clamp(drag.current.position.x - dx * 2 / Math.max(1, image.width - side)), y: clamp(drag.current.position.y - dy * 2 / Math.max(1, image.height - side)) });
          }} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }} />
      </div>
      <div className="mt-4 space-y-3">
        {[{ label: "avatar.zoom", value: zoom, min: 1, max: 3, change: (value: number) => setZoom(value) },
          { label: "avatar.horizontal", value: position.x, min: -1, max: 1, change: (value: number) => setPosition({ ...position, x: value }) },
          { label: "avatar.vertical", value: position.y, min: -1, max: 1, change: (value: number) => setPosition({ ...position, y: value }) }].map((field) => <label key={field.label} className="flex items-center gap-3 text-xs"><span className="w-20 shrink-0">{tr(field.label)}</span><Input type="range" min={field.min} max={field.max} step={0.01} value={field.value} disabled={saving} className="h-4 flex-1 border-0 bg-transparent p-0 accent-accent" onChange={(event) => field.change(Number(event.target.value))} /></label>)}
      </div>
      <Button variant="ghost" size="sm" className="mt-3" disabled={saving} icon={<RotateCcw size={12} />} onClick={() => { setZoom(1); setPosition({ x: 0, y: 0 }); }}>{tr("avatar.reset")}</Button>
      {message === "failed" && <p role="alert" className="mt-3 text-xs text-danger">{tr("settings.saveFailed")}</p>}
    </Modal>
  </>;
}
