import { useCallback } from "react";
import { useI18n } from "@/providers/I18nProvider";
export function useCopy() {
  const { lang } = useI18n();
  return useCallback(
    (zh: string, en: string) => (lang === "zh" ? zh : en),
    [lang],
  );
}
