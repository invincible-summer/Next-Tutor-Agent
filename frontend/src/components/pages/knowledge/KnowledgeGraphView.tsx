"use client";
// 知识图谱画布：分层 DAG 布局（前置边最长路径分层）+ 滚轮缩放 + 拖拽平移。
// 纯 SVG 实现，不依赖任何图表库；颜色全部走设计令牌。
// 章节视图复用本组件：nodeH + subtitle 渲染章节卡片，matchedIds 覆盖搜索匹配。
// 搜索匹配与排序统一在 ./search.ts，本组件只负责高亮 / 压暗与 focusId 居中。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RotateCcw } from "lucide-react";
import type { KnowledgeEdge, KnowledgeNode } from "@/lib/types-modules";
import { dt, stateColor } from "@/lib/labels";
import type { Lang } from "@/lib/i18n";
import { NODE_H, edgePath, edgeStyle, layoutDag, nodeWidth, splitLabel } from "./graph-layout";
// 两行标签所需节点盒高（单行 28 / 章节卡 40 之上按需加高）
const TWO_LINE_NODE_H = 54;

const MIN_K = 0.4;
const MAX_K = 2.5;

interface View {
  k: number;
  x: number;
  y: number;
}

const LEGEND: { type: string; dash?: string; stroke: string }[] = [
  { type: "prerequisite", stroke: "rgb(var(--fg-secondary))" },
  { type: "related", dash: "5 4", stroke: "rgb(var(--muted))" },
  { type: "application", dash: "1.5 5", stroke: "rgb(var(--accent))" },
  { type: "misconception", dash: "6 4", stroke: "rgb(var(--danger))" },
];

