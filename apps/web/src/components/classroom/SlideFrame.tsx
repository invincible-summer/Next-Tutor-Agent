"use client";
/* 课堂课件 iframe 宿主。
 *
 * 父页面用 apiFetch 带 JWT 读取自包含 HTML，再设置 iframe.srcdoc；
 * 不给 <iframe src> 塞带 token 的 query。iframe 只授予
 * sandbox="allow-scripts"。握手：等待 frame 发来 classroom_ready{nonce}
 * （校验 event.source === iframe.contentWindow），回发 init + MessagePort；
 * 之后所有指令/事件只走该端口。卸载立即 close。
 */
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
} from "react";

import { PRESENTATION_STYLE } from "./presentation-style";

export interface FrameBlockMeasure {
  id: string;
  top: number;
  left: number;
  right: number;
  bottom: number;
  overflow: boolean;
}

export interface SlideFrameHandle {
  gotoPage: (order: number) => void;
  setBlockState: (visible: string[], focus: string[]) => void;
  requestMeasure: () => void;
}

interface SlideFrameProps {
  html: string;
  title: string;
  /** frame 就绪后回调（发送指令的唯一通道就绪）。 */
  onReady?: () => void;
  onPageSelected?: (order: number, slideId: string) => void;
  onSourceClick?: (sourceId: string) => void;
  onHotkey?: (key: string) => void;
  onMeasure?: (order: number, blocks: FrameBlockMeasure[]) => void;
  className?: string;
}

const SlideFrame = forwardRef<SlideFrameHandle, SlideFrameProps>(
  function SlideFrame(
    { html, title, onReady, onPageSelected, onSourceClick, onHotkey,
      onMeasure, className },
    ref,
  ) {
    const styledHtml = useMemo(() => html.replace(/<\/head>/i, `<style>${PRESENTATION_STYLE}</style></head>`), [html]);
    const iframeRef = useRef<HTMLIFrameElement | null>(null);
    const portRef = useRef<MessagePort | null>(null);
    const desiredRef = useRef<{
      order: number | null;
      blocks: { visible: string[]; focus: string[] } | null;
    }>({ order: null, blocks: null });
    const callbacksRef = useRef({ onReady, onPageSelected, onSourceClick,
      onHotkey, onMeasure });
    callbacksRef.current = { onReady, onPageSelected, onSourceClick,
      onHotkey, onMeasure };

    useEffect(() => {
      const onWindowMessage = (event: MessageEvent) => {
        const frame = iframeRef.current;
        if (!frame || event.source !== frame.contentWindow) return;
        const data = event.data as
          | { type?: string; nonce?: unknown }
          | null;
        if (!data || typeof data !== "object") return;
        if (data.type === "classroom_ready" && typeof data.nonce === "string") {
          portRef.current?.close();
          const channel = new MessageChannel();
          portRef.current = channel.port1;
          channel.port1.onmessage = (msg: MessageEvent) => {
            const payload = msg.data as {
              type?: string;
              order?: number;
              slide_id?: string;
              source_id?: string;
              blocks?: FrameBlockMeasure[];
            };
            if (!payload || typeof payload !== "object") return;
            if (payload.type === "page_selected" && typeof payload.order === "number") {
              callbacksRef.current.onPageSelected?.(
                payload.order, payload.slide_id ?? "");
            } else if (payload.type === "source_clicked" && payload.source_id) {
              callbacksRef.current.onSourceClick?.(payload.source_id);
            } else if (payload.type === "layout_measurement" && payload.blocks) {
              callbacksRef.current.onMeasure?.(
                payload.order ?? 0, payload.blocks);
            } else if (payload.type === "hotkey"
                       && typeof (payload as { key?: unknown }).key === "string") {
              callbacksRef.current.onHotkey?.(
                (payload as { key: string }).key);
            }
          };
          channel.port1.start?.();
          frame.contentWindow?.postMessage(
            { type: "classroom_init", nonce: data.nonce },
            "*",
            [channel.port2],
          );
          const desired = desiredRef.current;
          if (desired.order !== null) {
            channel.port1.postMessage({ type: "goto_page", order: desired.order });
          }
          if (desired.blocks) {
            channel.port1.postMessage({ type: "set_block_state",
              ...desired.blocks });
          }
          callbacksRef.current.onReady?.();
        }
      };
      window.addEventListener("message", onWindowMessage);
      return () => {
        window.removeEventListener("message", onWindowMessage);
        portRef.current?.close();
        portRef.current = null;
      };
    }, [html]);

    const post = useCallback((message: Record<string, unknown>) => {
      portRef.current?.postMessage(message);
    }, []);

    useImperativeHandle(
      ref,
      () => ({
        gotoPage: (order: number) => {
          desiredRef.current.order = order;
          post({ type: "goto_page", order });
        },
        setBlockState: (visible: string[], focus: string[]) => {
          desiredRef.current.blocks = { visible, focus };
          post({ type: "set_block_state", visible, focus });
        },
        requestMeasure: () => post({ type: "measure" }),
      }),
      [post],
    );

    return (
      <iframe
        ref={iframeRef}
        title={title}
        className={className}
        sandbox="allow-scripts"
        srcDoc={styledHtml}
        referrerPolicy="no-referrer"
      />
    );
  },
);

export default SlideFrame;
