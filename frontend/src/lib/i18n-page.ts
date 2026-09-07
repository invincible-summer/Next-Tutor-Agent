// 页面级 i18n：各页面把自己的词条放在页面目录的 strings.ts 里，
// 用 makePageT 合并全局词典（页面词条优先，全局兜底）。
// 这样各页面词条零共享文件冲突，也不需要改 lib/i18n.ts。
import { getDict, type Lang } from "./i18n";

type Dict = Record<string, string>;

export interface PageStrings {
  zh: Dict;
  en: Dict;
}

export function makePageT(lang: Lang, strings: PageStrings) {
  return (key: string, fallback?: string): string =>
    strings[lang]?.[key] ?? getDict(lang)[key] ?? fallback ?? key;
}
