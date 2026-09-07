/** 轻量 className 合并（替代 clsx，零依赖）。 */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
