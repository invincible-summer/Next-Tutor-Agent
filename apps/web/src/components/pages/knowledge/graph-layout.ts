// 知识图谱共享布局与 SVG 绘制助手：分层 DAG 布局（前置边最长路径分层，带环保护）。
// 平铺概念视图与章节视图共用；纯函数、无 React 依赖，便于单测。
import type { KnowledgeEdge } from "@/lib/types-modules";

export const NODE_H = 28;
export const LAYER_GAP = 74;
export const COL_GAP = 30;
export const PAD = 48;

export interface LayoutItem<T> {
  n: T;
  cx: number;
  cy: number;
  w: number;
}

export interface DagLayout<T> {
  items: LayoutItem<T>[];
  byId: Map<string, LayoutItem<T>>;
  w: number;
  h: number;
}

/** 估算文本像素宽度（fontSize 11：CJK≈11px，拉丁≈6px）。 */
export function measure(s: string): number {
  let w = 0;
  for (const ch of s) w += (ch.codePointAt(0) ?? 0) > 0xff ? 11 : 6;
  return w;
}

export function nodeWidth(name: string): number {
  return Math.min(230, Math.max(60, measure(name) + 30));
}

export function fitLabel(name: string, maxW: number): string {
  if (measure(name) <= maxW) return name;
  let s = name;
  while (s.length > 1 && measure(`${s}…`) > maxW) s = s.slice(0, -1);
  return `${s}…`;
}

/** 长标签两行拆分（词边界优先，CJK 无空格时按码点宽度对半），每行再 fit。 */
export function splitLabel(name: string, maxW: number): string[] {
  if (measure(name) <= maxW) return [name];
  const chars = [...name];
  let cut = chars.length;
  for (let i = 0; i < chars.length; i++) {
    if (measure(chars.slice(0, i).join("")) >= maxW / 2) {
      cut = i;
      break;
    }
  }
  let best = -1;
  for (let i = 0; i < chars.length; i++) {
    if (chars[i] === " " || chars[i] === "\u3000") {
      if (best < 0 || Math.abs(i - cut) < Math.abs(best - cut)) best = i;
    }
  }
  const splitAt = best > 0 ? best : Math.max(1, cut);
  const out = [
    fitLabel(chars.slice(0, splitAt).join("").trim(), maxW),
    fitLabel(chars.slice(splitAt).join("").trim(), maxW),
  ].filter(Boolean);
  return out.length ? out : [fitLabel(name, maxW)];
}

/** 从 a 底部到 b 顶部的垂直贝塞尔边。 */
export function edgePath(x1: number, y1: number, x2: number, y2: number): string {
  const dir = y2 >= y1 ? 1 : -1;
  const c = Math.min(60, Math.max(16, Math.abs(y2 - y1) * 0.45)) * dir;
  return `M ${x1} ${y1} C ${x1} ${y1 + c} ${x2} ${y2 - c} ${x2} ${y2}`;
}

export function edgeStyle(type: string): { stroke: string; dash?: string; arrow: boolean; opacity: number } {
  switch (type) {
    case "prerequisite":
      return { stroke: "rgb(var(--fg-secondary))", arrow: true, opacity: 0.6 };
    case "application":
      return { stroke: "rgb(var(--accent))", dash: "1.5 5", arrow: false, opacity: 0.8 };
    case "misconception":
      return { stroke: "rgb(var(--danger))", dash: "6 4", arrow: false, opacity: 0.75 };
    case "related":
    default:
      return { stroke: "rgb(var(--muted))", dash: "5 4", arrow: false, opacity: 0.6 };
  }
}

/**
 * 分层布局：layer = 沿 prerequisite 边到根的最长距离（带环保护）。
 * 概念 / 章节通用：只依赖 id + name；nodeH 决定节点盒高与层距。
 */
export function layoutDag<T extends { id: string; name: string }>(
  nodes: T[],
  edges: Pick<KnowledgeEdge, "from" | "to" | "type">[],
  nodeH: number = NODE_H,
): DagLayout<T> {
  const byId = new Map<string, LayoutItem<T>>();
  if (nodes.length === 0) return { items: [], byId, w: 0, h: 0 };
  const ids = new Set(nodes.map((n) => n.id));
  const incoming = new Map<string, string[]>();
  for (const e of edges) {
    if (e.type !== "prerequisite" || !ids.has(e.from) || !ids.has(e.to)) continue;
    const arr = incoming.get(e.to);
    if (arr) arr.push(e.from);
    else incoming.set(e.to, [e.from]);
  }
  const memo = new Map<string, number>();
  const stack = new Set<string>();
  const depth = (id: string): number => {
    const hit = memo.get(id);
    if (hit !== undefined) return hit;
    if (stack.has(id)) return 0; // 环：按根处理
    stack.add(id);
    let d = 0;
    for (const s of incoming.get(id) ?? []) d = Math.max(d, depth(s) + 1);
    stack.delete(id);
    memo.set(id, d);
    return d;
  };
  const layers = new Map<number, T[]>();
  for (const n of nodes) {
    const d = depth(n.id);
    const arr = layers.get(d);
    if (arr) arr.push(n);
    else layers.set(d, [n]);
  }
  // 同层稳定排序：按输入数组出现顺序（后端即构建顺序：卷序×章序、概念出现
  // 序）。按 id 排序是哈希序 → 章节/概念同层乱排（大学物理两分册实测问题）。
  const indexOf = new Map(nodes.map((n, i) => [n.id, i]));
  const sorted = [...layers.values()].map((arr) =>
    [...arr].sort((a, b) => (indexOf.get(a.id) ?? 0) - (indexOf.get(b.id) ?? 0) || a.name.localeCompare(b.name)),
  );
  sorted.sort((a, b) => (memo.get(a[0].id) ?? 0) - (memo.get(b[0].id) ?? 0));
  const widths = new Map(nodes.map((n) => [n.id, nodeWidth(n.name)]));
  const layerW = sorted.map(
    (arr) => arr.reduce((s, n) => s + (widths.get(n.id) ?? 60), 0) + Math.max(0, arr.length - 1) * COL_GAP,
  );
  const maxW = Math.max(...layerW, 0);
  const items: LayoutItem<T>[] = [];
  sorted.forEach((arr, li) => {
    let x = PAD + (maxW - layerW[li]) / 2;
    const cy = PAD + li * (nodeH + LAYER_GAP) + nodeH / 2;
    for (const n of arr) {
      const w = widths.get(n.id) ?? 60;
      items.push({ n, cx: x + w / 2, cy, w });
      x += w + COL_GAP;
    }
  });
  for (const it of items) byId.set(it.n.id, it);
  const h = sorted.length > 0 ? sorted.length * (nodeH + LAYER_GAP) - LAYER_GAP + PAD * 2 : 0;
  return { items, byId, w: maxW + PAD * 2, h };
}
