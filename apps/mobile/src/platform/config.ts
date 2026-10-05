import Constants from "expo-constants";
import { Platform } from "react-native";

/**
 * API base URL 解析：EXPO_PUBLIC_API_BASE_URL（app.config.ts extra），由构建环境注入。
 * 该值是公开非密配置；密钥永不进入 App bundle。
 */
export function resolveApiBaseUrl(): string {
  const extra = Constants.expoConfig?.extra as
    { apiBaseUrl?: string } | undefined;
  const fromConfig = extra?.apiBaseUrl;
  if (typeof fromConfig === "string" && fromConfig.length > 0)
    return fromConfig;
  return "http://localhost:8000/api/v1";
}

export const APP_VERSION: string = Constants.expoConfig?.version ?? "0.0.0";

export const BUILD_NUMBER: string =
  (Platform.OS === "ios"
    ? Constants.expoConfig?.ios?.buildNumber
    : Constants.expoConfig?.android?.versionCode?.toString()) ?? "0";

export const CLIENT_PLATFORM: "ios" | "android" =
  Platform.OS === "ios" ? "ios" : "android";
