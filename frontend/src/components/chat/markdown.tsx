"use client";
import { memo, type ComponentProps, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { slugifyHeading } from "@/lib/markdown-toc";

type RehypeNode = {
  type?: string;
  properties?: Record<string, unknown>;
  children?: RehypeNode[];
};

function toReactStyleProperty(property: string): string {
  return property.trim().replace(/-([a-z])/g, (_match, character: string) => character.toUpperCase());
}

function parseInlineStyle(value: string): Record<string, string> {
  const style: Record<string, string> = {};
  for (const declaration of value.split(";")) {
    const separator = declaration.indexOf(":");
    if (separator < 0) continue;
    const property = toReactStyleProperty(declaration.slice(0, separator));
    const propertyValue = declaration.slice(separator + 1).trim();
    if (property && propertyValue) style[property] = propertyValue;
  }
  return style;
}

function rehypeStyleObjects() {
  return (tree: RehypeNode) => {
    const visit = (node: RehypeNode) => {
      if (typeof node.properties?.style === "string") {
        node.properties.style = parseInlineStyle(node.properties.style);
      }
      for (const child of node.children || []) visit(child);
    };
    visit(tree);
  };
}

/* ---- Math normalization ----
 * remark-math parses $...$ / $$...$$ delimiters. The tutor agent (and many
 * LLMs) emits LaTeX \\( ... \\) (inline) and \\[ ... \\] (display) instead.
 * We convert those to the $-form before markdown parsing so KaTeX renders them.
 * We protect fenced code blocks and inline code spans from substitution. */
const REMARK_PLUGINS = [remarkGfm, remarkMath];
// strict:false + throwOnError:false：模型输出里的中文下标等非严格 LaTeX
// 由 sanitizeCjkInMath 兜底转 \\text{}，渲染绝不中断、不刷 console 警告。
const REHYPE_PLUGINS: ComponentProps<typeof ReactMarkdown>["rehypePlugins"] =
  [[rehypeKatex, { output: "htmlAndMathml", strict: false, throwOnError: false }], rehypeStyleObjects];

type MathKind = "inline-dollar" | "display-dollar" | "paren" | "bracket";
type MathSpan = { kind: MathKind; start: number; end: number };
type MathScan = {
  hasMath: boolean;
  unclosedStart: number | null;
  unclosedKind: MathKind | null;
  /** 全部完整数学区间（含定界符），按出现顺序。 */
  spans: MathSpan[];
};

function isEscaped(source: string, index: number): boolean {
  let slashes = 0;
  for (let i = index - 1; i >= 0 && source[i] === "\\"; i -= 1) slashes += 1;
  return slashes % 2 === 1;
}

function backtickRun(source: string, index: number): number {
  let end = index;
  while (end < source.length && source[end] === "`") end += 1;
  return end - index;
}

/**
 * Scan markdown without treating code as math. Besides detecting historical
 * math messages, this gives the streaming renderer the exact point at which a
 * not-yet-closed formula starts. The stream normally only has an open formula
 * at its tail, but scanning from the beginning also handles a token arriving
 * after an unmatched delimiter without ever sending it to KaTeX prematurely.
 */
function scanMath(source: string): MathScan {
  let codeFenceLength = 0;
  let inlineCodeLength = 0;
  let mathKind: MathKind | null = null;
  let mathStart = -1;
  let hasMath = false;
  const spans: MathSpan[] = [];

  for (let i = 0; i < source.length;) {
    if (codeFenceLength > 0) {
      if (source[i] === "`") {
        const run = backtickRun(source, i);
        if (run >= codeFenceLength) {
          codeFenceLength = 0;
          i += run;
          continue;
        }
        i += run;
      } else {
        i += 1;
      }
      continue;
    }

    if (inlineCodeLength > 0) {
      if (source[i] === "`") {
        const run = backtickRun(source, i);
        if (run === inlineCodeLength) {
          inlineCodeLength = 0;
          i += run;
          continue;
        }
        i += run;
      } else {
        i += 1;
      }
      continue;
    }

    if (mathKind) {
      const closes =
        (mathKind === "display-dollar" && source.startsWith("$$", i)) ||
        (mathKind === "inline-dollar" && source[i] === "$" && !source.startsWith("$$", i)) ||
        (mathKind === "paren" && source.startsWith("\\)", i)) ||
        (mathKind === "bracket" && source.startsWith("\\]", i));
      if (closes && !isEscaped(source, i)) {
        hasMath = true;
        const delimLen = mathKind === "inline-dollar" ? 1 : 2;
        spans.push({ kind: mathKind, start: mathStart, end: i + delimLen });
        i += delimLen;
        mathKind = null;
        mathStart = -1;
        continue;
      }
      i += 1;
      continue;
    }

    if (source[i] === "`") {
      const run = backtickRun(source, i);
      if (run >= 3) codeFenceLength = run;
      else inlineCodeLength = run;
      i += run;
      continue;
    }

    if (isEscaped(source, i)) {
      i += 1;
      continue;
    }

    if (source.startsWith("$$", i)) {
      mathKind = "display-dollar";
      mathStart = i;
      i += 2;
      continue;
    }
    if (source[i] === "$") {
      mathKind = "inline-dollar";
      mathStart = i;
      i += 1;
      continue;
    }
    if (source.startsWith("\\(", i)) {
      mathKind = "paren";
      mathStart = i;
      i += 2;
      continue;
    }
    if (source.startsWith("\\[", i)) {
      mathKind = "bracket";
      mathStart = i;
      i += 2;
      continue;
    }

    i += 1;
  }

  return {
    hasMath,
    unclosedStart: mathKind ? mathStart : null,
    unclosedKind: mathKind,
    spans,
  };
}

/** True for complete or currently-open math outside fenced/inline code. */
export function containsMathMarkdown(source: string): boolean {
  const scan = scanMath(source);
  return scan.hasMath || scan.unclosedStart !== null;
}

/** 句中全部块状公式段（`$$…$$` 与 `\[…\]`，含定界符，按出现顺序）。
 *  语音黑板只挂块式：行内 `$…$`/`\(…\)` 不上板、也不触发换板。 */
export function displayMathSegments(source: string): string[] {
  return scanMath(source).spans
    .filter((s) => s.kind === "display-dollar" || s.kind === "bracket")
    .map((s) => source.slice(s.start, s.end));
}

/**
 * Keep an unfinished formula out of remark-math while the answer is streaming.
 * The raw tail is deliberately rendered as text; the next render after the
 * closing delimiter arrives sends the whole message through KaTeX at once.
 */
export function splitStreamingMarkdown(source: string): {
  stable: string;
  pending: string;
  pendingDisplay: boolean;
} {
  const scan = scanMath(source);
  if (scan.unclosedStart === null) return { stable: source, pending: "", pendingDisplay: false };
  return {
    stable: source.slice(0, scan.unclosedStart),
    pending: source.slice(scan.unclosedStart),
    pendingDisplay: scan.unclosedKind === "display-dollar" || scan.unclosedKind === "bracket",
  };
}

/** KaTeX math mode cannot typeset bare CJK (e.g. $c_{标准}$ warns and renders
 * badly). 中文下标是合法需求：把数学环境内的中文片段自动包进 \\text{...}，
 * 已有 \\text{...} 段原样跳过。 */
function sanitizeCjkInMath(body: string): string {
  return body.replace(
    /\\text\{[^{}]*\}|[一-鿿　-〿＀-￯]+/g,
    (m) => (m.startsWith("\\text{") ? m : `\\text{${m}}`),
  );
}

/**
 * remark-math only treats `$$` as a display-math fence when the opening and
 * closing fences occupy their own lines. The model often emits compact
 * fences such as `$$M_{32}=...$$`, which remark-math 6 parses as inlineMath.
 * That is harmless for a fraction but collapses a matrix/determinant into a
 * normal paragraph line. Normalize every display body to the block form
 * before handing the source to remark.
 */
function blockquotePrefixAt(source: string, index: number): string | null {
  const lineStart = source.lastIndexOf("\n", index - 1) + 1;
  const prefix = source.slice(lineStart, index).match(/^[ \t]{0,3}(?:>[ \t]?)+$/)?.[0];
  return prefix ?? null;
}

/**
 * Preserve Markdown blockquote containers while expanding a compact display
 * fence. Without this, a source such as `> $$g(n)=...$$` becomes a formula
 * containing literal `>` characters, which KaTeX dutifully renders as greater
 * than signs. The existing quote marker before the opening fence is kept by
 * the caller; the markers on the body/closing lines are restored here.
 */
function trimMathLines(lines: string[]): string[] {
  let start = 0;
  let end = lines.length;
  while (start < end && lines[start].trim() === "") start += 1;
  while (end > start && lines[end - 1].trim() === "") end -= 1;
  return lines.slice(start, end);
}

function displayMathBlock(body: string, quotePrefix?: string | null): string {
  if (!quotePrefix) {
    const lines = trimMathLines(sanitizeCjkInMath(body).split("\n"));
    return `\n\n$$\n${lines.join("\n")}\n$$\n\n`;
  }

  // Keep the blockquote leader outside the TeX source. Do not call String.trim
  // before removing it: trim() would erase indentation from the first quoted
  // line and turn `  > x` into `  > > x` when the quote is reconstructed.
  const quoteMarker = quotePrefix.trimEnd();
  const lines = trimMathLines(body.split("\n")).map((line) => {
    if (!line.startsWith(quoteMarker)) return line;
    const rest = line.slice(quoteMarker.length);
    return rest.startsWith(" ") ? rest.slice(1) : rest;
  });
  const sanitized = trimMathLines(sanitizeCjkInMath(lines.join("\n")).split("\n"));
  return `$$\n${sanitized.map((line) => `${quotePrefix}${line}`).join("\n")}\n${quotePrefix}$$`;
}

function sanitizeMathSpans(s: string): string {
  // Keep display math separate from surrounding prose so remark-math emits a
  // `math` node (and rehype-katex emits `.katex-display`) rather than an
  // `inlineMath` node. The quote-aware path prevents Markdown blockquote
  // markers from becoming literal KaTeX operators.
  s = s.replace(/\$\$([\s\S]*?)\$\$/g, (_m, body: string, offset: number, source: string) =>
    displayMathBlock(body, blockquotePrefixAt(source, offset)),
  );
  s = s.replace(/\$([^$\n]+?)\$/g, (_m, body: string) => `$${sanitizeCjkInMath(body)}$`);
  return s;
}

function convertDelimiters(s: string): string {
  // display: \\[ ... \\]  ->  $$ ... $$
  s = s.replace(/\\\[([\s\S]*?)\\\]/g, (_m, body: string) => `$$${body}$$`);
  // inline: \\( ... \\)  ->  $ ... $
  s = s.replace(/\\\(([\s\S]*?)\\\)/g, (_m, body: string) => `$${body}$`);
  return sanitizeMathSpans(s);
}

function transformMath(s: string): string {
  // Within a non-code segment, still avoid inline-code spans.
  const out: string[] = [];
  const inlineRe = /(`[^`]*`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = inlineRe.exec(s))) {
    out.push(convertDelimiters(s.slice(last, m.index)));
    out.push(m[0]);
    last = m.index + m[0].length;
  }
  out.push(convertDelimiters(s.slice(last)));
  return out.join("");
}

function codeRanges(source: string): { start: number; end: number }[] {
  const ranges: { start: number; end: number }[] = [];
  for (let i = 0; i < source.length;) {
    if (source[i] !== "`") {
      i += 1;
      continue;
    }
    const run = backtickRun(source, i);
    let end = source.length;
    for (let j = i + run; j < source.length;) {
      if (source[j] !== "`") {
        j += 1;
        continue;
      }
      const closingRun = backtickRun(source, j);
      if ((run >= 3 && closingRun >= run) || (run < 3 && closingRun === run)) {
        end = j + closingRun;
        break;
      }
      j += closingRun;
    }
    ranges.push({ start: i, end });
    i = end;
  }
  return ranges;
}

