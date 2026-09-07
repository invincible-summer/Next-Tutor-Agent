// MarkdownEditor 的纯函数层：对 textarea 选区做包装/行前缀/插入。
// 应用层用 document.execCommand("insertText")（见 MarkdownEditor），这里只算
// 出目标文本与选区，保持可单测。

export interface EditState {
  value: string;
  start: number;
  end: number;
}

export type EditResult = EditState;

/** 用给定文本替换选区（或光标处插入），返回新值与选中区间。 */
export function replaceRange(
  s: EditState,
  text: string,
  select?: [number, number],
): EditResult {
  const value = s.value.slice(0, s.start) + text + s.value.slice(s.end);
  if (select) return { value, start: s.start + select[0], end: s.start + select[1] };
  return { value, start: s.start + text.length, end: s.start + text.length };
}

/** 选区两侧包裹（无选中文本时插入占位并选中，便于直接输入）。 */
export function wrapSelection(s: EditState, before: string, after: string,
                              placeholder = ""): EditResult {
  const selected = s.value.slice(s.start, s.end) || placeholder;
  const text = before + selected + after;
  const value = s.value.slice(0, s.start) + text + s.value.slice(s.end);
  return {
    value,
    start: s.start + before.length,
    end: s.start + before.length + selected.length,
  };
}

/** 行前缀切换：选中跨多行时对每行操作；前缀已存在则移除（toggle）。 */
export function toggleLinePrefix(s: EditState, prefix: string,
                                 numbered = false): EditResult {
  const value = s.value;
  const lineStart = value.lastIndexOf("\n", s.start - 1) + 1;
  let lineEnd = value.indexOf("\n", s.end);
  if (lineEnd === -1) lineEnd = value.length;
  // 扩展到完整行（选区可能从行中开始）
  const block = value.slice(lineStart, lineEnd);
  const lines = block.split("\n");
  const allHave = lines.every((l) =>
    numbered ? /^\d+\.\s/.test(l) : l.startsWith(prefix));
  const out = lines.map((l, i) => {
    if (numbered) {
      if (allHave) return l.replace(/^\d+\.\s/, "");
      return `${i + 1}. ${l}`;
    }
    if (allHave) return l.slice(prefix.length);
    return prefix + l;
  }).join("\n");
  const next = value.slice(0, lineStart) + out + value.slice(lineEnd);
  return { value: next, start: lineStart, end: lineStart + out.length };
}

/** 在光标前插入块级片段（保证其独占行）。 */
export function insertBlock(s: EditState, block: string): EditResult {
  const before = s.value.slice(0, s.start);
  const needsLeading = before.length > 0 && !before.endsWith("\n\n")
    ? (before.endsWith("\n") ? "\n" : "\n\n")
    : "";
  const text = needsLeading + block;
  const value = s.value.slice(0, s.start) + text + s.value.slice(s.end);
  const caret = s.start + text.length;
  return { value, start: caret, end: caret };
}

export const GFM_TABLE = [
  "| 列一 | 列二 | 列三 |",
  "| --- | --- | --- |",
  "|  |  |  |",
].join("\n");

/** 生成 markdown 表格的插入态。 */
export function tableSnippet(s: EditState): EditResult {
  return insertBlock(s, GFM_TABLE);
}

/** 供工具栏按钮用的编辑动作描述。 */
export type EditorAction =
  | "bold" | "italic" | "strike" | "code" | "codeblock"
  | "h1" | "h2" | "h3" | "quote" | "ul" | "ol" | "task"
  | "table" | "hr" | "link" | "image" | "math" | "mathblock"
  | "wikilink";

export function applyAction(s: EditState, action: EditorAction): EditResult {
  switch (action) {
    case "bold": return wrapSelection(s, "**", "**", "加粗文本");
    case "italic": return wrapSelection(s, "*", "*", "斜体文本");
    case "strike": return wrapSelection(s, "~~", "~~", "删除文本");
    case "code": return wrapSelection(s, "`", "`", "code");
    case "codeblock": return insertBlock(s, "```\n代码\n```");
    case "h1": return toggleLinePrefix(s, "# ");
    case "h2": return toggleLinePrefix(s, "## ");
    case "h3": return toggleLinePrefix(s, "### ");
    case "quote": return toggleLinePrefix(s, "> ");
    case "ul": return toggleLinePrefix(s, "- ");
    case "ol": return toggleLinePrefix(s, "1. ", true);
    case "task": return toggleLinePrefix(s, "- [ ] ");
    case "table": return tableSnippet(s);
    case "hr": return insertBlock(s, "---");
    case "link": return wrapSelection(s, "[", "](https://)", "链接文字");
    case "image": return replaceRange(s, "![图片描述](https://)");
    case "math": return wrapSelection(s, "$", "$", "公式");
    case "mathblock": return insertBlock(s, "$$\n公式\n$$");
    case "wikilink": return wrapSelection(s, "[[", "]]", "笔记标题");
  }
}

// --- 轻量 LCS 行 diff（建议卡对比用，零依赖）--------------------------------

export interface DiffLine {
  kind: "same" | "add" | "del";
  text: string;
}

export function lineDiff(a: string, b: string, context = 2): DiffLine[] {
  const A = a.split("\n");
  const B = b.split("\n");
  const n = A.length;
  const m = B.length;
  // LCS 动态规划（笔记场景行数可控；超大文本走全文对比截断）
  const dp: number[][] = Array.from({ length: n + 1 },
    () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = A[i] === B[j]
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const raw: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) {
      raw.push({ kind: "same", text: A[i] });
      i++; j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      raw.push({ kind: "del", text: A[i++] });
    } else {
      raw.push({ kind: "add", text: B[j++] });
    }
  }
  while (i < n) raw.push({ kind: "del", text: A[i++] });
  while (j < m) raw.push({ kind: "add", text: B[j++] });
  // 只保留变更行 ± context，避免长笔记 diff 爆屏
  const changed = raw.map((l, idx) => (l.kind === "same" ? -1 : idx))
    .filter((idx) => idx >= 0);
  if (changed.length === 0) return [];
  const keep = new Set<number>();
  for (const idx of changed) {
    for (let k = idx - context; k <= idx + context; k++) {
      if (k >= 0 && k < raw.length) keep.add(k);
    }
  }
  const out: DiffLine[] = [];
  let prev = -1;
  for (let k = 0; k < raw.length; k++) {
    if (!keep.has(k)) continue;
    if (prev !== -1 && k > prev + 1) out.push({ kind: "same", text: "…" });
    out.push(raw[k]);
    prev = k;
  }
  return out;
}
