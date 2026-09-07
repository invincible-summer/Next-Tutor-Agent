/* 从 markdown 原文提取 h1–h3 目录，并给出与渲染端一致的标题锚点 slug。
 * 渲染端（chat/markdown.tsx 的 anchorHeadings）对 h1–h3 用同一 slugifyHeading
 * 生成 id，因此这里解析出的 id 能精确链接到对应标题。
 * 注意：slug 不去重——同名标题会命中第一个锚点（文档由管理员维护，属可接受边界）。 */

export interface TocItem {
  depth: 1 | 2 | 3;
  text: string;
  id: string;
}

/** 标题锚点：小写、空白折叠为连字符、去掉强调/代码等标记与标点，保留 CJK。 */
export function slugifyHeading(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "-")
    .replace(/[^\p{L}\p{N}-]+/gu, "")
    .replace(/-+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** 去掉行内 markdown 语法（链接/强调/代码/数学定界符），保留纯文本。 */
function stripInline(line: string): string {
  return line
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/(?:\*\*|__|\*|_|~|`|\$)+/g, "")
    .trim();
}

/** 解析 markdown 的 h1–h3 标题（跳过围栏代码块）为目录项。 */
export function extractToc(markdown: string): TocItem[] {
  const items: TocItem[] = [];
  let fence: string | null = null;
  for (const line of markdown.split("\n")) {
    const fenceMatch = line.match(/^\s*(`{3,}|~{3,})/);
    if (fenceMatch) {
      if (!fence) fence = fenceMatch[1];
      else if (fenceMatch[1].startsWith(fence)) fence = null;
      continue;
    }
    if (fence) continue;
    const heading = line.match(/^(#{1,3})\s+(.+?)\s*#*\s*$/);
    if (!heading) continue;
    const text = stripInline(heading[2]);
    if (!text) continue;
    items.push({ depth: heading[1].length as 1 | 2 | 3, text, id: slugifyHeading(text) });
  }
  return items;
}
