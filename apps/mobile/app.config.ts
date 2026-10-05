import type { ExpoConfig } from "expo/config";

/**
 * 集中管理移动端配置：bundle id / 版本 / scheme / 权限 / 插件 / runtimeVersion。
 * EXPO_PUBLIC_API_BASE_URL 是公开非密配置，由构建环境注入。
 */
const API_BASE_URL =
  process.env.EXPO_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

if (process.env.EAS_BUILD_PROFILE === "production") {
  const url = new URL(API_BASE_URL);
  if (
    url.protocol !== "https:" ||
    ["localhost", "127.0.0.1", "::1"].includes(url.hostname)
  )
    throw new Error("Production builds require a public HTTPS API URL.");
}

const config: ExpoConfig = {
  name: "Next Tutor",
  slug: "next-tutor-agent",
  scheme: "nexttutor",
  version: "3.0.0",
  orientation: "default",
  userInterfaceStyle: "automatic",
  icon: "./assets/brand/icon.png",
  backgroundColor: "#F6F5F1",
  ios: {
    supportsTablet: true,
    buildNumber: "1",
    bundleIdentifier: "com.nexttutor.agent",
  },
  android: {
    package: "com.nexttutor.agent",
    versionCode: 1,
    predictiveBackGestureEnabled: false,
    adaptiveIcon: {
      foregroundImage: "./assets/brand/adaptive-icon.png",
      backgroundColor: "#F6F5F1",
    },
    softwareKeyboardLayoutMode: "resize",
  },
  runtimeVersion: { policy: "appVersion" },
  plugins: [
    "expo-router",
    "expo-asset",
    "expo-secure-store",
    [
      "expo-audio",
      {
        microphonePermission: "录制你的问题，发送至服务器转为文字。",
        enableBackgroundPlayback: true,
        enableBackgroundRecording: false,
      },
    ],
    ["expo-document-picker", { iCloudContainerEnvironment: "Production" }],
    ["expo-image-picker", { photosPermission: "选择图片用于向辅导老师提问。" }],
    [
      "expo-splash-screen",
      {
        image: "./assets/brand/splash-icon.png",
        resizeMode: "contain",
        backgroundColor: "#F6F5F1",
        dark: { backgroundColor: "#101216" },
      },
    ],
  ],
  extra: {
    apiBaseUrl: API_BASE_URL,
  },
  experiments: { tsconfigPaths: true },
};

export default config;
