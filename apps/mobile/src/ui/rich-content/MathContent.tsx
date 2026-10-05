import React, { useEffect, useMemo, useRef, useState } from "react";
import { PixelRatio, View } from "react-native";
import { WebView } from "react-native-webview";
import { randomUUID } from "expo-crypto";
import { useTheme } from "../ThemeProvider";
import { mathDocument, safeContentLink } from "./math-document";
import type { RichBlock } from "./parser";
export function MathContent({
  blocks,
  text,
  streaming = false,
  onLinkPress,
}: {
  blocks: RichBlock[];
  text: string;
  streaming?: boolean;
  onLinkPress: (url: string) => void;
}) {
  const { theme } = useTheme();
  const [nonce] = useState(() => randomUUID());
  const [height, setHeight] = useState(80);
  const [overflow, setOverflow] = useState(false);
  const [visibleBlocks, setVisibleBlocks] = useState(blocks);
  const latest = useRef(blocks);
  latest.current = blocks;
  useEffect(() => {
    if (!streaming) {
      setVisibleBlocks(blocks);
      return;
    }
    const timer = setInterval(() => setVisibleBlocks(latest.current), 220);
    return () => clearInterval(timer);
  }, [streaming]);
  const renderedBlocks = streaming ? visibleBlocks : blocks;
  const html = useMemo(
    () =>
      mathDocument(
        renderedBlocks,
        nonce,
        theme.colors.fg,
        "transparent",
        Number(theme.type.body.fontSize ?? 16) * PixelRatio.getFontScale(),
      ),
    [renderedBlocks, nonce, theme],
  );
  return (
    <View accessibilityLabel={text} accessible style={{ minHeight: height }}>
      <WebView
        source={{ html, baseUrl: "about:blank" }}
        originWhitelist={["about:blank"]}
        scrollEnabled={overflow}
        style={{ height, backgroundColor: "transparent" }}
        javaScriptEnabled
        domStorageEnabled={false}
        cacheEnabled={false}
        incognito
        sharedCookiesEnabled={false}
        thirdPartyCookiesEnabled={false}
        allowFileAccess={false}
        allowFileAccessFromFileURLs={false}
        allowUniversalAccessFromFileURLs={false}
        setSupportMultipleWindows={false}
        onShouldStartLoadWithRequest={(r) => r.url === "about:blank"}
        onMessage={(e) => {
          try {
            const v = JSON.parse(e.nativeEvent.data);
            if (v.nonce !== nonce) return;
            if (
              v.type === "height" &&
              typeof v.height === "number" &&
              Number.isFinite(v.height)
            ) {
              setHeight(Math.max(32, Math.min(12000, v.height)));
              setOverflow(v.height > 12000);
            } else if (
              v.type === "link" &&
              typeof v.url === "string" &&
              safeContentLink(v.url)
            )
              onLinkPress(v.url);
          } catch {}
        }}
      />
    </View>
  );
}
