import type { makePageT } from "@/lib/i18n-page";

/** 页面级翻译函数类型。 */
export type Tr = ReturnType<typeof makePageT>;

/** 词条中的 {n} 占位替换。 */
export const fill = (s: string, n: number): string => s.replace("{n}", String(n));
