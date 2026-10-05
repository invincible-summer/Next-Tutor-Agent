import { parseInline, parseRichText } from "@/ui/rich-content/parser";
import type { RichBlock, RichInline } from "@/ui/rich-content/parser";

function blockAt<T extends RichBlock["type"]>(
  blocks: RichBlock[],
  idx: number,
  type: T,
): Extract<RichBlock, { type: T }> {
  const b = blocks[idx];
  if (!b || b.type !== type) {
    throw new Error(
      `期望 blocks[${idx}] 为 ${type}，实际为 ${b ? b.type : "越界"}`,
    );
  }
  return b as Extract<RichBlock, { type: T }>;
}

/** 拍平 span 树为纯文本，便于断言。 */
function flatText(spans: RichInline[]): string {
  return spans
    .map((s) => {
      switch (s.type) {
        case "text":
          return s.text;
        case "break":
          return "\n";
        case "code":
          return s.text;
        case "math":
          return s.tex;
        case "link":
          return flatText(s.label);
        default:
          return flatText(s.children);
      }
    })
    .join("");
}

describe("rich-content parser：块级", () => {
  test("空输入与纯空白 → 空数组", () => {
    expect(parseRichText("")).toEqual([]);
    expect(parseRichText("  \n \n")).toEqual([]);
  });

  test("普通段落", () => {
    const blocks = parseRichText("你好，世界");
    expect(blocks).toHaveLength(1);
    const p = blockAt(blocks, 0, "paragraph");
    expect(p.children).toEqual([{ type: "text", text: "你好，世界" }]);
  });

  test("ATX 标题 #..#### 与闭合井号", () => {
    const blocks = parseRichText(
      "# 一\n## 二\n### 三\n#### 四\n##### 五\n\n#无空格\n\n## 带闭合 ##",
    );
    expect(blockAt(blocks, 0, "heading").level).toBe(1);
    expect(blockAt(blocks, 1, "heading").level).toBe(2);
    expect(blockAt(blocks, 2, "heading").level).toBe(3);
    expect(blockAt(blocks, 3, "heading").level).toBe(4);
    // 超过四级 / 无空格 → 段落
    expect(blockAt(blocks, 4, "paragraph").children[0]).toEqual({
      type: "text",
      text: "##### 五",
    });
    expect(blockAt(blocks, 5, "paragraph").children[0]).toEqual({
      type: "text",
      text: "#无空格",
    });
    expect(flatText(blockAt(blocks, 6, "heading").children)).toBe("带闭合");
  });

  test("标题打断段落", () => {
    const blocks = parseRichText("前文\n# 标题");
    expect(blocks.map((b) => b.type)).toEqual(["paragraph", "heading"]);
  });

  test("围栏代码块：语言、收尾、~~~ 围栏", () => {
    const blocks = parseRichText("```ts\nconst a = 1;\n```\n~~~\nplain\n~~~");
    const code = blockAt(blocks, 0, "code");
    expect(code.language).toBe("ts");
    expect(code.code).toBe("const a = 1;");
    const tilde = blockAt(blocks, 1, "code");
    expect(tilde.language).toBe("");
    expect(tilde.code).toBe("plain");
  });

  test("流式：未闭合围栏收到 EOF 按代码块收尾", () => {
    const blocks = parseRichText("前文\n```py\nprint(1)\nprint(2)");
    expect(blocks.map((b) => b.type)).toEqual(["paragraph", "code"]);
    const code = blockAt(blocks, 1, "code");
    expect(code.language).toBe("py");
    expect(code.code).toBe("print(1)\nprint(2)");
  });

  test("无序/有序列表与标记保留", () => {
    const blocks = parseRichText("- 甲\n* 乙\n+ 丙\n\n3. 第三\n4) 第四");
    const ul = blockAt(blocks, 0, "list");
    expect(ul.items).toHaveLength(3);
    expect(ul.items.every((it) => !it.ordered && it.marker === "•")).toBe(true);
    const ol = blockAt(blocks, 1, "list");
    expect(ol.items.map((it) => it.marker)).toEqual(["3.", "4)"]);
    expect(ol.items.every((it) => it.ordered)).toBe(true);
  });

  test("列表一层嵌套（2 空格与 4 空格）", () => {
    const two = parseRichText("- 父\n  - 子A\n  - 子B\n- 末");
    const list = blockAt(two, 0, "list");
    expect(list.items).toHaveLength(2);
    const parent = list.items[0];
    expect(parent?.children.map((c) => flatText(c.content))).toEqual([
      "子A",
      "子B",
    ]);
    expect(parent?.children[0]?.children).toEqual([]);

    const four = parseRichText("- 父\n    - 深子");
    const deepList = blockAt(four, 0, "list");
    expect(deepList.items[0]?.children).toHaveLength(1);
  });

  test("列表续行并入最近项（硬换行连接）", () => {
    const blocks = parseRichText("- 第一行\n  续行文本");
    const list = blockAt(blocks, 0, "list");
    expect(list.items[0]?.content).toEqual([
      { type: "text", text: "第一行" },
      { type: "break" },
      { type: "text", text: "续行文本" },
    ]);
  });

  test("引用块：单行/多行/嵌套/内嵌列表", () => {
    const blocks = parseRichText("> 引用\n> 第二行");
    const quote = blockAt(blocks, 0, "quote");
    const p = blockAt(quote.blocks, 0, "paragraph");
    expect(flatText(p.children)).toBe("引用\n第二行");

    const nested = parseRichText(">> 嵌套引用");
    const outer = blockAt(nested, 0, "quote");
    const inner = blockAt(outer.blocks, 0, "quote");
    expect(flatText(blockAt(inner.blocks, 0, "paragraph").children)).toBe(
      "嵌套引用",
    );

    const withList = parseRichText("> - 甲\n> - 乙");
    const q = blockAt(withList, 0, "quote");
    expect(blockAt(q.blocks, 0, "list").items).toHaveLength(2);
  });

  test("分隔线 --- / *** / - - -", () => {
    const blocks = parseRichText("---\n***\n- - -");
    expect(blocks.map((b) => b.type)).toEqual(["hr", "hr", "hr"]);
  });

  test("表格：表头/对齐/行，列数不足补空、超出截断", () => {
    const blocks = parseRichText(
      "| 名称 | 值 |\n| :--- | ---: |\n| 甲 | 1 |\n| 乙 |\n| 丙 | 2 | 多余 |",
    );
    const table = blockAt(blocks, 0, "table");
    expect(table.header.map(flatText)).toEqual(["名称", "值"]);
    expect(table.align).toEqual(["left", "right"]);
    expect(table.rows).toHaveLength(3);
    expect(table.rows[0]?.map(flatText)).toEqual(["甲", "1"]);
    expect(table.rows[1]?.map(flatText)).toEqual(["乙", ""]);
    expect(table.rows[2]?.map(flatText)).toEqual(["丙", "2"]);
  });

  test("流式：不完整表格退化为段落", () => {
    // 只有表头，分隔行未到
    const onlyHeader = parseRichText("| a | b |");
    expect(blockAt(onlyHeader, 0, "paragraph").children[0]).toEqual({
      type: "text",
      text: "| a | b |",
    });
    // 分隔行列数不匹配
    const mismatch = parseRichText("| a | b |\n| --- |");
    expect(mismatch.every((b) => b.type === "paragraph")).toBe(true);
  });

  test("display 数学：多行 / 紧凑单行 / 段落内嵌", () => {
    const multi = parseRichText("$$\nE = mc^2\n$$");
    expect(blockAt(multi, 0, "math").tex).toBe("E = mc^2");

    const compact = parseRichText("$$x_{32} = 1$$");
    expect(blockAt(compact, 0, "math").tex).toBe("x_{32} = 1");

    const inline = parseRichText("前文 $$a+b$$ 后文");
    expect(inline.map((b) => b.type)).toEqual([
      "paragraph",
      "math",
      "paragraph",
    ]);
    expect(blockAt(inline, 1, "math").tex).toBe("a+b");
  });

  test("\\[…\\] 归一化为 display 数学（对齐 Web convertDelimiters）", () => {
    const blocks = parseRichText("\\[\n\\int_0^1 x\\,dx\n\\]");
    expect(blockAt(blocks, 0, "math").tex).toBe("\\int_0^1 x\\,dx");

    const sameLine = parseRichText("\\[a=b\\]");
    expect(blockAt(sameLine, 0, "math").tex).toBe("a=b");
  });

  test("流式：未闭合 $$ / \\[ 按字面文本", () => {
    const blocks = parseRichText("$$\nE = mc^2");
    expect(blocks).toHaveLength(1);
    const p = blockAt(blocks, 0, "paragraph");
    expect(flatText(p.children)).toBe("$$\nE = mc^2");

    const bracket = parseRichText("\\[\nx=1");
    const bracketText = flatText(blockAt(bracket, 0, "paragraph").children);
    expect(bracketText).toContain("[");
    expect(bracketText).toContain("x=1");
  });
});

