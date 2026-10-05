import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { Appearance, useColorScheme } from "react-native";

import { loadPref, PREF_KEYS, savePref } from "@/platform/prefs";
import { buildTheme, type AppTheme, type ThemePreference } from "./theme";

interface ThemeContextValue {
  theme: AppTheme;
  preference: ThemePreference;
  fontScale: number;
  setPreference: (pref: ThemePreference) => void;
  setFontScale: (scale: number) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function normalizePreference(raw: string | null): ThemePreference {
  return raw === "light" || raw === "dark" || raw === "system" ? raw : "system";
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const systemScheme = useColorScheme();
  const [preference, setPreferenceState] = useState<ThemePreference>("system");
  const [fontScale, setFontScaleState] = useState(1);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    void (async () => {
      const [theme, fs] = await Promise.all([
        loadPref(PREF_KEYS.theme),
        loadPref(PREF_KEYS.fontScale),
      ]);
      setPreferenceState(normalizePreference(theme));
      const parsed = fs ? Number(fs) : 1;
      if (parsed >= 1 && parsed <= 1.75) setFontScaleState(parsed);
      setHydrated(true);
    })();
  }, []);

  // system 模式下跟随系统外观变化（Appearance 已由 useColorScheme 订阅）。
  useEffect(() => {
    if (preference !== "system") return;
    const sub = Appearance.addChangeListener(() => {
      // useColorScheme 触发重渲染；此处仅确保 listener 生命周期覆盖。
    });
    return () => sub.remove();
  }, [preference]);

  const resolved =
    preference === "system"
      ? systemScheme === "dark"
        ? "dark"
        : "light"
      : preference;
  const theme = useMemo(
    () => buildTheme(resolved, fontScale),
    [resolved, fontScale],
  );

  const setPreference = useCallback((pref: ThemePreference) => {
    setPreferenceState(pref);
    void savePref(PREF_KEYS.theme, pref);
  }, []);

  const setFontScale = useCallback((scale: number) => {
    setFontScaleState(scale);
    void savePref(PREF_KEYS.fontScale, String(scale));
  }, []);

  const value = useMemo(
    () => ({ theme, preference, fontScale, setPreference, setFontScale }),
    [theme, preference, fontScale, setPreference, setFontScale],
  );

  // 水合完成前使用系统默认主题渲染，避免闪白/闪黑之外的第三态。
  if (!hydrated) {
    const fallback = buildTheme(systemScheme === "dark" ? "dark" : "light", 1);
    return (
      <ThemeContext.Provider
        value={{
          theme: fallback,
          preference,
          fontScale,
          setPreference,
          setFontScale,
        }}
      >
        {children}
      </ThemeContext.Provider>
    );
  }

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
