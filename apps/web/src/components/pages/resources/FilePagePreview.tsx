"use client";
import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";
import { apiFetch } from "@/lib/api-fetch";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import type { Lang } from "@/lib/i18n";
import { DEMO_MODE } from "@/lib/demo";

/** PDF 原页经鉴权请求取回，图片解码完成后才发布文件+页码定位凭据。 */
export function FilePagePreview({ fileId, filename, page, lang, onClose, onPage }: {
  fileId: string; filename: string; page: string; lang: Lang;
  onClose: () => void; onPage: (page: number) => void;
}) {
  const [url, setUrl] = useState("");
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const number = Number(page);
  const valid = /^\d+$/.test(page) && Number.isSafeInteger(number) && number >= 1 && number <= 5000;
  const en = lang === "en";
  useEffect(() => {
    if (!valid) return;
    const controller = new AbortController();
    let objectUrl = "";
    apiFetch(DEMO_MODE ? `${API_BASE}/library/files/${encodeURIComponent(fileId)}/download` : `${API_BASE}/library/files/${encodeURIComponent(fileId)}/page/${number}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(String(response.status));
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => { if (!controller.signal.aborted) setError(en ? "Page unavailable: the file may be missing or the page is out of range." : "无法预览：文件已不可用或页码超出范围。"); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [fileId, number, valid, en]);
  return <Modal open onClose={onClose} width={820} title={`${filename} · ${en ? "Page" : "第"} ${page}${en ? "" : " 页"}`}
    footer={<>
      <Button variant="ghost" disabled={!valid || number <= 1} onClick={() => onPage(number - 1)}>{en ? "Previous" : "上一页"}</Button>
      <Button variant="ghost" disabled={!ready} onClick={() => onPage(number + 1)}>{en ? "Next" : "下一页"}</Button>
      <Button onClick={onClose}>{en ? "Close" : "关闭"}</Button>
    </>}>
    <div data-file-preview={fileId} data-page={page} data-preview-state={!valid || error ? "failed" : ready ? "ready" : "loading"}>
      {(!valid || error) ? <p role="alert">{error || (en ? "Invalid page number." : "页码无效，请使用 1–5000 的整数。")}</p> : <>
        {!ready && <p role="status">{en ? "Loading page…" : "正在加载原页…"}</p>}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        {url && (DEMO_MODE ? <iframe src={`${url}#page=${number}`} title={`${filename} — ${page}`} className="h-[65vh] w-full" onLoad={() => setReady(true)} /> : <img src={url} alt={`${filename} — ${page}`} className="h-auto w-full" onLoad={() => setReady(true)} onError={() => setError(en ? "Unable to display page." : "无法显示原页。")} />)}
      </>}
    </div>
  </Modal>;
}
