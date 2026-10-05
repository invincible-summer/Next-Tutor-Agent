// jest config: jest-expo preset + pnpm-aware transformIgnorePatterns.
// Packages ship raw TS (workspace @next-tutor/*) or modern RN/Expo source and
// must be transformed; everything else stays ignored for speed.
const packagesToTransform = [
  "(jest-)?react-native",
  "@react-native(-community)?",
  "expo(nent)?",
  "@expo(nent)?",
  "react-navigation",
  "@react-navigation",
  "react-native-svg",
  "react-native-gesture-handler",
  "react-native-reanimated",
  "react-native-safe-area-context",
  "react-native-screens",
  "lucide-react-native",
  "@next-tutor",
  "@tanstack",
  "zustand",
].join("|");

module.exports = {
  preset: "jest-expo",
  testMatch: ["<rootDir>/tests/**/*.test.{ts,tsx}"],
  // pnpm 布局：node_modules/.pnpm/<pkg>@<ver>[_peers]/node_modules/<pkg>/…
  // 前缀匹配（与官方 jest-expo 模式一致），同时兼容 .pnpm 中间层。
  transformIgnorePatterns: [
    `node_modules/(?!(?:.pnpm/[^/]+/node_modules/)?(?:${packagesToTransform}))`,
  ],
  moduleFileExtensions: ["ts", "tsx", "js", "jsx", "json"],
  setupFiles: ["<rootDir>/tests/setup.ts"],
};
