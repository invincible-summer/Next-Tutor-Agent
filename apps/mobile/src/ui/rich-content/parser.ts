/**
 * 聊天富文本解析器（Markdown 子集 + TeX 占位）：纯函数、零依赖、可独立单测。
 * 语义对齐 Web 端 react-markdown + remark-gfm + remark-math（含 \\(...\\) / \\[...\\]
 * 归一化），但面向 RN 做了简化：表格/列表嵌套只支持一层、不支持 HTML 与 setext 标题。
 * 流式容忍：未闭合的 ``` 收到 EOF 按代码块收尾；未闭合的 $$ / $ 按字面文本；
 * 不完整的表格退化为段落；任何输入都不抛异常。
 */

// ---------- AST ----------

export type RichInline =
  | { type: "text"; text: string }
  | { type: "bold"; children: RichInline[] }
  | { type: "italic"; children: RichInline[] }
  | { type: "strike"; children: RichInline[] }
  | { type: "code"; text: string }
  | { type: "math"; tex: string }
  | { type: "link"; label: RichInline[]; url: string }
  | { type: "break" };

export interface RichListItem {
  /** 有序项保留原始编号（如 "3."），无序项归一化为 "•"。 */
  marker: string;
  ordered: boolean;
  content: RichInline[];
  /** 一层嵌套（2/4 空格缩进）；嵌套项自身不再有 children。 */
  children: RichListItem[];
}

export type TableAlign = "left" | "center" | "right";

export type RichBlock =
  | { type: "heading"; level: 1 | 2 | 3 | 4; children: RichInline[] }
  | { type: "paragraph"; children: RichInline[] }
  | { type: "code"; language: string; code: string }
  | { type: "quote"; blocks: RichBlock[] }
  | { type: "hr" }
  | { type: "list"; items: RichListItem[] }
  | {
      type: "table";
      header: RichInline[][];
      align: TableAlign[];
      rows: RichInline[][][];
    }
  | { type: "math"; tex: string };

// ---------- 通用工具 ----------

