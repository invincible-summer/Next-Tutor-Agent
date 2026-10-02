import fs from "node:fs";
import {
  AlignmentType,
  Document,
  Footer,
  Header,
  HeadingLevel,
  Packer,
  PageNumber,
  Paragraph,
  TextRun,
  convertInchesToTwip,
} from "docx";

const outputPath = process.argv[2];
if (!outputPath) throw new Error("Usage: node create_speech.js /abs/output.docx");

const T = String.raw;
const title = T`Next Tutor Agent 项目答辩演讲稿`;

const font = {
  ascii: "Times New Roman",
  hAnsi: "Times New Roman",
  cs: "Times New Roman",
  eastAsia: "SimSun",
};

const run = (text, options = {}) => new TextRun({ text, font, size: 24, ...options });
const para = (children, options = {}) =>
  new Paragraph({ spacing: { after: 160, line: 300 }, ...options, children: Array.isArray(children) ? children : [children] });

const bodyPara = (text) =>
  para(run(text), { indent: { firstLine: convertInchesToTwip(0.33) } });

const h = (text) =>
  para(run(text, { bold: true, size: 26, color: "1F4E79" }), {
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 260, after: 120 },
  });

const sections = [
  {
    title: T`第 1 页 · 封面（约 0.5 分钟）`,
    paragraphs: [
      T`各位老师好，我是杨钧富。我汇报的项目是 Next Tutor Agent——一个以教材为事实源、以学生真实表现为证据驱动的闭环学习智能体。市面上大多数 AI 学习产品优化的是"这一问怎么答"，而我们想回答一个更根本的问题："这个学生下一步该学什么？"题目从学习目标中来，评价从真实表现中来，下一步教学从评价中来——这就是我们整个系统的设计主线。`,
    ],
  },
  {
    title: T`第 2–5 页 · 教材解析（约 1 分钟）`,
    paragraphs: [
      T`教育 AI 的第一步，是让教材成为稳定、可引用、可追踪的事实源。系统逐页 OCR 并保留印刷页码，再按定义、定理、例题、图表的边界做结构化切片，而非机械等长截断——这背后有认知负荷理论的考量：只有语境完整的材料，才不会给学生增加额外的外在认知负荷。在此之上派生两层能力：教材检索采用 FOUND / PARTIAL / NOT_FOUND 三级证据判定，"检索不到"也是正式结果，教材没有说的绝不包装成教材观点；知识图谱按章抽取概念间前置、相关、应用、误解四类关系，前置关系构成有向无环图，依据知识依赖结构直接推理"先学什么"。目前 12 本公用教材完成解析，《高等数学》单册构建 362 个概念、857 条关系边。图谱不是展示层，而是对话、出题、评价、编排四大功能共享的公共语义层。`,
    ],
  },
  {
    title: T`第 6–9 页 · 对话教学（约 40 秒）`,
    paragraphs: [
      T`对话教学中，每一轮对话都是一次教学决策：先定位问题对应的概念、检索原文证据，再在引入、讲解、纠偏、练习、复习、挑战六类教学模式间切换。帮助强度依据认知负荷理论设计为可逐步撤除的三档——完整示范、关键提示、独立完成；帮助条件会被完整记录，并在评价时约束推断范围：看过示范后做对，不等于独立掌握。学生聊微信式的"懂了""谢谢"不会被计入学习证据，只有解释、反例、自我纠错、迁移这类真实表现才进入统一学习证据账本。`,
    ],
  },
  {
    title: T`第 10–11 页 · 笔记与编排（约 20 秒，快速带过）`,
    paragraphs: [
      T`编排按知识依赖把长期目标拆解为周计划与今日任务；笔记从对话、教材、错题中沉淀、来源可追，并接入 SM-2 间隔重复算法，到期自动回到学习计划。（此处快速带过。）`,
    ],
  },
  {
    title: T`第 12–16 页 · 智能出题（约 1.5 分钟，重点）`,
    paragraphs: [
      T`这是本项目最核心的创新。让大模型"出一道题"几乎必然踩中九类陷阱——脱离教材、答案有误、标称分析实则套公式、变式只换数字。我们的立场是：出题不是文本生成，而是任务设计，其理论依据是证据中心设计（ECDL）——不是先有题再想这题能说明什么，而是先有学习主张，再设计能产生证据的任务。`,
      T`系统从教材解析出的概念出发，先制定命题蓝图，明确六要素：学习主张、认知过程、知识类型、证据机会、帮助设计、量规草案。认知过程采用布鲁姆修订版认知目标分类（RBT）六级——但它是"任务要求"而非"难度标签"：生成器必须声明目标认知过程，审核者用"最低可行解法"反查伪高阶题。比如"写出动量守恒定律的公式"只能产生记忆证据；而"在新情境中判断系统边界与外力条件，决定动量守恒是否适用"才真正产生分析能力的证据。`,
      T`题目生成后还要过独立审题这一关：Generator 与 Critic 结构性分离，Critic 像命题审校一样实际重解关键步骤再下结论；评分量规在学生作答前先行冻结——评价器面对的是一份"任务合同"，不会被答案反向诱导，同一道题多次作答也适用同一份标准。图题采用受约束 SVG，作为题目结构的一部分接受图文一致性审核。变式题不做换数字的伪变式，而是做情境迁移、结构变异与收敛变式，真正区分"记住模板"与"真正会用"。一句话：我们的题不是"像这门课"，而是可以回溯到当前教材。`,
    ],
  },
  {
    title: T`第 17–22 页 · 学习评价与闭环（约 1.5 分钟，重点）`,
    paragraphs: [
      T`评价的对象不是分数，而是可证实的学习主张——这同样遵循 ECDL 证据链：学习主张、任务机会、可观察证据、推断依据与限制、当前判断、下一教学动作，每个概念都可追问到底。判分不等于评价：选择题做确定性判分，开放题按冻结量规逐项判定——已满足、部分满足、未观察到、等价解法，批改指出具体证据，而非只给一个总分。`,
      T`每个知识点的学习状态采用证据式语义五态：未观察到、局部支持、范围内有支持、脆弱、证据冲突——状态随新证据回退或分裂，不是百分比的另一种写法。例如学生计算题稳定正确、迁移任务失败，系统不会压缩成一个"72% 掌握度"，而是给出"范围内有支持、迁移能力未被支持"，直接指明下一步该验证什么。认知负荷理论在这里再次介入：帮助条件决定结论能推多远——看过示范做对，只能支持"支持下能复现"；完全独立完成，才能支持"独立表现"。同时我们设定了严格的证据门槛：迁移任务必须与基线任务存在真实差异，即时重答不能被包装成长期记忆。`,
      T`评价结果直接驱动闭环：系统借鉴 CAT 自适应测评的思想，根据最近的有效作答动态选择最有信息价值的下一题，并生成下一教学动作——重新解释、同类练习、结构变式、新情境迁移、延迟重测。评价不是终点，而是下一轮教学的输入，整个系统由此循环起来。`,
    ],
  },
  {
    title: T`第 23 页 · 产品对比（约 40 秒）`,
    paragraphs: [
      T`最后客观对比。ChatGPT Study Mode 的苏格拉底式引导、Gemini NotebookLM 的资料引用与多模态整理、科大讯飞学习机的题库规模与产业成熟度，都是业界标杆，这些优势我们认可。但这些产品的出题与评价机制是内部黑盒，无法审计；Next Tutor Agent 的每一个环节都对齐当前教材、沿证据链可回溯、教学机制可审计——这是真实、可验证的优越性，也是本项目存在的理由。我的汇报到此结束，谢谢各位老师。`,
    ],
  },
];

const children = [
  para(run(title, { bold: true, size: 34, color: "1F4E79" }), {
    heading: HeadingLevel.TITLE,
    alignment: AlignmentType.CENTER,
    spacing: { after: 200 },
  }),
  para(run(T`（正文约 2000 字 · 按 PPT 页组织 · 正常语速约 7 分钟）`, { italics: true, size: 20, color: "5F6B7A" }), {
    alignment: AlignmentType.CENTER,
    spacing: { after: 320 },
  }),
];

for (const s of sections) {
  children.push(h(s.title));
  for (const p of s.paragraphs) children.push(bodyPara(p));
}

const doc = new Document({
  sections: [{
    properties: { page: { margin: { top: 1440, bottom: 1440, left: 1440, right: 1440 } } },
    headers: {
      default: new Header({
        children: [para(run(title, { size: 18, color: "9AA1AC" }), { alignment: AlignmentType.CENTER })],
      }),
    },
    footers: {
      default: new Footer({
        children: [para(new TextRun({ children: [PageNumber.CURRENT], size: 20 }), { alignment: AlignmentType.CENTER })],
      }),
    },
    children,
  }],
});

fs.writeFileSync(outputPath, await Packer.toBuffer(doc));