export function KnowledgeGraphView({
  nodes,
  edges,
  hasQuery,
  focusId,
  prereqOnly,
  selectedId,
  onSelect,
  lang,
  resetLabel,
  nodeH = NODE_H,
  subtitle,
  matchedIds,
}: {
  nodes: KnowledgeNode[];
  edges: KnowledgeEdge[];
  /** 是否处于搜索态（命中高亮、其余压暗） */
  hasQuery: boolean;
  /** 需要居中定位的节点 id（搜索结果跳转 / ‹› 循环切换） */
  focusId?: string | null;
  prereqOnly: boolean;
  selectedId: string | null;
  onSelect: (n: KnowledgeNode) => void;
  lang: Lang;
  resetLabel: string;
  /** 节点盒高：默认 28（概念），章节卡片传更大值放双行文本 */
  nodeH?: number;
  /** 节点副标题（章节卡片的概念数）；返回 undefined 则单行渲染 */
  subtitle?: (n: KnowledgeNode) => string | undefined;
  /** 搜索命中集合（页面经 search.ts 统一计算；章节视图传聚合到章节的集合） */
  matchedIds: Set<string>;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<View>({ k: 1, x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ px: number; py: number; moved: boolean } | null>(null);
  // 任一名超宽 → 该节点两行渲染，整图节点盒统一加高（分层间距同步增大）。
  const effNodeH = useMemo(() => {
    const need = nodes.some((n) => splitLabel(n.name, nodeWidth(n.name) - 26).length === 2);
    return need ? Math.max(nodeH, TWO_LINE_NODE_H) : nodeH;
  }, [nodes, nodeH]);
  const layout = useMemo(() => layoutDag(nodes, edges, effNodeH), [nodes, edges, effNodeH]);
  const matched = matchedIds;

  const visEdges = useMemo(
    () => edges.filter((e) => (!prereqOnly || e.type === "prerequisite") && layout.byId.has(e.from) && layout.byId.has(e.to)),
    [edges, prereqOnly, layout],
  );

  /** 适应视图：把整图缩放居中到容器。 */
  const fit = useCallback(() => {
    const el = ref.current;
    if (!el || layout.w === 0 || layout.h === 0) return;
    const r = el.getBoundingClientRect();
    const k = Math.min(1.1, Math.max(MIN_K, Math.min(r.width / layout.w, r.height / layout.h)));
    setView({ k, x: (r.width - layout.w * k) / 2, y: (r.height - layout.h * k) / 2 });
  }, [layout]);

  useEffect(() => {
    fit();
  }, [fit]);

  // 滚轮缩放（React 根事件是 passive，必须原生监听才能 preventDefault）
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const px = e.clientX - r.left;
      const py = e.clientY - r.top;
      setView((v) => {
        const k = Math.min(MAX_K, Math.max(MIN_K, v.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
        const wx = (px - v.x) / v.k;
        const wy = (py - v.y) / v.k;
        return { k, x: px - wx * k, y: py - wy * k };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // 搜索定位：居中到 focusId 节点（布局变化后也重新居中，覆盖筛选切换场景）
  const focusItem = focusId ? layout.byId.get(focusId) : undefined;
  useEffect(() => {
    const el = ref.current;
    if (!el || !focusItem) return;
    const r = el.getBoundingClientRect();
    setView((v) => {
      const k = Math.min(MAX_K, Math.max(0.9, v.k));
      return { k, x: r.width / 2 - focusItem.cx * k, y: r.height / 2 - focusItem.cy * k };
    });
  }, [focusItem]);

  return (
    <div
      ref={ref}
      className="relative h-full w-full overflow-hidden rounded-[10px] border border-border bg-surface"
    >
      <svg
        className="h-full w-full touch-none select-none"
        style={{ cursor: dragging ? "grabbing" : "grab" }}
        onPointerDown={(e) => {
          drag.current = { px: e.clientX, py: e.clientY, moved: false };
          (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
          setDragging(true);
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d) return;
          const dx = e.clientX - d.px;
          const dy = e.clientY - d.py;
          if (!d.moved && Math.abs(dx) + Math.abs(dy) < 4) return;
          d.moved = true;
          d.px = e.clientX;
          d.py = e.clientY;
          setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy }));
        }}
        onPointerUp={(e) => {
          const d = drag.current;
          drag.current = null;
          setDragging(false);
          // 点击选中放在 pointerup 命中测试里：指针捕获会把 click 重定向到 svg，
          // 导致节点 <g> 的 onClick 在 Chromium 下不触发。
          if (!d || d.moved) return;
          const r = e.currentTarget.getBoundingClientRect();
          const wx = (e.clientX - r.left - view.x) / view.k;
          const wy = (e.clientY - r.top - view.y) / view.k;
          const hit = layout.items.find(
            (it) => Math.abs(wx - it.cx) <= it.w / 2 && Math.abs(wy - it.cy) <= effNodeH / 2,
          );
          if (hit) onSelect(hit.n);
        }}
        onPointerLeave={() => {
          drag.current = null;
          setDragging(false);
        }}
      >
        <defs>
          <marker
            id="kg-arrow"
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 0.5 L 7.5 4 L 0 7.5 z" fill="rgb(var(--fg-secondary))" fillOpacity="0.7" />
          </marker>
        </defs>
        <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
          {visEdges.map((e, i) => {
            const a = layout.byId.get(e.from);
            const b = layout.byId.get(e.to);
            if (!a || !b) return null;
            const st = edgeStyle(e.type);
            const dim = hasQuery && (!matched.has(e.from) || !matched.has(e.to));
            return (
              <path
                key={`${e.from}-${e.to}-${e.type}-${i}`}
                d={edgePath(a.cx, a.cy + effNodeH / 2, b.cx, b.cy - effNodeH / 2)}
                fill="none"
                stroke={st.stroke}
                strokeWidth={1.4}
                strokeDasharray={st.dash}
                strokeLinecap="round"
                opacity={dim ? 0.12 : st.opacity}
                markerEnd={st.arrow ? "url(#kg-arrow)" : undefined}
              />
            );
          })}
          {layout.items.map((it) => {
            const n = it.n;
            const hit = matched.has(n.id);
            const dim = hasQuery && !hit;
            const selected = selectedId === n.id;
            const fill = stateColor(n.mastery?.state ?? "");
            const diff = Math.min(5, Math.max(1, Math.round(n.difficulty || 1)));
            const sub = subtitle?.(n);
            const lines = splitLabel(n.name, Math.max(60, it.w - 26));
            const twoLine = lines.length === 2;
            return (
              <g
                key={n.id}
                transform={`translate(${it.cx - it.w / 2} ${it.cy - effNodeH / 2})`}
                opacity={dim ? 0.28 : 1}
                className="cursor-pointer"
              >
                <title>{`${n.name} · ${n.subject} · ${dt(lang, `state.${n.mastery?.state ?? "unknown"}`)}`}</title>
                {selected && (
                  <rect x={-3} y={-3} width={it.w + 6} height={effNodeH + 6} rx={9} fill="none" stroke="rgb(var(--accent))" strokeWidth={2.4} />
                )}
                {!selected && hit && hasQuery && (
                  <rect x={-3} y={-3} width={it.w + 6} height={effNodeH + 6} rx={9} fill="none" stroke="rgb(var(--accent2))" strokeWidth={2} />
                )}
                <rect width={it.w} height={effNodeH} rx={6} fill={fill} />
                {/* 难度：左侧白色刻度条，越难越粗 */}
                <rect x={2.5} y={4} width={1.5 + diff * 1.3} height={effNodeH - 8} rx={1.5} fill="rgb(255 255 255)" fillOpacity={0.38} />
                {(() => {
                  if (!twoLine) {
                    return (
                      <text
                        x={it.w / 2}
                        y={sub ? effNodeH / 2 - 7 : effNodeH / 2 + 0.5}
                        textAnchor="middle"
                        dominantBaseline="central"
                        fontSize={11}
                        className="fill-white font-medium"
                        style={{ pointerEvents: "none" }}
                      >
                        {lines[0]}
                      </text>
                    );
                  }
                  return (
                    <>
                      <text
                        x={it.w / 2}
                        y={effNodeH / 2 - 9}
                        textAnchor="middle"
                        dominantBaseline="central"
                        fontSize={11}
                        className="fill-white font-medium"
                        style={{ pointerEvents: "none" }}
                      >
                        {lines[0]}
                      </text>
                      <text
                        x={it.w / 2}
                        y={effNodeH / 2 + 7}
                        textAnchor="middle"
                        dominantBaseline="central"
                        fontSize={11}
                        className="fill-white font-medium"
                        style={{ pointerEvents: "none" }}
                      >
                        {lines[1]}
                      </text>
                    </>
                  );
                })()}
                {sub && (
                  <text
                    x={it.w / 2}
                    y={twoLine ? effNodeH / 2 + 17.5 : effNodeH / 2 + 8.5}
                    textAnchor="middle"
                    dominantBaseline="central"
                    fontSize={9.5}
                    className="fill-white"
                    fillOpacity={0.85}
                    style={{ pointerEvents: "none" }}
                  >
                    {sub}
                  </text>
                )}
              </g>
            );
          })}
        </g>
      </svg>

      {/* 图例：固定左下 */}
      <div className="absolute bottom-3 left-3 flex flex-col gap-1.5 rounded-[8px] border border-border bg-surface/90 px-3 py-2 backdrop-blur-sm">
        {LEGEND.map((it) => (
          <div key={it.type} className="flex items-center gap-2">
            <svg width="26" height="6" aria-hidden>
              <line x1="1" y1="3" x2="25" y2="3" stroke={it.stroke} strokeWidth="1.6" strokeDasharray={it.dash} strokeLinecap="round" />
            </svg>
            <span className="text-[10px] leading-none text-fg-secondary">{dt(lang, `edge.${it.type}`)}</span>
          </div>
        ))}
      </div>

      {/* 复位视图 */}
      <button
        onClick={fit}
        title={resetLabel}
        className="absolute right-3 top-3 flex h-7 cursor-pointer items-center gap-1 rounded-[7px] border border-border bg-surface/90 px-2 text-[11px] text-fg-secondary backdrop-blur-sm hover:border-accent hover:text-accent"
      >
        <RotateCcw size={12} />
        {resetLabel}
      </button>
    </div>
  );
}
