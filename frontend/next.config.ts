import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Hide the Next.js dev/build floating indicator (replaced by our own
  // settings gear in the top-right corner).
  devIndicators: false,
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
    return [
      { source: "/api/:path*", destination: `${process.env.BACKEND_URL || "http://127.0.0.1:8000"}/api/:path*` },
    ];
  },
  // 资料中心 Tab 路由段化：/resources 落点在路由前直接 307（零 JS），
  // 旧深链 /resources?tab=textbooks 一并兼容。页面级 redirect 仅作兜底。
  async redirects() {
    return [
      { source: "/resources", has: [{ type: "query", key: "tab", value: "textbooks" }], destination: "/resources/textbooks", permanent: false },
      { source: "/resources", destination: "/resources/files", permanent: false },
    ];
  },
};

export default nextConfig;
