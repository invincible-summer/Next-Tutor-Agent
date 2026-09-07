"use client";
// 漂浮文字力导向图：纯文本节点（无圆点），字号按被引用次数（入度）加权。
// 自研零依赖力学模拟：边弹簧力 + 节点斥力 + 文本包围盒碰撞（自动避让）+
// 每节点随机相位的低频正弦漂浮——收敛后仍缓慢漂移避让，不冻屏。
// Canvas 2D 渲染：黑字层次（被引多=前景加粗，少=次要灰），常态无光晕 + 设备
// 像素对齐保证清晰，hover 才轻微发光；连线常态为低透明流动虚线，按住节点
// 时相连边切换为浅灰细实线聚焦；端点裁剪到包围盒外缘不压字（断链红虚线）。
// 支持聊天/教材节点开关、平移缩放、节点拖拽与点击跳转；
// prefers-reduced-motion 退化为一次性静态布局。
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import {
  BookOpen, FolderOpen, Maximize2, MessageSquareText, Minus, NotebookPen,
  Plus, RotateCcw, Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { cn } from "@/lib/cn";
import type { GraphEdge, GraphNode, NotesGraph } from "@/lib/types-notes";

interface SimNode {
  ref: GraphNode;
  label: string;
  x: number; y: number; vx: number; vy: number;
  w: number; h: number; size: number; weight: number;
  phase: number; speed: number; held: boolean;
}

interface SimEdge {
  a: number; b: number;
  resolved: boolean;
  kind: GraphEdge["kind"];
}

interface Sim {
  nodes: SimNode[];
  edges: SimEdge[];
  adj: Set<number>[];
  font: string;
}

interface Palette {
  edge: string;
  danger: string;
  muted: string;
  fg: string;
  fgSecondary: string;
}

const FILTER_KEY = "edu-agent-notes-graph-filter";

// 图谱文字用衬线体（宋体系）：西文 Georgia，中文 Songti SC / SimSun / Noto Serif
const GRAPH_FONT = 'Georgia, "Times New Roman", "Songti SC", "STSong", "SimSun", "Noto Serif SC", "Source Han Serif SC", serif';

// 节点开关存 localStorage：useSyncExternalStore 读取（服务端快照=默认全开，
// 无 hydration 不一致；写入时同步通知）。
interface KindFilter { chats: boolean; textbooks: boolean }
const FILTER_DEFAULT: KindFilter = { chats: true, textbooks: true };
let filterCache: KindFilter | null = null;
const filterListeners = new Set<() => void>();
function readFilter(): KindFilter {
  if (filterCache) return filterCache;
  try {
    const raw = localStorage.getItem(FILTER_KEY);
    if (raw) {
      const p = JSON.parse(raw) as Partial<KindFilter>;
      filterCache = { chats: p.chats !== false, textbooks: p.textbooks !== false };
      return filterCache;
    }
  } catch { /* ignore */ }
  filterCache = { ...FILTER_DEFAULT };
  return filterCache;
}
function writeFilter(next: KindFilter) {
  filterCache = next;
  try { localStorage.setItem(FILTER_KEY, JSON.stringify(next)); } catch { /* ignore */ }
  filterListeners.forEach((l) => l());
}
function subscribeFilter(listener: () => void) {
  filterListeners.add(listener);
  return () => { filterListeners.delete(listener); };
}

/** 单步力学积分；settle=true 时关闭漂浮（用于预收敛）。 */
function tickSim(sim: Sim, t: number, settle: boolean) {
  const nodes = sim.nodes;
  const n = nodes.length;
  if (!n) return;
  // 斥力 O(n²)：个人仓库规模足够
  for (let i = 0; i < n; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < n; j++) {
      const b = nodes[j];
      let dx = b.x - a.x;
      let dy = b.y - a.y;
      let d2 = dx * dx + dy * dy;
      if (d2 < 1) { dx = (i - j) * 0.7 + 0.4; dy = ((i + j) % 5) * 0.3 - 0.6; d2 = dx * dx + dy * dy; }
      const d = Math.sqrt(d2);
          const f = Math.min(2.0, 7200 / d2);
      const fx = (dx / d) * f;
      const fy = (dy / d) * f;
      a.vx -= fx; a.vy -= fy;
      b.vx += fx; b.vy += fy;
    }
  }
  // 边弹簧：静置长度 = 两端文字半宽和 + 80px 可见边段（连线不被标签挤没）
  for (const e of sim.edges) {
    const a = nodes[e.a];
    const b = nodes[e.b];
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const d = Math.max(1, Math.hypot(dx, dy));
    const rest = (a.w + b.w) / 2 + 80;
    const f = (d - rest) * 0.016;
    const fx = (dx / d) * f;
    const fy = (dy / d) * f;
    a.vx += fx; a.vy += fy;
    b.vx -= fx; b.vy -= fy;
  }
  for (const p of nodes) {
    if (p.held) continue;
    p.vx -= p.x * 0.0008;
    p.vy -= p.y * 0.0008;
    if (!settle) {
      p.vx += Math.cos(t * p.speed + p.phase) * 0.016;
      p.vy += Math.sin(t * p.speed * 0.9 + p.phase * 1.31) * 0.016;
    }
    p.vx *= 0.86;
    p.vy *= 0.86;
    const v = Math.hypot(p.vx, p.vy);
    if (v > 3.2) { p.vx = (p.vx / v) * 3.2; p.vy = (p.vy / v) * 3.2; }
    p.x += p.vx;
    p.y += p.vy;
  }
  // 文本包围盒碰撞：位置修正（自动避让，标签互不重叠）
  for (let i = 0; i < n; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < n; j++) {
      const b = nodes[j];
      const ox = (a.w + b.w) / 2 + 18 - Math.abs(b.x - a.x);
      if (ox <= 0) continue;
      const oy = (a.h + b.h) / 2 + 12 - Math.abs(b.y - a.y);
      if (oy <= 0) continue;
      const wa = a.held ? 0 : b.held ? 1 : 0.5;
      const wb = 1 - wa;
      if (ox < oy) {
        const push = ox * 0.7 * (b.x >= a.x ? 1 : -1);
        a.x -= push * wa;
        b.x += push * wb;
      } else {
        const push = oy * 0.7 * (b.y >= a.y ? 1 : -1);
        a.y -= push * wa;
        b.y += push * wb;
      }
    }
  }
}

