"use client";
/* 课堂课件 iframe 宿主（plan.md §9.5）。
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
  useRef,
} from "react";

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
  setReading: (on: boolean) => void;
  requestMeasure: () => void;
}

interface SlideFrameProps {
  html: string;
  title: string;
  /** frame 就绪后回调（发送指令的唯一通道就绪）。 */
  onReady?: () => void;
  onPageSelected?: (order: number, slideId: string) => void;
  onSourceClick?: (sourceId: string) => void;
  onMeasure?: (order: number, blocks: FrameBlockMeasure[]) => void;
  className?: string;
}

const SlideFrame = forwardRef<SlideFrameHandle, SlideFrameProps>(
  function SlideFrame(
    { html, title, onReady, onPageSelected, onSourceClick, onMeasure, className },
    ref,
  ) {
    const iframeRef = useRef<HTMLIFrameElement | null>(null);
    const portRef = useRef<MessagePort | null>(null);
    const nonceRef = useRef<string>("");

    useEffect(() => {
      const onWindowMessage = (event: MessageEvent) => {
        const frame = iframeRef.current;
        if (!frame || event.source !== frame.contentWindow) return;
        const data = event.data as
          | { type?: string; nonce?: unknown }
          | null;
        if (!data || typeof data !== "object") return;
        if (data.type === "classroom_ready" && typeof data.nonce === "string") {
          nonceRef.current = data.nonce;
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
              onPageSelected?.(payload.order, payload.slide_id ?? "");
            } else if (payload.type === "source_clicked" && payload.source_id) {
              onSourceClick?.(payload.source_id);
            } else if (payload.type === "layout_measurement" && payload.blocks) {
              onMeasure?.(payload.order ?? 0, payload.blocks);
            }
          };
          channel.port1.start?.();
          frame.contentWindow?.postMessage(
            { type: "classroom_init", nonce: data.nonce },
            "*",
            [channel.port2],
          );
          onReady?.();
        }
      };
      window.addEventListener("message", onWindowMessage);
      return () => {
        window.removeEventListener("message", onWindowMessage);
        portRef.current?.close();
        portRef.current = null;
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [html]);

    const post = useCallback((message: Record<string, unknown>) => {
      portRef.current?.postMessage(message);
    }, []);

    useImperativeHandle(
      ref,
      () => ({
        gotoPage: (order: number) => post({ type: "goto_page", order }),
        setBlockState: (visible: string[], focus: string[]) =>
          post({ type: "set_block_state", visible, focus }),
        setReading: (on: boolean) => post({ type: "set_reading", reading: on }),
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
        srcDoc={html}
        referrerPolicy="no-referrer"
      />
    );
  },
);

export default SlideFrame;
