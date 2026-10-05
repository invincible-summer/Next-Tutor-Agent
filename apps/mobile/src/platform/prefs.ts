import AsyncStorage from "@react-native-async-storage/async-storage";

/**
 * 非敏感偏好存储（主题/语言/字号/学段等）。严禁存 token、学习正文。
 * key 统一带 `nt.` 前缀，与 Web localStorage 键语义对齐但独立命名空间。
 */
export const PREF_KEYS = {
  theme: "nt.theme",
  lang: "nt.lang",
  fontScale: "nt.fontScale",
  grade: "nt.grade",
  outputLang: "nt.outputLang",
  devApiBaseUrl: "nt.devApiBaseUrl",
} as const;

export async function loadPref(key: string): Promise<string | null> {
  try {
    return await AsyncStorage.getItem(key);
  } catch {
    return null;
  }
}

export async function savePref(
  key: string,
  value: string | null,
): Promise<void> {
  try {
    if (value === null) await AsyncStorage.removeItem(key);
    else await AsyncStorage.setItem(key, value);
  } catch {
    // 偏好写入失败不阻断交互。
  }
}