/** 单位方向 (ux,uy) 从中心穿出半宽 hw/半高 hh 包围盒的距离占全长 d 的比例，clamp [0, 0.5]。 */
function boxExit(ux: number, uy: number, hw: number, hh: number, d: number) {
  let dist = Infinity;
  if (Math.abs(ux) > 1e-6) dist = Math.min(dist, hw / Math.abs(ux));
  if (Math.abs(uy) > 1e-6) dist = Math.min(dist, hh / Math.abs(uy));
  return dist === Infinity ? 0.5 : Math.max(0, Math.min(0.5, dist / d));
}

function drawSim(
  canvas: HTMLCanvasElement, sim: Sim,
  view: { x: number; y: number; scale: number },
  colors: Palette, hover: number, t: number, animate: boolean,
) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const W = canvas.width / dpr;
  const H = canvas.height / dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.setTransform(
    dpr * view.scale, 0, 0, dpr * view.scale,
    dpr * (view.x * view.scale + W / 2), dpr * (view.y * view.scale + H / 2),
  );
  // 边：常态保持原本的低透明流动虚线；按住（点击着）某节点时，与之相连的
  // 边切换为浅灰细实线，配合 hover 压暗其余边形成聚焦；未解析断链始终红
  // 色虚线；端点裁剪到文字包围盒外缘（不钻到字底下）。
  const flow = animate ? (t * 9) % 14 : 0;
  const pressed = sim.nodes.findIndex((p) => p.held);
  for (const e of sim.edges) {
    const a = sim.nodes[e.a];
    const b = sim.nodes[e.b];
    const dim = hover >= 0 && hover !== e.a && hover !== e.b;
    const focus = pressed >= 0 && (e.a === pressed || e.b === pressed);
    const d = Math.max(1, Math.hypot(b.x - a.x, b.y - a.y));
    const ux = (b.x - a.x) / d;
    const uy = (b.y - a.y) / d;
    const ta = boxExit(ux, uy, a.w / 2 + 6, a.h / 2 + 4, d);
    const tb = boxExit(-ux, -uy, b.w / 2 + 6, b.h / 2 + 4, d);
    if (ta + tb >= 1) continue; // 包围盒仍重叠时不画，避免线段穿过文字
    if (focus) {
      ctx.globalAlpha = dim ? 0.07 : e.resolved ? 0.8 : 0.55;
      ctx.strokeStyle = e.resolved ? colors.muted : colors.danger;
      ctx.lineWidth = e.kind === "note" ? 1.1 : 0.95;
      ctx.setLineDash(e.resolved ? [] : [3, 5]);
    } else {
      ctx.globalAlpha = dim ? 0.07 : e.resolved ? 0.42 : 0.38;
      ctx.strokeStyle = e.resolved ? colors.edge : colors.danger;
      ctx.lineWidth = e.kind === "note" ? 1.15 : 1;
      ctx.setLineDash(e.resolved ? [1.6, 9] : [3, 5]);
    }
    ctx.lineDashOffset = -flow;
    ctx.beginPath();
    ctx.moveTo(a.x + ux * ta * d, a.y + uy * ta * d);
    ctx.lineTo(b.x - ux * tb * d, b.y - uy * tb * d);
    ctx.stroke();
  }
  ctx.setLineDash([]);
  // 文本节点：黑字层次（被引多=前景色加粗，少=次要灰），常态无光晕保证清晰；
  // hover 才轻微发光；绘制坐标按设备像素对齐，消除缩放亚像素模糊。
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const tx = view.x * view.scale + W / 2;
  const ty = view.y * view.scale + H / 2;
  const k = view.scale;
  sim.nodes.forEach((p, i) => {
    const active = i === hover;
    const dim = hover >= 0 && !active && !sim.adj[hover].has(i);
    const kind = p.ref.kind;
    const invalid = !!p.ref.status && p.ref.status !== "resolved";
    const hub = p.weight >= 2;
    const color = invalid && kind !== "ghost" ? colors.danger
      : kind === "ghost" ? colors.muted
      : hub ? colors.fg : colors.fgSecondary;
    const size = p.size * (active ? 1.12 : 1);
    ctx.font = `${hub || active ? 600 : 500} ${size}px ${sim.font}`;
    ctx.globalAlpha = dim ? 0.15 : kind === "ghost" || invalid ? 0.85 : 1;
    ctx.fillStyle = color;
    if (active) {
      ctx.shadowColor = color;
      ctx.shadowBlur = 8;
    }
    const rx = (Math.round((p.x * k + tx) * dpr) / dpr - tx) / k;
    const ry = (Math.round((p.y * k + ty) * dpr) / dpr - ty) / k;
    ctx.fillText(p.label, rx, ry);
    ctx.shadowBlur = 0;
    // 幽灵/失效节点：虚线下划线提示「未解析」
    if (kind === "ghost" || invalid) {
      ctx.globalAlpha *= 0.7;
      ctx.strokeStyle = color;
      ctx.lineWidth = 0.8;
      ctx.setLineDash([2, 3]);
      ctx.beginPath();
      ctx.moveTo(rx - p.w / 2, ry + p.h / 2 + 1.5);
      ctx.lineTo(rx + p.w / 2, ry + p.h / 2 + 1.5);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  });
  ctx.globalAlpha = 1;
}

