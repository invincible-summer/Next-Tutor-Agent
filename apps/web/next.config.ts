import type { NextConfig } from "next";

const demo = process.env.NEXT_PUBLIC_DEMO_MODE === "1";

const nextConfig: NextConfig = {
  ...(process.env.NEXT_TUTOR_E2E === "1" ? {
    distDir: ".next-e2e",
    typescript: { tsconfigPath: "tsconfig.e2e.json" },
  } : {}),
  // Hide the Next.js dev/build floating indicator.
  devIndicators: false,
  // The fallback API proxy must cover V1's 90-second illustration budget
  // and the browser's 120-second POST watchdog.
  experimental: { proxyTimeout: 150_000 },
  ...(demo ? {
    output: "export" as const,
    distDir: "out",
    basePath: process.env.NEXT_PUBLIC_BASE_PATH || "",
    trailingSlash: true,
    images: { unoptimized: true },
  } : {}),
  // Fallback rewrite for same-origin requests when NEXT_PUBLIC_BACKEND_URL
  // is unset. Despite the name, rewrites are NOT dev-only: they are baked into
  // the build and also active under `next start`. In the same-origin
  // production deployment this is harmless — nginx intercepts /api/* before it
  // ever reaches Next, so the rewrite never fires. When launched via start.sh
  // the frontend calls the backend directly (NEXT_PUBLIC_BACKEND_URL is set),
  // so this rewrite is bypassed there too. Its real purpose: let a directly
  // launched `npx next dev` work out of the box when a backend is on :8000.
  //
  // If your backend is on another port, either launch via start.sh (sets the
  // env var) or run:
  //   NEXT_PUBLIC_BACKEND_URL=http://localhost:8123 npx next dev
  async rewrites() {
    if (demo) return [];
    return [
      { source: "/api/:path*", destination: `${process.env.BACKEND_URL || "http://127.0.0.1:8000"}/api/:path*` },
    ];
  },
  // 资料中心 Tab 路由段化：/resources 落点在路由前直接 307（零 JS），
  // 旧深链 /resources?tab=textbooks 一并兼容。页面级 redirect 仅作兜底。
  async redirects() {
    if (demo) return [];
    return [
      { source: "/resources", has: [{ type: "query", key: "tab", value: "textbooks" }], destination: "/resources/textbooks", permanent: false },
      { source: "/resources", destination: "/resources/files", permanent: false },
    ];
  },
  // 基础安全响应头 + 基础 CSP。
  //
  // CSP 是"阻断外域脚本/数据外连"的一层（与 localStorage 中的 token 组合
  // 成纵深）：script-src 保留 'unsafe-inline'（Next 引导脚本与主题初始化
  // 无 nonce 机制，见原注释），但外域脚本/样式/连接一律拒绝。开发模式额
  // 外放开 'unsafe-eval'（react-refresh 需要）。各项资源指令按现有功能
  // 实测配置：blob:（TTS 音频/上传预览）、data:（KaTeX 字体/quiz SVG）、
  // ws://localhost|127.0.0.1（语音 WS 开发直连；同源 wss 由 'self' 覆盖）。
  //
  // 其余每条都验证过不影响现有功能：
  //  - frame-ancestors 'self' + XFO：防点击劫持（应用自身嵌入 sandbox
  //    iframe 走 frame-src，不受影响）
  //  - object-src 'none'：封死 <object>/<embed> 插件嵌入
  //  - base-uri 'self' / form-action 'self'：防 <base> 劫持与跨站表单提交
  //  - microphone=(self)：语音功能只在同源上下文可用
  async headers() {
    if (demo) return [];
    let backendOrigin = "";
    if (process.env.NEXT_PUBLIC_BACKEND_URL) {
      try { backendOrigin = new URL(process.env.NEXT_PUBLIC_BACKEND_URL).origin; } catch { backendOrigin = ""; }
    }
    const connectSrc = [
      "'self'",
      ...(backendOrigin ? [backendOrigin] : []),
      "ws://localhost:*", "ws://127.0.0.1:*",
    ].join(" ");
    const isDev = process.env.NODE_ENV === "development";
    const csp = [
      `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob:",
      "font-src 'self' data:",
      `connect-src ${connectSrc}`,
      "frame-src 'self' blob:",
      "worker-src 'self' blob:",
      "media-src 'self' blob:",
      "frame-ancestors 'self'",
      "object-src 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join("; ");
    return [{
      source: "/:path*",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "SAMEORIGIN" },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        { key: "Permissions-Policy", value: "camera=(), geolocation=(), microphone=(self)" },
        { key: "Content-Security-Policy", value: csp },
      ],
    }];
  },
};

// Static hosts have no route/response-header server hooks.
if (demo) {
  delete nextConfig.rewrites;
  delete nextConfig.redirects;
  delete nextConfig.headers;
}

export default nextConfig;