const ASCII_PUNCT = /^[!-/:-@[-`{-~]$/;
const WORD_CHAR = /^[A-Za-z0-9]$/;
const DIGIT = /^[0-9]$/;
const MAX_INLINE_DEPTH = 8;
const MAX_QUOTE_DEPTH = 3;

function charAt(text: string, index: number): string {
  return text[index] ?? "";
}

function isEscaped(text: string, index: number): boolean {
  let slashes = 0;
  for (let i = index - 1; i >= 0 && text[i] === "\\"; i -= 1) slashes += 1;
  return slashes % 2 === 1;
}

function countRun(text: string, start: number, ch: string): number {
  let n = 0;
  while (text[start + n] === ch) n += 1;
  return n;
}

/** 找到未转义的 delim；找不到返回 -1。 */
function findUnescaped(text: string, delim: string, from: number): number {
  let idx = text.indexOf(delim, from);
  while (idx >= 0) {
    if (!isEscaped(text, idx)) return idx;
    idx = text.indexOf(delim, idx + delim.length);
  }
  return -1;
}

/**
 * 强调闭合符搜索：闭合符左侧不能是空白（CommonMark right-flanking）；
 * 单星/单下划线闭合必须“孤立”（两侧都不是同字符），避免吞掉成对的 **。
 */
function findEmphasisClose(text: string, from: number, delim: string): number {
  let idx = text.indexOf(delim, from);
  while (idx >= 0) {
    const prev = idx > 0 ? charAt(text, idx - 1) : "";
    const okFlank = prev !== "" && !/\s/.test(prev);
    const okIsolated =
      delim.length !== 1 || (prev !== delim && charAt(text, idx + 1) !== delim);
    if (!isEscaped(text, idx) && okFlank && okIsolated) return idx;
    idx = text.indexOf(delim, idx + 1);
  }
  return -1;
}

// ---------- 行内解析 ----------

interface InlineMatch {
  node: RichInline;
  end: number;
}

function tryCodeSpan(text: string, i: number): InlineMatch | null {
  const run = countRun(text, i, "`");
  let j = i + run;
  while (j < text.length) {
    const idx = text.indexOf("`", j);
    if (idx < 0) return null;
    const closeRun = countRun(text, idx, "`");
    if (closeRun === run) {
      let code = text.slice(i + run, idx).replace(/\n/g, " ");
      // CommonMark：两端各剥一个空格（内容非全空格时）
      if (
        code.length >= 2 &&
        code.startsWith(" ") &&
        code.endsWith(" ") &&
        code.trim() !== ""
      ) {
        code = code.slice(1, -1);
      }
      return { node: { type: "code", text: code }, end: idx + closeRun };
    }
    j = idx + closeRun;
  }
  return null; // 未闭合 → 调用方按字面输出反引号
}

function tryDollarMath(text: string, i: number): InlineMatch | null {
  // $$…$$（段落预切分之外的兜底，如引用内同行紧凑写法）→ 按行内数学降级
  if (text.startsWith("$$", i)) {
    const close = findUnescaped(text, "$$", i + 2);
    if (close < 0) return null;
    const tex = text.slice(i + 2, close);
    if (tex.includes("\n") || tex.trim() === "") return null;
    return { node: { type: "math", tex }, end: close + 2 };
  }
  // $…$：开口右侧非空白非 $；闭合左侧非空白、右侧非数字（pandoc 规则，挡价格 $5）
  const next = charAt(text, i + 1);
  if (next === "" || next === "$" || /\s/.test(next)) return null;
  let j = i + 1;
  while (j < text.length) {
    const idx = text.indexOf("$", j);
    if (idx < 0) return null;
    const tex = text.slice(i + 1, idx);
    const before = charAt(text, idx - 1);
    const after = charAt(text, idx + 1);
    if (
      !isEscaped(text, idx) &&
      before !== "" &&
      !/\s/.test(before) &&
      !tex.includes("\n") &&
      after !== "$" &&
      !DIGIT.test(after)
    ) {
      return { node: { type: "math", tex }, end: idx + 1 };
    }
    j = idx + 1;
  }
  return null;
}

function tryParenMath(text: string, i: number): InlineMatch | null {
  // \( … \) → 行内数学（对齐 Web convertDelimiters）
  const close = findUnescaped(text, "\\)", i + 2);
  if (close < 0) return null;
  const tex = text.slice(i + 2, close);
  if (tex.trim() === "") return null;
  return { node: { type: "math", tex }, end: close + 2 };
}

function tryEmphasis(
  text: string,
  i: number,
  depth: number,
): InlineMatch | null {
  const ch = charAt(text, i);
  if (ch !== "*" && ch !== "_") return null;
  const run = countRun(text, i, ch);
  const after = charAt(text, i + run);
  if (after === "" || /\s/.test(after)) return null; // 开口右侧必须非空白
  if (ch === "_") {
    const prev = i > 0 ? charAt(text, i - 1) : "";
    if (WORD_CHAR.test(prev)) return null; // snake_case 词内下划线不解析
  }
  const sizes = run >= 3 ? [3, 2, 1] : run === 2 ? [2, 1] : [1];
  for (const size of sizes) {
    const delim = ch.repeat(size);
    let close = findEmphasisClose(text, i + size, delim);
    while (close >= 0) {
      if (ch === "_" && WORD_CHAR.test(charAt(text, close + size))) {
        close = findEmphasisClose(text, close + size, delim);
        continue;
      }
      break;
    }
    if (close < 0 || close === i + size) continue;
    const inner = parseInline(text.slice(i + size, close), depth + 1);
    let node: RichInline;
    if (size === 3)
      node = { type: "bold", children: [{ type: "italic", children: inner }] };
    else if (size === 2) node = { type: "bold", children: inner };
    else node = { type: "italic", children: inner };
    return { node, end: close + size };
  }
  return null;
}

function tryStrike(text: string, i: number, depth: number): InlineMatch | null {
  if (!text.startsWith("~~", i)) return null;
  const after = charAt(text, i + 2);
  if (after === "" || /\s/.test(after)) return null;
  const close = findEmphasisClose(text, i + 2, "~~");
  if (close < 0 || close === i + 2) return null;
  return {
    node: {
      type: "strike",
      children: parseInline(text.slice(i + 2, close), depth + 1),
    },
    end: close + 2,
  };
}

function tryLink(
  text: string,
  i: number,
  depth: number,
  image: boolean,
): InlineMatch | null {
  // text[i] === "["；image 时 i 指向 "["（调用方已确认前一个字符是 "!"）
  const labelEnd = findUnescaped(text, "]", i + 1);
  if (labelEnd < 0 || charAt(text, labelEnd + 1) !== "(") return null;
  const closeParen = findUnescaped(text, ")", labelEnd + 2);
  if (closeParen < 0) return null;
  let url = text.slice(labelEnd + 2, closeParen).trim();
  const spaceIdx = url.search(/\s/); // 丢弃可选 title：[a](b "t")
  if (spaceIdx >= 0) url = url.slice(0, spaceIdx);
  if (url.startsWith("<") && url.endsWith(">")) url = url.slice(1, -1);
  if (url === "") return null;
  const rawLabel = text.slice(i + 1, labelEnd);
  // 图片降级为链接：label 用 alt 文本（无 alt 时占位 "image"）
  const label = parseInline(
    rawLabel === "" && image ? "image" : rawLabel,
    depth + 1,
  );
  return { node: { type: "link", label, url }, end: closeParen + 1 };
}

const AUTO_LINK_RE = /^<((?:https?:\/\/|mailto:)[^\s<>]*)>/;

/** 解析行内片段为 span 列表；\n 一律视作硬换行（聊天场景约定）。 */
export function parseInline(text: string, depth = 0): RichInline[] {
  if (depth > MAX_INLINE_DEPTH)
    return text === "" ? [] : [{ type: "text", text }];
  const spans: RichInline[] = [];
  let buffer = "";
  const flush = () => {
    if (buffer !== "") {
      spans.push({ type: "text", text: buffer });
      buffer = "";
    }
  };
  let i = 0;
  while (i < text.length) {
    const ch = charAt(text, i);
    if (ch === "\n") {
      flush();
      spans.push({ type: "break" });
      i += 1;
      continue;
    }
    if (ch === "\\") {
      if (text.startsWith("\\(", i)) {
        const m = tryParenMath(text, i);
        if (m) {
          flush();
          spans.push(m.node);
          i = m.end;
          continue;
        }
      }
      const next = charAt(text, i + 1);
      if (ASCII_PUNCT.test(next)) {
        buffer += next;
        i += 2;
        continue;
      }
      buffer += "\\";
      i += 1;
      continue;
    }
    if (ch === "`") {
      const m = tryCodeSpan(text, i);
      if (m) {
        flush();
        spans.push(m.node);
        i = m.end;
        continue;
      }
      buffer += text.slice(i, i + countRun(text, i, "`")); // 未闭合：整段反引号按字面
      i += countRun(text, i, "`");
      continue;
    }
    if (ch === "$") {
      const m = tryDollarMath(text, i);
      if (m) {
        flush();
        spans.push(m.node);
        i = m.end;
        continue;
      }
      buffer += "$";
      i += 1;
      continue;
    }
    if (ch === "!" && charAt(text, i + 1) === "[") {
      const m = tryLink(text, i + 1, depth, true);
      if (m) {
        flush();
        spans.push(m.node);
        i = m.end;
        continue;
      }
      buffer += "!";
      i += 1;
      continue;
    }
    if (ch === "[") {
      const m = tryLink(text, i, depth, false);
      if (m) {
        flush();
        spans.push(m.node);
        i = m.end;
        continue;
      }
      buffer += "[";
      i += 1;
      continue;
    }
    if (ch === "*" || ch === "_") {
      const m = tryEmphasis(text, i, depth);
      if (m) {
        flush();
        spans.push(m.node);
        i = m.end;
        continue;
      }
      buffer += ch;
      i += 1;
      continue;
    }
    if (ch === "~" && charAt(text, i + 1) === "~") {
      const m = tryStrike(text, i, depth);
      if (m) {
        flush();
        spans.push(m.node);
        i = m.end;
        continue;
      }
    }
    if (ch === "<") {
      const m = AUTO_LINK_RE.exec(text.slice(i));
      if (m) {
        const url = m[1] ?? "";
        if (url !== "") {
          flush();
          spans.push({
            type: "link",
            label: [{ type: "text", text: url }],
            url,
          });
          i += (m[0] ?? "").length;
          continue;
        }
      }
    }
    buffer += ch;
    i += 1;
  }
  flush();
  return spans;
}

// ---------- 块级解析 ----------

const FENCE_OPEN_RE = /^[ \t]{0,3}(`{3,}|~{3,})(.*)$/;
const HEADING_RE = /^(#{1,4})(?:[ \t]+(.*))?$/;
const QUOTE_RE = /^[ \t]{0,3}>[ \t]?/;
const LIST_ITEM_RE = /^(\s*)([-*+]|\d{1,9}[.)])[ \t]+/;
/** 段落内的紧凑 $$…$$ / \[…\] 展开为独立 display 块（对齐 Web normalizeMath）。 */
const DISPLAY_MATH_RE = /\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]/g;

interface ListMarker {
  indent: number;
  marker: string;
  ordered: boolean;
  rest: string;
}

function matchListItem(line: string): ListMarker | null {
  const m = LIST_ITEM_RE.exec(line);
  if (!m) return null;
  let indent = 0;
  for (const c of m[1] ?? "") indent += c === "\t" ? 4 : 1;
  const marker = m[2] ?? "";
  if (marker === "") return null;
  return {
    indent,
    marker,
    ordered: WORD_CHAR.test(charAt(marker, 0)),
    rest: line.slice(m[0].length),
  };
}

function isFenceClose(line: string, ch: string, len: number): boolean {
  const t = line.trim();
  if (t.length < len) return false;
  for (let k = 0; k < t.length; k += 1) if (t[k] !== ch) return false;
  return true;
}

function isHr(line: string): boolean {
  const t = line.trim().replace(/[ \t]/g, "");
  return t.length >= 3 && /^(-+|\*+|_+)$/.test(t);
}

function splitTableRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}

/** 分隔行 → 对齐信息；不是合法分隔行返回 null。 */
function delimiterAligns(line: string): TableAlign[] | null {
  const cells = splitTableRow(line);
  if (cells.length === 0) return null;
  const aligns: TableAlign[] = [];
  for (const cell of cells) {
    const m = /^(:?)-+(:?)$/.exec(cell);
    if (!m) return null;
    const left = m[1] === ":";
    const right = m[2] === ":";
    aligns.push(left && right ? "center" : right ? "right" : "left");
  }
  return aligns;
}

/** 表格起点判定：当前行含 | 且下一行是列数一致的合法分隔行。 */
function isTableStart(lines: string[], i: number): boolean {
  const line = lines[i];
  const next = lines[i + 1];
  if (line === undefined || next === undefined) return false;
  if (!line.includes("|")) return false;
  const aligns = delimiterAligns(next);
  if (aligns === null) return false;
  return splitTableRow(line).length === aligns.length;
}

function mathFenceStart(line: string): "$$" | "\\[" | null {
  const t = line.trimStart();
  if (t.startsWith("$$")) return "$$";
  if (t.startsWith("\\[")) return "\\[";
  return null;
}

/** 段落/列表续行是否被新块打断（数学/表格需要前瞻）。 */
function isStructuralBlockStart(lines: string[], i: number): boolean {
  const line = lines[i];
  if (line === undefined) return false;
  if (FENCE_OPEN_RE.test(line)) return true;
  if (HEADING_RE.test(line.trimStart())) return true;
  if (isHr(line)) return true;
  if (QUOTE_RE.test(line)) return true;
  if (mathFenceStart(line) !== null) return true;
  if (isTableStart(lines, i)) return true;
  return false;
}

function pushParagraph(raw: string, blocks: RichBlock[]): void {
  const text = raw.replace(/^\n+|\n+$/g, "");
  if (text.trim() === "") return;
  blocks.push({ type: "paragraph", children: parseInline(text) });
}

/** 段落收尾：切出紧凑 display 数学块，其余按段落输出。 */
function emitParagraphLike(raw: string, blocks: RichBlock[]): void {
  const re = new RegExp(DISPLAY_MATH_RE.source, "g");
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(raw)) !== null) {
    const seg = m[0];
    pushParagraph(raw.slice(last, m.index), blocks);
    const tex = seg.slice(2, -2).trim();
    if (tex !== "") blocks.push({ type: "math", tex });
    else pushParagraph(seg, blocks);
    last = m.index + seg.length;
  }
  pushParagraph(raw.slice(last), blocks);
}

/** $$ / \[ 开头的 display 数学块；返回消费到的下一行下标。未闭合 → 字面段落。 */
function parseMathBlock(
  lines: string[],
  start: number,
  blocks: RichBlock[],
): number {
  const openLine = lines[start] ?? "";
  const delim = mathFenceStart(openLine) ?? "$$";
  const close = delim === "$$" ? "$$" : "\\]";
  const rest = openLine.trimStart().slice(delim.length);
  const body: string[] = [];
  let trailing = "";
  let i = start;
  let closed = false;
  const selfClose = rest.indexOf(close);
  if (selfClose >= 0) {
    body.push(rest.slice(0, selfClose));
    trailing = rest.slice(selfClose + close.length);
    closed = true;
    i = start + 1;
  } else {
    if (rest !== "") body.push(rest);
    i = start + 1;
    while (i < lines.length) {
      const l = lines[i] ?? "";
      const idx = l.indexOf(close);
      if (idx >= 0) {
        body.push(l.slice(0, idx));
        trailing = l.slice(idx + close.length);
        closed = true;
        i += 1;
        break;
      }
      body.push(l);
      i += 1;
    }
  }
  if (!closed) {
    // 流式：未闭合的 $$ 到 EOF 按字面文本输出
    pushParagraph(lines.slice(start).join("\n"), blocks);
    return lines.length;
  }
  const tex = body.join("\n").trim();
  if (tex !== "") blocks.push({ type: "math", tex });
  else pushParagraph(openLine, blocks);
  if (trailing.trim() !== "") pushParagraph(trailing, blocks);
  return i;
}

interface ListScanItem {
  marker: string;
  ordered: boolean;
  rawLines: string[];
  children: ListScanItem[];
}

function finishListItem(item: ListScanItem): RichListItem {
  return {
    marker: item.marker,
    ordered: item.ordered,
    content: parseInline(item.rawLines.join("\n")),
    children: item.children.map(finishListItem),
  };
}

/** 列表块：顶层项 + 一层嵌套（缩进 ≥ 基准+2）；更深层级拍平进嵌套层。 */
function parseList(
  lines: string[],
  start: number,
): { items: RichListItem[]; next: number } {
  const first = matchListItem(lines[start] ?? "");
  const base = first?.indent ?? 0;
  const items: ListScanItem[] = [];
  let current: ListScanItem | null = null;
  let currentNested: ListScanItem | null = null;
  let i = start;
  while (i < lines.length) {
    const line = lines[i];
    if (line === undefined || line.trim() === "") break; // 简化：空行即结束（不支持松散列表）
    const m = matchListItem(line);
    if (m !== null) {
      if (m.indent <= base + 1) {
        current = {
          marker: m.ordered ? m.marker : "•",
          ordered: m.ordered,
          rawLines: [m.rest],
          children: [],
        };
        items.push(current);
        currentNested = null;
      } else if (current !== null) {
        const child: ListScanItem = {
          marker: m.ordered ? m.marker : "•",
          ordered: m.ordered,
          rawLines: [m.rest],
          children: [],
        };
        current.children.push(child);
        currentNested = child;
      } else {
        current = {
          marker: m.ordered ? m.marker : "•",
          ordered: m.ordered,
          rawLines: [m.rest],
          children: [],
        };
        items.push(current);
        currentNested = null;
      }
      i += 1;
      continue;
    }
    if (isStructuralBlockStart(lines, i)) break;
    // 宽容续行：并入最近一个（嵌套）项，换行连接
    const target = currentNested ?? current;
    if (target === null) break;
    target.rawLines.push(line.trim());
    i += 1;
  }
  return { items: items.map(finishListItem), next: i };
}

function parseTable(
  lines: string[],
  start: number,
): { block: RichBlock; next: number } {
  const headerLine = lines[start] ?? "";
  const header = splitTableRow(headerLine).map((cell) => parseInline(cell));
  const align =
    delimiterAligns(lines[start + 1] ?? "") ??
    header.map(() => "left" as TableAlign);
  const rows: RichInline[][][] = [];
  let i = start + 2;
  while (i < lines.length) {
    const l = lines[i];
    if (l === undefined || l.trim() === "" || !l.includes("|")) break;
    const cells = splitTableRow(l);
    // GFM：单元格不足补空、超出截断
    rows.push(
      Array.from({ length: header.length }, (_, k) =>
        parseInline(cells[k] ?? ""),
      ),
    );
    i += 1;
  }
  return { block: { type: "table", header, align, rows }, next: i };
}

function parseBlocks(lines: string[], quoteDepth: number): RichBlock[] {
  const blocks: RichBlock[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line === undefined) break;
    if (line.trim() === "") {
      i += 1;
      continue;
    }

    // 围栏代码块（未闭合 → 收到 EOF，流式容忍）
    const fence = FENCE_OPEN_RE.exec(line);
    if (fence !== null) {
      const fenceText = fence[1] ?? "```";
      const fenceChar = charAt(fenceText, 0) || "`";
      const info = (fence[2] ?? "").trim();
      const language = info.split(/\s+/)[0] ?? "";
      const body: string[] = [];
      i += 1;
      while (i < lines.length) {
        const l = lines[i] ?? "";
        if (isFenceClose(l, fenceChar, fenceText.length)) {
          i += 1;
          break;
        }
        body.push(l);
        i += 1;
      }
      blocks.push({ type: "code", language, code: body.join("\n") });
      continue;
    }

    // ATX 标题（#..####；# 后必须空格或行尾）
    const heading = HEADING_RE.exec(line.trimStart());
    if (heading !== null) {
      const hashes = heading[1] ?? "#";
      const level = Math.min(4, Math.max(1, hashes.length)) as 1 | 2 | 3 | 4;
      const content = (heading[2] ?? "").replace(/[ \t]+#+[ \t]*$/, "").trim();
      blocks.push({ type: "heading", level, children: parseInline(content) });
      i += 1;
      continue;
    }

    if (isHr(line)) {
      blocks.push({ type: "hr" });
      i += 1;
      continue;
    }

    // 引用块：连续 > 行，剥离标记后递归解析（嵌套引用自然支持，深度受限）
    if (QUOTE_RE.test(line)) {
      const qlines: string[] = [];
      while (i < lines.length) {
        const l = lines[i];
        if (l === undefined || !QUOTE_RE.test(l)) break;
        qlines.push(l.replace(QUOTE_RE, ""));
        i += 1;
      }
      if (quoteDepth >= MAX_QUOTE_DEPTH) {
        blocks.push({
          type: "quote",
          blocks: [
            { type: "paragraph", children: parseInline(qlines.join("\n")) },
          ],
        });
      } else {
        blocks.push({
          type: "quote",
          blocks: parseBlocks(qlines, quoteDepth + 1),
        });
      }
      continue;
    }

    // display 数学（$$ / \[ 行首；未闭合 → 字面段落）
    if (mathFenceStart(line) !== null) {
      i = parseMathBlock(lines, i, blocks);
      continue;
    }

    // 列表
    if (matchListItem(line) !== null) {
      const { items, next } = parseList(lines, i);
      blocks.push({ type: "list", items });
      i = next;
      continue;
    }

    // 表格（需要分隔行前瞻；不完整 → 落入段落分支）
    if (isTableStart(lines, i)) {
      const { block, next } = parseTable(lines, i);
      blocks.push(block);
      i = next;
      continue;
    }

    // 段落：累积到空行或新块起点
    const plines: string[] = [];
    while (i < lines.length) {
      const l = lines[i];
      if (l === undefined || l.trim() === "") break;
      if (plines.length > 0 && isStructuralBlockStart(lines, i)) break;
      plines.push(l);
      i += 1;
    }
    emitParagraphLike(plines.join("\n"), blocks);
  }
  return blocks;
}

/** 解析聊天消息为块级 AST；任何输入都不会抛异常。 */
export function parseRichText(input: string): RichBlock[] {
  try {
    const normalized = input.replace(/\r\n?/g, "\n");
    if (normalized.trim() === "") return [];
    return parseBlocks(normalized.split("\n"), 0);
  } catch {
    return input === ""
      ? []
      : [{ type: "paragraph", children: [{ type: "text", text: input }] }];
  }
}