export function normalizeMath(src: string): string {
  if (!src) return src;
  // Protect fenced code blocks and *all* inline-code delimiter lengths
  // (including double-backtick spans) before converting alternate math
  // delimiters. An unfinished code span is kept literal to avoid changing
  // any `$` characters inside it while the message is still being edited.
  const segments: string[] = [];
  const ranges = codeRanges(src);
  let last = 0;
  for (const range of ranges) {
    segments.push(transformMath(src.slice(last, range.start)));
    segments.push(src.slice(range.start, range.end));
    last = range.end;
  }
  segments.push(transformMath(src.slice(last)));
  return segments.join("");
}

/* Keep the official KaTeX subtree intact. The extra sibling wrapper owns only
 * responsive horizontal overflow; it never changes KaTeX's internal vlist,
 * scripts, fractions or radicals. */
const MARKDOWN_COMPONENTS: ComponentProps<typeof ReactMarkdown>["components"] = {
  span(input) {
    const { node, className, children, ...props } = input;
    void node;
    if (typeof className === "string" && className.split(/\s+/).includes("katex-display")) {
      return (
        <div className="katex-scroll">
          <span className={className} {...props}>{children}</span>
        </div>
      );
    }
    return <span className={className} {...props}>{children}</span>;
  },
};

/* ---- Docs 标题锚点 ----
 * anchorHeadings=true 时给 h1–h3 注入与 lib/markdown-toc.ts 一致的 slug id，
 * 供 /docs 目录跳转。仅文档页启用，chat/notes 渲染路径零影响。 */
