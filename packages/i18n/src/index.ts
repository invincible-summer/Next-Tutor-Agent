/**
 * Shared cross-client i18n primitives: language type, locale mapping and the
 * translation contract both platforms implement. Web's page-level
 * `strings.ts` dictionaries are NOT migrated wholesale — keys move here only
 * when a feature actually ships on mobile (see docs/architecture/client-platform.md).
 */
export type Lang = "zh" | "en";

export function localeFor(lang: Lang): "zh-CN" | "en-US" {
  return lang === "en" ? "en-US" : "zh-CN";
}

export const LANGS: { code: Lang; label: string }[] = [
  { code: "zh", label: "中文" },
  { code: "en", label: "English" },
];

export type MessageDict = Record<string, string>;

/**
 * Bound translator: resolved dictionary entry, else the caller's fallback,
 * else the key itself (same chain as the Web `t()`).
 */
export type Translator = (key: string, fallback?: string) => string;

export function createTranslator(dicts: Record<Lang, MessageDict>, lang: Lang): Translator {
  const dict = dicts[lang];
  return (key, fallback) => dict[key] ?? fallback ?? key;
}

export const I18N_PACKAGE_VERSION = "0.2.0";
