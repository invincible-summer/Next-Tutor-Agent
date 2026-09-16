# CAT 题目插图：文字先行 + 审核后 SVG 补充

本文件描述 2026-09-16 起 `/assessment`（M4 CAT）当前有效的插图合同。它覆盖 `DESIGN.md` §7.2 中仍描述 CAT“题图与题面同次生成 / 共享 6 次调用、90 秒”的旧段落；聊天 `generate_quiz` / `fit_quiz` 的同步结构化题图路径不受本次改造影响。

## 1. 核心不变量

CAT 题目分为两个彼此独立的阶段：

1. **文字题阶段**：`/assessment/start` 与 `/assessment/next` 生成、审核并注册一份完全自洽的文字题，硬截止 27 秒。即使请求 `illustration_request="required"`，题干也必须在无图时独立可答，不允许写入“如图”“见下图”之类尚不存在的必需条件，也不允许把内部回退说明写进题干。
2. **插图 enrichment 阶段**：文字题已经在浏览器显示后，前端针对同一个 `(question_id, question_revision)` 调用 `POST /api/v1/assessment/questions/{question_id}/illustration`。该调用只可生成补充 `illustration`，不得改题干、选项、答案、解析、量规或 revision。

`TaskSnapshot`、`rubric_hash` 与 `question_revision` 仍是冻结学习证据，不因后到的插图改变。插图属于题目身份上的附加视觉 enrichment；它不得增加文字题中没有的解题必需条件，也不得泄露正确答案或待求结论。

## 2. API 与所有权

请求：

```text
POST /api/v1/assessment/questions/{question_id}/illustration
{"question_revision": 1}
```

服务端只信任 JWT 解析出的 `student_id`。它从该学生的 learning-evidence journal 读取权威 `TaskSnapshot`，并要求题目是某个 CAT instance 的**最后/当前 question_ref**；历史题不能通过手工调用重新触发生图。客户端不能提交题干、答案或 SVG。

响应状态：

- `ready`：返回通过确定性清洗和语义审图的规范化 SVG；
- `not_required`：有效策略为 off，或 auto 判断不需要图；
- `failed`：18 秒预算、SVG 校验或语义审图未通过。失败只影响图，文字题仍可作答；
- `illustration_disabled`：required 请求与账户/运维开关冲突，按现有 409 合同返回。

成功结果按 `(student_id, question_id, question_revision)` 写入账户私有 cache；同题并发请求由 keyed asyncio lock 合并，后续刷新直接命中 cache，不再烧模型调用。

## 3. 预算合同

总体验预算固定为 45 秒上限：

- 文字题：≤27 秒；
- 插图：≤18 秒，最多 3 次 LLM 调用。

插图阶段：

1. Call 1：只生成 illustration；
2. 确定性 SVG sanitizer；
3. Call 2：独立核对图与冻结题目/答案的一致性；
4. 只有剩余时间足够时才允许一次修复；修复后必须再次 sanitizer，并在最大 3 调用内完成最终审核。

实现保留至少约 3.5 秒最终审图窗口，修复启动门槛约 7 秒。预算不足时直接失败，不再启动“注定做不完”的新阶段，也绝不重新生成另一道完整题。

## 4. SVG 安全子集

`app/core/quiz_illustration.py` 仍使用 defusedxml 解析后**重新构建** canonical SVG，而非透传模型字符串。当前 `sanitizer_version=2` 在原闭集基础上增加常见、安全的绘图能力：

- `<defs>` + `<marker>`；
- `marker-start|marker-mid|marker-end="url(#local-id)"`，仅允许本 SVG 内本地 id；
- 有限 presentation inline `style`，解析后转换为显式属性；
- 根节点少量无害 metadata/accessibility 属性可输入，但 canonical 输出会丢弃并重建根属性。

仍禁止：`script`、`foreignObject`、`image`、`use`、`style` element、动画、事件处理器、外部 href/URL、任意 CSS、DTD/实体/处理指令。尺寸、节点数、深度、path segment、数值范围、颜色、字体大小等原预算继续生效。

前端 `QuestionIllustration` 同时接受 sanitizer v1/v2，并始终以 `img` 的 SVG data URI 图片上下文渲染，不把模型 SVG 以内联 DOM 方式执行。

## 5. 前端状态

测评页收到文字题后立即进入可答状态。插图有独立局部状态：

`idle -> generating -> ready | not_required | failed`

`generating` 显示“文字题已可作答，正在生成并审核配图”，但输入、选项和提交按钮不被禁用；`ready` 原位显示图；`failed` 显示非阻塞失败提示和重试入口。题目身份变化会终止旧请求结果写回，避免上一题图串到下一题；快速提交进入 feedback 后，反馈卡继续监听同一题的 enrichment。

## 6. 测试边界

普通 CI **不调用真实 LLM**。GitHub Actions 只运行：后端单元/安全回归、TypeScript/lint/build、repository invariants，以及使用 fake LLM 的 Playwright 产品链路测试。

真实模型验收是显式的**本地开发机测试**，不使用部署域名：

```bash
cd frontend
LIVE_LLM_TEST=1 \
LLM_API_KEY=... \
LLM_BASE_URL=... \
LLM_MODEL=... \
pnpm test:e2e:live-llm
```

该命令使用 `playwright.live.config.ts`，本地启动隔离 backend（默认 8125）与 Next frontend（默认 3031），继承开发者 shell 的真实 quiz provider 配置；测试目录是 `e2e-live/`，默认 CI 的 `playwright.config.ts` 不会扫描。默认连续 3 次验证 required CAT 的 `text ready -> generating -> reviewed SVG ready`，并打印 question_id、文字/配图耗时、调用数、repair 次数和 sanitizer 版本。可用 `LIVE_ILLUSTRATION_RUNS=N` 调整样本数。

在真实本地验收完成以前，不把 fake LLM 结果表述为“真实模型已验证”。
