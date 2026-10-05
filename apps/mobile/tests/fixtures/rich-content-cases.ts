/** Project-authored synthetic cases. No textbook or conversation data. */
export const TEX_CASES = [
  "a^2+b^2=c^2",
  "x_{n+1}=x_n+1",
  "\\frac{1}{2}",
  "\\sqrt{x^2+y^2}",
  "\\int_0^1 x^2\\,dx",
  "\\sum_{k=1}^{n}k",
  "\\lim_{x\\to0}\\frac{\\sin x}{x}",
  "\\begin{bmatrix}1&2\\\\3&4\\end{bmatrix}",
  "\\vec{a}\\cdot\\vec{b}",
  "\\left(\\frac{x+1}{x-1}\\right)^2",
  "P(A\\mid B)",
  "\\binom{n}{r}",
  "\\alpha+\\beta=\\gamma",
  "\\text{面积}=\\pi r^2",
  "\\ce{H2O}",
  "\\begin{aligned}x+y&=3\\\\x-y&=1\\end{aligned}",
  "\\underbrace{1+\\cdots+1}_{n\\text{ 次}}",
  "\\mathbb{R}\\setminus\\{0\\}",
  "\\frac{\\partial f}{\\partial x}",
  "\\displaystyle\\prod_{i=1}^{20}(1+x_i)",
];

const WRAPPERS: ((tex: string) => string)[] = [
  (tex) => `中文与 English：$${tex}$。`,
  (tex) => `## 合成标题\n\n\\(${tex}\\)`,
  (tex) => `$$\n${tex}\n$$`,
  (tex) => `前面 $$${tex}$$ 后面`,
  (tex) => `\\[${tex}\\]`,
  (tex) => `> 引用：$${tex}$\n> **思考过程**`,
  (tex) => `- 第一步 $${tex}$\n  - 子步骤\n- 第二步`,
  (tex) => `3. 第三步 $${tex}$\n4) 第四步`,
  (tex) => `| 表达式 | 说明 |\n| :--- | ---: |\n| $${tex}$ | 合成数据 |`,
  (tex) => `
\`\`\`ts
const literal = "<script>synthetic</script>";
\`\`\`
公式：$${tex}$`,
  (tex) =>
    `**加粗** / *斜体* / ~~删除~~ / \`代码\`：$${tex}$\n\n[公开说明](https://example.invalid/path?x=1&y=2)`,
  (tex) =>
    `![合成图片](https://example.invalid/synthetic.png)\n\n$${tex}$\n\n---`,
];

export const RICH_CONTENT_CASES = TEX_CASES.flatMap((tex, i) =>
  WRAPPERS.map((wrap, j) => ({
    id: `tex-${i + 1}-format-${j + 1}`,
    tex,
    text: wrap(tex),
  })),
);
