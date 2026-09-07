// 时间格式化助手（Unix 秒 → 展示字符串）。
import type { Lang } from "./i18n";

/** YYYY-MM-DD HH:mm */
export function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** YYYY-MM-DD */
export function fmtDate(ts: number): string {
  const d = new Date(ts * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** 相对时间：刚刚 / N 分钟前 / N 小时前 / N 天前（超过 30 天给日期）。 */
export function relTime(ts: number, lang: Lang = "zh"): string {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return lang === "en" ? "just now" : "刚刚";
  if (diff < 3600) {
    const n = Math.floor(diff / 60);
    return lang === "en" ? `${n}m ago` : `${n} 分钟前`;
  }
  if (diff < 86400) {
    const n = Math.floor(diff / 3600);
    return lang === "en" ? `${n}h ago` : `${n} 小时前`;
  }
  if (diff < 86400 * 30) {
    const n = Math.floor(diff / 86400);
    return lang === "en" ? `${n}d ago` : `${n} 天前`;
  }
  return fmtDate(ts);
}

/** 按日分组键（YYYY-MM-DD）。 */
export function dayKey(ts: number): string {
  return fmtDate(ts);
}

/** 字节数 → 人类可读（B/KB/MB/GB/TB，一位小数，≥100 取整）。 */
export function fmtBytes(n: number): string {
  const v = Math.max(0, Math.floor(n ?? 0));
  if (v < 1024) return `${v} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let x = v / 1024;
  let i = 0;
  while (x >= 1024 && i < units.length - 1) {
    x /= 1024;
    i += 1;
  }
  return `${x >= 100 ? Math.round(x) : Math.round(x * 10) / 10} ${units[i]}`;
}
