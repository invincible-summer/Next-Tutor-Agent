import React, { useEffect, useMemo, useRef, useState } from "react";
import { ErrorState } from "@/ui/EmptyState";
import { useCopy } from "@/lib/copy";
import { WebView } from "react-native-webview";
import { randomUUID } from "expo-crypto";
import { classroomDocument, readFrameMessage } from "./frame-document";
export function LessonFrame({
  html,
  order,
  slides,
  height,
  onPage,
}: {
  html: string;
  order: number;
  slides: { slide_id: string; order: number }[];
  height: number;
  onPage: (order: number) => void;
}) {
  const view = useRef<WebView>(null);
  const [nonce] = useState(() => randomUUID());
  const [ready, setReady] = useState(false);
  const c = useCopy();
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const document = useMemo(() => {
    try {
      return classroomDocument(html, nonce);
    } catch {
      return null;
    }
  }, [html, nonce]);
  useEffect(() => {
    if (ready)
      view.current?.injectJavaScript(
        `window.__ntGoto&&window.__ntGoto(${JSON.stringify(nonce)},${order});true;`,
      );
  }, [ready, order, nonce]);
  if (!document || failed)
    return (
      <ErrorState
        title={c(
          "课件画布暂不可用，可继续阅读原生讲稿。",
          "The slide canvas is unavailable. You can still read the narration.",
        )}
        retryLabel={c("重试", "Retry")}
        onRetry={() => {
          setFailed(false);
          setAttempt((old) => old + 1);
        }}
      />
    );
  return (
    <WebView
      key={attempt}
      onError={() => {
        setReady(false);
        setFailed(true);
      }}
      onContentProcessDidTerminate={() => {
        setReady(false);
        setFailed(true);
      }}
      ref={view}
      source={{ html: document, baseUrl: "about:blank" }}
      style={{ height, backgroundColor: "#fff" }}
      originWhitelist={["about:blank", "about:srcdoc"]}
      javaScriptEnabled
      domStorageEnabled={false}
      sharedCookiesEnabled={false}
      thirdPartyCookiesEnabled={false}
      incognito
      cacheEnabled={false}
      allowFileAccess={false}
      allowFileAccessFromFileURLs={false}
      allowUniversalAccessFromFileURLs={false}
      setSupportMultipleWindows={false}
      scrollEnabled={false}
      onShouldStartLoadWithRequest={(r) =>
        r.url === "about:blank" || r.url === "about:srcdoc"
      }
      onMessage={(e) => {
        const message = readFrameMessage(e.nativeEvent.data, nonce, slides);
        if (message?.type === "ready") setReady(true);
        else if (message?.type === "page_selected") onPage(message.order);
      }}
    />
  );
}
