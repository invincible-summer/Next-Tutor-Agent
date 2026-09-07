// 知识谱系搜索的唯一匹配实现：name / id / aliases 大小写不敏感子串匹配，
// 按命中质量排序（名称前缀 > 名称子串 > 别名 > id）。章节容器不参与搜索；
// 节（课/篇目）参与——篇目名正是用户最常搜的。匹配同时做压缩归一比较
// （去空白/间隔号/标点），「沁园春江畔」能命中「沁园春·江畔」。
import type { KnowledgeNode, KnowledgeTaxonomyGroup } from "@/lib/types-modules";

/** 结果面板最多展示的条数（超出截断，提示输入更精确的关键词）。 */
export const SEARCH_RESULT_LIMIT = 30;

type Score = 0 | 1 | 2 | 3 | 4;

/** 标题压缩归一：NFKC 等价的 Latin/CJK + 数字，去空白/间隔号/常用标点。 */
export function foldTitle(s: string): string {
  return (s ?? "").toLowerCase()
    .replace(/[\s·・‧﹒．。、，,：:；;！!？?（）()[\]【】「」『』《》〈〉“”"'‘’\-—_~～*]+/g, "");
}

function scoreNode(n: KnowledgeNode, q: string, qFolded: string): Score {
  const name = n.name.toLowerCase();
  if (name.startsWith(q)) return 4;
  if (name.includes(q)) return 3;
  if (qFolded && foldTitle(n.name).includes(qFolded)) return 3;
  if ((n.aliases ?? []).some((a) => a.toLowerCase().includes(q) || (qFolded && foldTitle(a).includes(qFolded)))) return 2;
  if (n.id.toLowerCase().includes(q)) return 1;
  return 0;
}

/** 搜索一组节点（当前学段全部概念与节），返回按命中质量降序的匹配数组。 */
export function searchConcepts(nodes: KnowledgeNode[], raw: string): KnowledgeNode[] {
  const q = raw.trim().toLowerCase();
  if (!q) return [];
  const qFolded = foldTitle(raw.trim());
  const scored: { n: KnowledgeNode; s: Score }[] = [];
  for (const n of nodes) {
    if (n.kind === "chapter") continue;
    const s = scoreNode(n, q, qFolded);
    if (s > 0) scored.push({ n, s });
  }
  scored.sort((a, b) => b.s - a.s || a.n.name.localeCompare(b.n.name, "zh-Hans-CN"));
  return scored.map((x) => x.n);
}

/** 按 node_prefix 前缀（长前缀优先）反查概念所属的教材组。 */
export function groupOfNode(
  nodeId: string,
  groupsByPrefix: KnowledgeTaxonomyGroup[],
): KnowledgeTaxonomyGroup | null {
  return groupsByPrefix.find((g) => nodeId.startsWith(g.node_prefix)) ?? null;
}