function reactNodeText(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(reactNodeText).join("");
  if (typeof node === "object" && "props" in node) {
    return reactNodeText((node as { props?: { children?: ReactNode } }).props?.children);
  }
  return "";
}

function anchorHeading(tag: "h1" | "h2" | "h3") {
  const Tag = tag;
  return function RenderHeading({ node, children, ...props }: { node?: unknown } & ComponentProps<"h1">) {
    void node;
    return (
      <Tag id={slugifyHeading(reactNodeText(children))} className="scroll-mt-6" {...props}>
        {children}
      </Tag>
    );
  };
}

const ANCHOR_MARKDOWN_COMPONENTS: ComponentProps<typeof ReactMarkdown>["components"] = {
  ...MARKDOWN_COMPONENTS,
  h1: anchorHeading("h1"),
  h2: anchorHeading("h2"),
  h3: anchorHeading("h3"),
};

function ParsedMarkdown({ children, anchorHeadings }: { children: string; anchorHeadings?: boolean }) {
  return (
    <ReactMarkdown
      remarkPlugins={REMARK_PLUGINS}
      rehypePlugins={REHYPE_PLUGINS}
      components={anchorHeadings ? ANCHOR_MARKDOWN_COMPONENTS : MARKDOWN_COMPONENTS}
    >
      {normalizeMath(children || "")}
    </ReactMarkdown>
  );
}

