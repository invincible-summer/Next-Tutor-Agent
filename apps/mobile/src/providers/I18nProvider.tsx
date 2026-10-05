import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { Lang, Translator } from "@next-tutor/i18n";

import { makeTranslator } from "@/lib/i18n";
import { loadPref, PREF_KEYS, savePref } from "@/platform/prefs";

interface I18nContextValue {
  lang: Lang;
  t: Translator;
  setLang: (lang: Lang) => void;
}

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Lang>("zh");

  useEffect(() => {
    void loadPref(PREF_KEYS.lang).then((stored) => {
      if (stored === "zh" || stored === "en") setLangState(stored);
    });
  }, []);

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    void savePref(PREF_KEYS.lang, next);
  }, []);

  const value = useMemo<I18nContextValue>(
    () => ({ lang, t: makeTranslator(lang), setLang }),
    [lang, setLang],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used within I18nProvider");
  return ctx;
}