describe("rich-content parser：行内", () => {
  test("粗体 / 斜体 / 粗斜体 / 删除线", () => {
    const spans = parseInline("**粗** *斜* _斜2_ ***兼*** ~~删~~");
    expect(spans[0]).toEqual({
      type: "bold",
      children: [{ type: "text", text: "粗" }],
    });
    expect(spans[2]).toEqual({
      type: "italic",
      children: [{ type: "text", text: "斜" }],
    });
    expect(spans[4]).toEqual({
      type: "italic",
      children: [{ type: "text", text: "斜2" }],
    });
    expect(spans[6]).toEqual({
      type: "bold",
      children: [{ type: "italic", children: [{ type: "text", text: "兼" }] }],
    });
    expect(spans[8]).toEqual({
      type: "strike",
      children: [{ type: "text", text: "删" }],
    });
  });

  test("词内下划线不解析（snake_case）", () => {
    const spans = parseInline("snake_case_name");
    expect(spans).toEqual([{ type: "text", text: "snake_case_name" }]);
  });

  test("行内代码与双反引号包裹", () => {
    const spans = parseInline("使用 `npm test` 运行");
    expect(spans[1]).toEqual({ type: "code", text: "npm test" });
    const nested = parseInline("`` `code` ``");
    expect(nested[0]).toEqual({ type: "code", text: "`code`" });
    // 未闭合 → 字面
    const open = parseInline("前 `未闭合");
    expect(open).toEqual([{ type: "text", text: "前 `未闭合" }]);
  });

  test("行内数学 $…$ 与 \\(…\\)", () => {
    const spans = parseInline("勾股 $a^2+b^2=c^2$ 成立");
    expect(spans[1]).toEqual({ type: "math", tex: "a^2+b^2=c^2" });
    const paren = parseInline("即 \\(a+b\\) 成立");
    expect(paren[1]).toEqual({ type: "math", tex: "a+b" });
  });

  test("未闭合 $ 与价格场景按字面", () => {
    expect(parseInline("半开 $a+b")).toEqual([
      { type: "text", text: "半开 $a+b" },
    ]);
    expect(parseInline("价格 $5 到 $10 元")).toEqual([
      { type: "text", text: "价格 $5 到 $10 元" },
    ]);
  });

  test("链接 / 图片降级为链接 / 自动链接", () => {
    const spans = parseInline(
      "[文档](https://example.com) 与 ![截图](https://example.com/a.png)",
    );
    const link = spans[0];
    if (!link || link.type !== "link") throw new Error("期望链接");
    expect(link.url).toBe("https://example.com");
    expect(flatText(link.label)).toBe("文档");
    const img = spans[2];
    if (!img || img.type !== "link") throw new Error("期望图片降级为链接");
    expect(img.url).toBe("https://example.com/a.png");
    expect(flatText(img.label)).toBe("截图");

    const auto = parseInline("<https://example.com/x>");
    const autoLink = auto[0];
    if (!autoLink || autoLink.type !== "link") throw new Error("期望自动链接");
    expect(autoLink.url).toBe("https://example.com/x");

    // 未闭合链接 → 字面
    expect(parseInline("[标签](未闭合")).toEqual([
      { type: "text", text: "[标签](未闭合" },
    ]);
  });

  test("硬换行", () => {
    const spans = parseInline("甲\n乙");
    expect(spans).toEqual([
      { type: "text", text: "甲" },
      { type: "break" },
      { type: "text", text: "乙" },
    ]);
  });

  test("嵌套：粗体内含斜体与代码；链接内含粗体", () => {
    const spans = parseInline("**粗 *斜* `码`**");
    const bold = spans[0];
    if (!bold || bold.type !== "bold") throw new Error("期望粗体");
    expect(bold.children.map((c) => c.type)).toEqual([
      "text",
      "italic",
      "text",
      "code",
    ]);

    const linkSpans = parseInline("[**重点**](https://a.b)");
    const link = linkSpans[0];
    if (!link || link.type !== "link") throw new Error("期望链接");
    expect(link.label[0]).toEqual({
      type: "bold",
      children: [{ type: "text", text: "重点" }],
    });
  });

  test("反斜杠转义", () => {
    expect(parseInline("\\*非强调\\*")).toEqual([
      { type: "text", text: "*非强调*" },
    ]);
    expect(parseInline("\\$不是数学\\$")).toEqual([
      { type: "text", text: "$不是数学$" },
    ]);
  });

  test("CJK 混合往返", () => {
    const blocks = parseRichText(
      "中文段落 **加粗** 还有 `代码` 与 $c_{标准}$。",
    );
    const p = blockAt(blocks, 0, "paragraph");
    expect(p.children.map((s) => s.type)).toEqual([
      "text",
      "bold",
      "text",
      "code",
      "text",
      "math",
      "text",
    ]);
    expect(flatText(p.children)).toBe("中文段落 加粗 还有 代码 与 c_{标准}。");
  });
});

describe("rich-content parser：健壮性", () => {
  test("任意垃圾输入不抛异常", () => {
    const junk = [
      "```",
      "$$",
      "$$$$",
      "||",
      "[](",
      "***",
      ">",
      "- ",
      "#",
      "\\[",
      "````python\n未闭合",
      "| a |\n| - |\n| 行",
      "a".repeat(5000),
      "**".repeat(200),
      "$x$ $y$ $z$",
      "> > > 深层",
    ];
    for (const input of junk) {
      expect(() => parseRichText(input)).not.toThrow();
      expect(Array.isArray(parseRichText(input))).toBe(true);
    }
  });

  test("CRLF 归一化", () => {
    const blocks = parseRichText("# 标题\r\n\r\n正文\r\n");
    expect(blocks.map((b) => b.type)).toEqual(["heading", "paragraph"]);
  });
});