/** Full markdown body for assistant prose (chat-prose styling).
 * Memoized: the remark+KaTeX parse is expensive, so identical children
 * (e.g. parent re-renders on heartbeat during streaming) skip re-parsing. */
export const Markdown = memo(function Markdown({ children, className, anchorHeadings }: { children: string; className?: string; anchorHeadings?: boolean }) {
  return (
    <div className={className ?? "chat-prose"}>
      <ParsedMarkdown anchorHeadings={anchorHeadings}>{children}</ParsedMarkdown>
    </div>
  );
});

/** Streaming renderer: stable markdown is parsed normally, while an open math
 * tail remains literal until its closing delimiter arrives. */
export const StreamingMarkdown = memo(function StreamingMarkdown({ children, className }: { children: string; className?: string }) {
  const { stable, pending, pendingDisplay } = splitStreamingMarkdown(children || "");
  return (
    <div className={className ?? "chat-prose"}>
      {stable ? <ParsedMarkdown>{stable}</ParsedMarkdown> : null}
      {pendingDisplay ? (
        <div className="streaming-math-pending streaming-math-pending-display">{pending}</div>
      ) : pending ? (
        <span className="streaming-math-pending">{pending}</span>
      ) : null}
    </div>
  );
});

/* ---- Inline Markdown renderer for quiz fields ----
 * Quiz stems / options / explanations / feedback may contain math and
 * formatting. We reuse the same remark+rehype pipeline + normalizeMath so
 * KaTeX renders inside quiz cards exactly like in chat prose. */
export const MiniMarkdown = memo(function MiniMarkdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={className}>
      <ParsedMarkdown>{children}</ParsedMarkdown>
    </div>
  );
});