export function TextForceGraph({
  graph, folderNames, tr, home = false, noteCount = 0,
  onOpenCenter, onOpenNote, onCreateNote, onOpenSession, onOpenThread,
  onOpenTextbook, onGenerate,
}: {
  graph: NotesGraph | null;
  folderNames: Record<string, string>;
  tr: (k: string, fallback?: string) => string;
  home?: boolean;
  noteCount?: number;
  onOpenCenter?: () => void;
  onOpenNote: (id: string) => void;
  onCreateNote: (title: string) => void;
  onOpenSession?: (id: string) => void;
  onOpenThread?: (id: string) => void;
  onOpenTextbook?: () => void;
  onGenerate?: () => void;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const simRef = useRef<Sim>({ nodes: [], edges: [], adj: [], font: "sans-serif" });
  const viewRef = useRef({ x: 0, y: 0, scale: 1 });
  const colorsRef = useRef<Palette>({
    edge: "#d4d4d8", danger: "#ef4444", muted: "#9ca3af",
    fg: "#18181b", fgSecondary: "#52525b",
  });
  const hoverRef = useRef(-1);
  const dragRef = useRef<{
    mode: "pan" | "node"; index: number; moved: boolean;
    startX: number; startY: number; baseX: number; baseY: number;
  } | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const filters = useSyncExternalStore(subscribeFilter, readFilter, () => FILTER_DEFAULT);

  // 按开关过滤节点（笔记/幽灵始终保留；助手线程不进图）与两端可见的边
  const filtered = useMemo<NotesGraph>(() => {
    if (!graph) return { nodes: [], edges: [] };
    const keep = (n: GraphNode) =>
      n.kind === "note" || n.kind === "ghost" ||
      (filters.chats && n.kind === "session") ||
      (filters.textbooks && n.kind === "textbook");
    const nodes = graph.nodes.filter(keep);
    const ids = new Set(nodes.map((n) => n.id));
    return { nodes, edges: graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target)) };
  }, [graph, filters]);

  const degreeOf = useMemo(() => {
    const d: Record<string, number> = {};
    filtered.edges.forEach((e) => {
      d[e.source] = (d[e.source] || 0) + 1;
      d[e.target] = (d[e.target] || 0) + 1;
    });
    return d;
  }, [filtered]);
  const nodeById = useMemo(
    () => Object.fromEntries(filtered.nodes.map((n) => [n.id, n])) as Record<string, GraphNode>,
    [filtered]);
  const hovered = hoverId ? nodeById[hoverId] : undefined;

  const resolveColors = useCallback(() => {
    const cs = getComputedStyle(document.documentElement);
    const get = (name: string, fb: string) => cs.getPropertyValue(name).trim() || fb;
    colorsRef.current = {
      edge: get("--color-border", "#d4d4d8"),
      danger: get("--color-danger", "#ef4444"),
      muted: get("--color-muted", "#9ca3af"),
      fg: get("--color-fg", "#18181b"),
      fgSecondary: get("--color-fg-secondary", "#52525b"),
    };
  }, []);

  const fitView = useCallback((scaleCap = 1.15) => {
    const wrap = wrapRef.current;
    const sim = simRef.current;
    if (!wrap) return;
    if (!sim.nodes.length) {
      viewRef.current = { x: 0, y: 0, scale: 1 };
      return;
    }
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    sim.nodes.forEach((p) => {
      minX = Math.min(minX, p.x - p.w / 2);
      maxX = Math.max(maxX, p.x + p.w / 2);
      minY = Math.min(minY, p.y - p.h / 2);
      maxY = Math.max(maxY, p.y + p.h / 2);
    });
    const W = wrap.clientWidth || 900;
    const H = wrap.clientHeight || 600;
    const bw = Math.max(90, maxX - minX);
    const bh = Math.max(70, maxY - minY);
    const scale = Math.max(0.3, Math.min(scaleCap, Math.min((W - 72) / bw, (H - 72) / bh)));
    viewRef.current = { scale, x: -(minX + maxX) / 2, y: -(minY + maxY) / 2 };
  }, []);

  // 构建模拟：入度加权字号 → 量文本 → 黄金角螺旋散布 → 无漂移预收敛 → 适应视图
  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const font = GRAPH_FONT;
    const inDegree: Record<string, number> = {};
    filtered.edges.forEach((e) => {
      inDegree[e.target] = (inDegree[e.target] || 0) + 1;
    });
    const indexOf = new Map(filtered.nodes.map((n, i) => [n.id, i]));
    const nodes: SimNode[] = filtered.nodes.map((n, i) => {
      const weight = inDegree[n.id] || 0;
      const size = Math.max(11, Math.min(26, 12 + 4.6 * Math.sqrt(weight)));
      const label = n.title.length > 14 ? `${n.title.slice(0, 14)}…` : n.title || "—";
      ctx.font = `${weight >= 2 ? 600 : 500} ${size}px ${font}`;
      const angle = i * 2.399963;
      const r = 92 * Math.sqrt(i + 0.6);
      return {
        ref: n, label,
        w: Math.max(12, ctx.measureText(label).width),
        h: size * 1.3, size, weight,
        x: Math.cos(angle) * r, y: Math.sin(angle) * r * 0.72,
        vx: 0, vy: 0,
        phase: Math.random() * Math.PI * 2,
        speed: 0.14 + Math.random() * 0.22,
        held: false,
      };
    });
    const edges: SimEdge[] = [];
    filtered.edges.forEach((e) => {
      const a = indexOf.get(e.source);
      const b = indexOf.get(e.target);
      if (a === undefined || b === undefined || a === b) return;
      edges.push({ a, b, resolved: e.resolved, kind: e.kind });
    });
    const adj = nodes.map(() => new Set<number>());
    edges.forEach((e) => { adj[e.a].add(e.b); adj[e.b].add(e.a); });
    const sim: Sim = { nodes, edges, adj, font };
    for (let i = 0; i < 320; i++) tickSim(sim, 0, true);
    simRef.current = sim;
    hoverRef.current = -1;
    setHoverId(null);
    fitView();
  }, [filtered, fitView]);

  // 渲染循环：rAF 常驻（页面隐藏暂停）；主题切换时重解析 CSS 变量颜色
  useEffect(() => {
    resolveColors();
    const mo = new MutationObserver(resolveColors);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style"] });
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    let raf = 0;
    let running = true;
    const frame = () => {
      if (!running) return;
      const wrap = wrapRef.current;
      const canvas = canvasRef.current;
      if (wrap && canvas) {
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        const W = wrap.clientWidth;
        const H = wrap.clientHeight;
        if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
          canvas.width = Math.round(W * dpr);
          canvas.height = Math.round(H * dpr);
        }
        const t = performance.now() / 1000;
        const animate = !reduced.matches;
        if (animate) tickSim(simRef.current, t, false);
        drawSim(canvas, simRef.current, viewRef.current, colorsRef.current,
          hoverRef.current, t, animate);
      }
      raf = requestAnimationFrame(frame);
    };
    const onVis = () => {
      if (document.hidden) {
        running = false;
        cancelAnimationFrame(raf);
      } else if (!running) {
        running = true;
        raf = requestAnimationFrame(frame);
      }
    };
    document.addEventListener("visibilitychange", onVis);
    raf = requestAnimationFrame(frame);
    return () => {
      running = false;
      cancelAnimationFrame(raf);
      mo.disconnect();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [resolveColors]);

  const zoomAt = useCallback((sx: number, sy: number, factor: number) => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const v = viewRef.current;
    const scale = Math.max(0.3, Math.min(2.8, v.scale * factor));
    const wx = (sx - wrap.clientWidth / 2) / v.scale - v.x;
    const wy = (sy - wrap.clientHeight / 2) / v.scale - v.y;
    viewRef.current = {
      scale,
      x: (sx - wrap.clientWidth / 2) / scale - wx,
      y: (sy - wrap.clientHeight / 2) / scale - wy,
    };
  }, []);
  const zoomCenter = useCallback((factor: number) => {
    const wrap = wrapRef.current;
    if (wrap) zoomAt(wrap.clientWidth / 2, wrap.clientHeight / 2, factor);
  }, [zoomAt]);

  const toWorld = useCallback((sx: number, sy: number) => {
    const wrap = wrapRef.current;
    const v = viewRef.current;
    const W = wrap?.clientWidth || 0;
    const H = wrap?.clientHeight || 0;
    return { x: (sx - W / 2) / v.scale - v.x, y: (sy - H / 2) / v.scale - v.y };
  }, []);

  const pickNode = useCallback((sx: number, sy: number): number => {
    const { x, y } = toWorld(sx, sy);
    const nodes = simRef.current.nodes;
    for (let i = nodes.length - 1; i >= 0; i--) {
      const p = nodes[i];
      if (Math.abs(x - p.x) <= p.w / 2 + 5 && Math.abs(y - p.y) <= p.h / 2 + 4) return i;
    }
    return -1;
  }, [toWorld]);

  const activate = useCallback((n: GraphNode) => {
    if (n.status && n.status !== "resolved") {
      window.alert(n.status === "deleted"
        ? tr("graph.alert.deleted") : tr("graph.alert.missing"));
      if (n.kind === "ghost" && n.status === "unresolved") onCreateNote(n.title);
      return;
    }
    if (n.kind === "note") onOpenNote(n.resource_id || n.id);
    else if (n.kind === "session") onOpenSession?.(n.resource_id || n.id.replace(/^session:/, ""));
    else if (n.kind === "notes_thread") onOpenThread?.(n.resource_id || n.id.replace(/^notes_thread:/, ""));
    else if (n.kind === "textbook") onOpenTextbook?.();
    else onCreateNote(n.title);
  }, [tr, onOpenNote, onCreateNote, onOpenSession, onOpenThread, onOpenTextbook]);

  const canvasPos = (e: React.PointerEvent | React.MouseEvent) => {
    const rect = canvasRef.current?.getBoundingClientRect();
    return { x: e.clientX - (rect?.left || 0), y: e.clientY - (rect?.top || 0) };
  };

  const isEmpty = graph !== null && filtered.nodes.length === 0;

  return (
    <div ref={wrapRef} className="relative min-h-0 flex-1 overflow-hidden bg-bg">
      <canvas
        ref={canvasRef}
        className="absolute inset-0 h-full w-full touch-none select-none"
        onPointerDown={(e) => {
          (e.currentTarget as HTMLCanvasElement).setPointerCapture(e.pointerId);
          const pos = canvasPos(e);
          const hit = pickNode(pos.x, pos.y);
          if (hit >= 0) {
            const p = simRef.current.nodes[hit];
            p.held = true;
            p.vx = 0;
            p.vy = 0;
            const w = toWorld(pos.x, pos.y);
            dragRef.current = { mode: "node", index: hit, moved: false, startX: pos.x, startY: pos.y, baseX: w.x - p.x, baseY: w.y - p.y };
          } else {
            dragRef.current = { mode: "pan", index: -1, moved: false, startX: pos.x, startY: pos.y, baseX: viewRef.current.x, baseY: viewRef.current.y };
          }
        }}
        onPointerMove={(e) => {
          const pos = canvasPos(e);
          const drag = dragRef.current;
          if (drag) {
            if (Math.abs(e.clientX - drag.startX) + Math.abs(e.clientY - drag.startY) > 4) drag.moved = true;
            if (drag.mode === "node") {
              const p = simRef.current.nodes[drag.index];
              if (p) {
                const w = toWorld(pos.x, pos.y);
                p.x = w.x - drag.baseX;
                p.y = w.y - drag.baseY;
                p.vx = 0;
                p.vy = 0;
              }
            } else {
              const v = viewRef.current;
              viewRef.current = {
                ...v,
                x: drag.baseX + (pos.x - drag.startX) / v.scale,
                y: drag.baseY + (pos.y - drag.startY) / v.scale,
              };
            }
            e.currentTarget.style.cursor = "grabbing";
            return;
          }
          const hit = pickNode(pos.x, pos.y);
          if (hit !== hoverRef.current) {
            hoverRef.current = hit;
            setHoverId(hit >= 0 ? simRef.current.nodes[hit]?.ref.id ?? null : null);
          }
          e.currentTarget.style.cursor = hit >= 0 ? "pointer" : "grab";
        }}
        onPointerUp={(e) => {
          const drag = dragRef.current;
          dragRef.current = null;
          if (drag?.mode === "node" && drag.index >= 0) {
            const p = simRef.current.nodes[drag.index];
            if (p) p.held = false;
          }
          e.currentTarget.style.cursor = "grab";
          if (!drag || drag.moved) return;
          const pos = canvasPos(e);
          const hit = pickNode(pos.x, pos.y);
          if (hit >= 0) {
            const n = simRef.current.nodes[hit]?.ref;
            if (n) activate(n);
          }
        }}
        onPointerLeave={() => {
          hoverRef.current = -1;
          setHoverId(null);
        }}
        onWheel={(e) => {
          e.preventDefault();
          const pos = canvasPos(e);
          zoomAt(pos.x, pos.y, e.deltaY > 0 ? 0.9 : 1.11);
        }}
      />

      {/* 浮动工具条：类型开关（笔记/幽灵始终保留） */}
      <div className="absolute left-3 top-3 flex flex-wrap items-center gap-1.5">
        <FilterChip
          icon={<MessageSquareText size={12} />}
          label={tr("graph.toggle.chats")}
          dot="var(--color-info)"
          active={filters.chats}
          onClick={() => writeFilter({ ...filters, chats: !filters.chats })}
        />
        <FilterChip
          icon={<BookOpen size={12} />}
          label={tr("graph.toggle.textbooks")}
          dot="var(--color-warning)"
          active={filters.textbooks}
          onClick={() => writeFilter({ ...filters, textbooks: !filters.textbooks })}
        />
      </div>

      {/* 右侧控制：缩放 */}
      <div className="absolute right-3 top-3 flex items-center gap-1.5">
        <div className="flex items-center gap-0.5 rounded-full border border-border bg-surface/85 px-1 py-0.5 text-muted shadow-sm backdrop-blur">
          <CtrlBtn title={tr("graph.zoom.out")} onClick={() => zoomCenter(0.85)}><Minus size={13} /></CtrlBtn>
          <CtrlBtn title={tr("graph.zoom.in")} onClick={() => zoomCenter(1.18)}><Plus size={13} /></CtrlBtn>
          <CtrlBtn title={tr("graph.zoom.fit")} onClick={() => fitView(1.15)}><Maximize2 size={13} /></CtrlBtn>
          <CtrlBtn title={tr("graph.zoom.reset")} onClick={() => { viewRef.current = { x: 0, y: 0, scale: 1 }; }}><RotateCcw size={13} /></CtrlBtn>
        </div>
      </div>

      {/* 底部居中玻璃 dock：笔记中心入口 + 快速新建 */}
      {onOpenCenter && (
        <div className="absolute bottom-5 left-1/2 flex -translate-x-1/2 items-center gap-1 rounded-full border border-border/60 bg-surface/70 p-1.5 shadow-xl backdrop-blur-md">
          <button
            onClick={onOpenCenter}
            title={tr("center.title")}
            className="flex cursor-pointer items-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs font-medium text-fg transition-colors hover:bg-surface-hover hover:text-accent"
          >
            <FolderOpen size={13} className="text-accent" />
            {tr("center.title")}
            {noteCount > 0 && <span className="tnum text-[10px] text-muted">{noteCount}</span>}
          </button>
          <div className="h-4 w-px bg-border/60" />
          <button
            onClick={() => onCreateNote("")}
            title={tr("notes.new")}
            aria-label={tr("notes.new")}
            className="flex size-7 cursor-pointer items-center justify-center rounded-full text-accent transition-colors hover:bg-accent-soft"
          >
            <Plus size={14} />
          </button>
        </div>
      )}

      {/* 操作提示 */}
      {!isEmpty && (
        <div className="pointer-events-none absolute bottom-3 right-3 text-[10px] text-muted/70">
          {tr("graph.hint")}
        </div>
      )}

      {/* hover 信息卡 */}
      {hovered && (
        <div className="pointer-events-none absolute bottom-4 left-4 max-w-xs rounded-lg border border-border bg-surface/95 px-3 py-2 text-xs shadow-lg">
          <div className="font-medium text-fg">{hovered.title}</div>
          <div className="mt-1 text-[11px] text-muted">
            {tr("graph.info.kind")}：{tr(`graph.kind.${hovered.kind}`)} · {tr("graph.info.degree")}：{degreeOf[hovered.id] || 0}
          </div>
          {hovered.folder_id && (
            <div className="text-[11px] text-muted">
              {tr("graph.info.folder")}：{folderNames[hovered.folder_id] || hovered.folder_name || tr("notes.unfiled")}
            </div>
          )}
          {hovered.message_count !== undefined && (
            <div className="text-[11px] text-muted">{tr("graph.info.messages")}：{hovered.message_count}</div>
          )}
          {hovered.status && hovered.status !== "resolved" && (
            <div className="text-[11px] text-danger">
              {hovered.status === "deleted" ? tr("graph.status.deleted") : tr("graph.status.missing")}
            </div>
          )}
        </div>
      )}

      {/* 加载中 / 空仓库（容器不拦截点击，按钮区恢复交互） */}
      {graph === null && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-xs text-muted">
          {tr("graph.loading")}
        </div>
      )}
      {isEmpty && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-6">
          <div className="pointer-events-auto">
            {home ? (
              <EmptyState
                icon={<NotebookPen size={22} />}
                title={tr("notes.empty.title")}
                desc={tr("notes.empty.desc")}
                action={
                  <div className="flex items-center gap-2">
                    <Button size="sm" variant="outline" onClick={() => onCreateNote("")}>
                      {tr("notes.new")}
                    </Button>
                    {onGenerate && (
                      <Button size="sm" variant="accent2" icon={<Sparkles size={13} />} onClick={onGenerate}>
                        {tr("notes.generate")}
                      </Button>
                    )}
                  </div>
                }
              />
            ) : (
              <div className="text-xs text-muted">{tr("graph.empty")}</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function FilterChip({
  icon, label, dot, active, onClick,
}: {
  icon: React.ReactNode;
  label: string;
  dot: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] shadow-sm backdrop-blur transition-colors",
        active
          ? "border-border bg-surface/85 text-fg"
          : "border-dashed border-border bg-surface/60 text-muted hover:text-fg",
      )}
    >
      <span className="flex items-center gap-1.5">
        <span className={cn("size-1.5 rounded-full", !active && "opacity-40")} style={{ background: dot }} />
        {icon}
      </span>
      {label}
    </button>
  );
}

function CtrlBtn({
  title, onClick, children,
}: {
  title: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      title={title}
      aria-label={title}
      onClick={onClick}
      className="cursor-pointer rounded-full p-1 transition-colors hover:bg-surface-hover hover:text-fg"
    >
      {children}
    </button>
  );
}
