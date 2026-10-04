// 知识图谱布局几何已迁入共享包 @next-tutor/domain（跨端复用，无平台 API）。
// 本文件保留 Web 表现层的边配色（CSS 令牌映射），并 re-export 布局 API
// 以维持既有导入路径不变。
export {
  COL_GAP,
  LAYER_GAP,
  NODE_H,
  PAD,
  edgePath,
  fitLabel,
  layoutDag,
  measure,
  nodeWidth,
  splitLabel,
} from "@next-tutor/domain";
export type { DagLayout, LayoutEdge, LayoutItem } from "@next-tutor/domain";
import type { KnowledgeEdge } from "@/lib/types-modules";

/** 边类型 → SVG 样式（Web 设计令牌；移动端按平台令牌自行映射）。 */
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

// 保持旧签名对 KnowledgeEdge 的结构兼容（domain 只要求 from/to/type）。
export type GraphLayoutEdge = Pick<KnowledgeEdge, "from" | "to" | "type">;
