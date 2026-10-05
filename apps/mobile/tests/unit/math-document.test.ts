import { RICH_CONTENT_CASES } from "../fixtures/rich-content-cases";
import { parseRichText } from "@/ui/rich-content/parser";
import {
  escapeHtml,
  hasMath,
  mathDocument,
  richHtml,
  safeContentLink,
} from "@/ui/rich-content/math-document";
import { MATH_CSS } from "@/ui/rich-content/math-assets";

describe("synthetic Markdown and TeX combinations", () => {
  test("has at least 200 distinct authored format combinations", () => {
    expect(RICH_CONTENT_CASES.length).toBeGreaterThanOrEqual(200);
    expect(new Set(RICH_CONTENT_CASES.map((c) => c.text)).size).toBe(
      RICH_CONTENT_CASES.length,
    );
  });

  test.each(RICH_CONTENT_CASES)(
    "$id preserves TeX and safely serializes the AST",
    ({ text, tex }) => {
      const blocks = parseRichText(text);
      expect(hasMath(blocks)).toBe(true);
      const html = richHtml(blocks);
      expect(html).toContain(`data-math="${escapeHtml(tex)}"`);
      expect(html).not.toContain("<script>");
      expect(richHtml(parseRichText(text))).toBe(html);
    },
  );

  test.each([
    "**尚未闭合",
    "半个公式 $x+",
    "$$\n\\frac{1",
    "```js\n<script>fake</script>",
    "| 甲 | 乙 |",
    "[链接](尚未闭合",
    "\\[x+",
  ])("streaming partial input remains safe: %s", (text) => {
    expect(() => richHtml(parseRichText(text))).not.toThrow();
    expect(richHtml(parseRichText(text))).not.toContain("<script>");
  });
});

describe("offline math HTML security", () => {
  test.each([
    "javascript:alert(1)",
    "data:text/html,synthetic",
    "file:///private",
    "nexttutor://me",
    "//example.invalid",
    "https://example.invalid/\nsecret",
  ])("rejects non HTTP content link %s", (url) => {
    expect(safeContentLink(url)).toBe(false);
  });

  test("plain content and formula attributes cannot break out into HTML", () => {
    const html = richHtml([
      {
        type: "paragraph",
        children: [
          {
            type: "text",
            text: '<img src=x onerror="evil()"><script>evil()</script>',
          },
        ],
      },
      { type: "math", tex: '\"><script>evil()</script>' },
      {
        type: "paragraph",
        children: [
          {
            type: "link",
            url: "javascript:evil()",
            label: [{ type: "text", text: "Safe label" }],
          },
        ],
      },
    ]);
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain('href="javascript:');
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("Safe label");
  });

  test("HTTP URLs are escaped as attributes and checked for length", () => {
    const html = richHtml([
      {
        type: "paragraph",
        children: [
          {
            type: "link",
            url: 'https://example.invalid/?a="quoted"&b=1',
            label: [{ type: "text", text: "Link" }],
          },
        ],
      },
    ]);
    expect(html).toContain(
      'href="https://example.invalid/?a=&quot;quoted&quot;&amp;b=1"',
    );
    expect(safeContentLink("https://example.invalid/" + "a".repeat(2048))).toBe(
      false,
    );
  });

  test("KaTeX and fonts are bundled locally with a nonce and closed network policy", () => {
    const html = mathDocument(
      parseRichText("公式 $x^2$"),
      "math-fixture-1234",
      "#123456",
      "#ffffff",
      16,
    );
    expect(html).toContain("Content-Security-Policy");
    expect(html).toContain("connect-src &apos;none&apos;");
    expect(html).toContain('nonce="math-fixture-1234"');
    expect(html).toContain("trust:false");
    expect(html).toContain("maxExpand:1000");
    expect(html).not.toContain('<script src="');
    expect(MATH_CSS).toContain("data:font/woff2;base64,");
    expect(MATH_CSS).not.toMatch(/url\((?:["']?https?:|fonts\/)/);
  });

  test("math is detected inside native Markdown containers", () => {
    for (const input of [
      "> $x$",
      "- A\n  - $x$",
      "[**$x$**](https://example.invalid)",
      "| $x$ | y |\n| --- | --- |\n| a | b |",
    ])
      expect(hasMath(parseRichText(input))).toBe(true);
    expect(hasMath(parseRichText("Pure Markdown **text** and `code`"))).toBe(
      false,
    );
  });

  test("ordered numbering and table alignment survive the offline serializer", () => {
    const list = richHtml(parseRichText("3. 第三步 $x$\n4) 第四步"));
    expect(list).toContain('<ol start="3">');
    expect(list).toContain('<ol start="4">');
    const table = richHtml(
      parseRichText("| 左 | 右 |\n| :--- | ---: |\n| $x$ | y |"),
    );
    expect(table).toContain("text-align:left");
    expect(table).toContain("text-align:right");
  });
});
