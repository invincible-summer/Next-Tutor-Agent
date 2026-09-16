# Next Tutor Agent：两阶段题目配图稳定化执行计划

版本：2.0；制定日期：2026-09-16；目标分支：`fix/two-stage-quiz-illustration-20260916`；基线：`main@070e50384830d62af42b1133d7322164413c526b`。

## 1. 已审计现状与确定根因

本轮已重新核对仓库根目录、`backend/app`、`frontend/src`、评估 journal、CAT、自适应出题、SVG 规范化器、前端题卡、现有 E2E 与 GitHub Actions。项目现有的 BM25 教材检索、知识图谱、学习证据 journal、CAT 判分和 Question/TaskSnapshot 冻结模型均继续保留；它们不是本次故障源，不为“有修改而修改”。

当前必须修的链路是：

1. `backend/app/agents/assessment/generator.py` 的 CAT 快速路径先 `attempt("off")` 生成一题文字题，再在剩余预算满足“2 次调用 + 4 秒”时执行 `attempt(policy, ...)`。后者不是给已成功题目补图，而是重新生成另一道完整题并重新审题；文字成功并没有降低配图成本。
2. 同文件 `required` 最终无图时把 `【配图未完成：先返回文字保底版，并非带图题】` 写入 stem；所以前端收到的是“失败说明 + 题目”，而不是“文字题已就绪、配图仍在进行”的真实状态。
3. `/assessment/start` 和 `/assessment/next` 当前等待整道含图题生成后才返回；前端 `assessment/page.tsx` 只有 `idle/asking/feedback/done`，`QuestionCard` 只会同步渲染 `question.illustration`，没有独立的配图状态。
4. `GenerationBudget` 只有全局 deadline/calls，没有文字、生成图、审图、修图之间的保留预算。现行 4 秒启动门槛无法保证随后 critic 完成。
5. `quiz_illustration.py` 的 SVG 语法白名单过窄：浏览器常见的 `defs`、`marker`、`marker-start/end`、安全的行内 `style` 等会被项目自身拒绝；根节点也会因无害属性触发拒绝。
6. `TaskSnapshot` 明确是冻结题面。异步补图如果原地修改它，会造成 `question_revision` 不变但题面变化，并与正在作答的客户端产生竞态，因此本轮不走这条路。
7. 最新 GitHub Actions run `35079617255` 中 `backend-core`、`backend-vector-regression`、`frontend-checks` 已通过；CI 总失败来自两类真实问题：repository invariant 报 `ConfigCard.tsx` 含已退役 `conceptstate` 标识；Playwright 中 assessment 插图开关可访问性契约失配，以及 `quiz-open-answer` 因卡片重渲染使提交按钮 detach/随后 disabled 而超时。不得通过跳过测试、扩大 timeout 或放宽 invariant 掩盖。

参考规范：MDN `<defs>` https://developer.mozilla.org/en-US/docs/Web/SVG/Reference/Element/defs 、`<marker>` https://developer.mozilla.org/en-US/docs/Web/SVG/Reference/Element/marker 、SVG `style` attribute https://developer.mozilla.org/en-US/docs/Web/SVG/Reference/Attribute/style 。注意 MDN 说明 `<defs>/<marker>` 的通用 SVG 内容模型也允许 `script/foreignObject/image/style element` 等高风险能力；本项目只吸收满足题图需求的安全子集，不把浏览器“能渲染”误当成“允许执行”。GitHub Actions 继续遵循最小权限和真实 gate，参考 https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax 。

## 2. 目标架构：文字题先交付，SVG 作为受审阅 enrichment 后到达

CAT/测评中心改成两阶段，但不破坏聊天题卡当前已工作的同步路径：

- **阶段 A：文字题**。`/assessment/start`、`/assessment/next` 只生成、critic、注册一份完全自洽的文字题。单题阶段硬 deadline 27 秒。即使本次 `illustration_request=required`，文字题本身也不能出现“见图”“如下图”等依赖尚未存在图片的条件。成功后立即返回并在前端可答。
- **阶段 B：配图 enrichment**。当前题在前端出现后，如果 effective policy 为 `auto|required`，前端立即调用新的题目配图端点。题卡显示“正在生成配图…”，但作答输入与提交保持可用。该端点只接收题目身份，不让客户端上传/篡改权威题干。
- enrichment 以 `(student_id, question_id, question_revision)` 为唯一键。服务端从 journal 读取已冻结 TaskSnapshot 和所属 CAT instance，再让 LLM **只生成 illustration**；绝不重新生成 stem/options/answer/rubric。
- enrichment 是补充视觉解释，不能新增解题必需条件、改变答案、暗示答案。它通过 SVG 确定性清洗 + 独立语义审图后才成为 `ready`。因此冻结 TaskSnapshot、rubric_hash、question_revision 不变。
- 服务端持久化已通过审查的 enrichment；刷新/恢复时同一题直接命中缓存，不重复烧 LLM。运行中的请求由 keyed asyncio lock 去重，同题并发只生成一次。

建议新增：`backend/app/core/quiz_illustration_enrichment.py`，职责只包括 enrichment DTO、持久化、单题图生成/审图预算与并发去重；不混入 CAT 难度逻辑。持久化放在账户私有的 `students/` 命名空间中，原子写 + `file_lock`，文件只存题目身份、状态、规范化后的 illustration、时间和审计摘要，不存模型原始 reasoning。

## 3. API 与状态合同

新增（路径最终按现有 router 风格落地）：

`POST /api/v1/assessment/questions/{question_id}/illustration`

请求：`{"question_revision": 1}`。

响应固定为：

- `200 {status:"ready", question_id, question_revision, illustration}`：已缓存或本次成功；
- `202 {status:"generating", ...}` 仅在实现真正后台 job 时使用；本轮优先采用前端发起的单请求同步 enrichment，题目已经显示，因此无需再造后台 worker；
- `200 {status:"not_required", ...}`：有效策略为 off/none 或 auto 判定无需图；
- `200 {status:"failed", retryable:false, code}`：18 秒预算耗尽、语义审图未通过或 SVG 最终不合法；不修改题干，也不把内部错误写到 stem。

鉴权、ownership、当前 CAT 绑定、question revision 必须服务端校验；用户 B 不得读取/触发用户 A 的题图。`required` 时前端保留明确状态；生成失败显示“配图生成失败，本题文字版仍可作答”，不能假装题图完成。

## 4. 严格预算与调用门槛

总体验收预算按用户要求固定为 45 秒：文字阶段最多 27 秒；配图阶段最多 18 秒。实现不再依据模糊的“剩余 4 秒 + 2 call”启动。

文字阶段：
- CAT 使用独立 `GenerationBudget(deadline=now+27s)`；只做文字题 generation + 必要 critic/一次有界文字修订。
- 不进入 illustration generation，因而不再出现第二次完整 `attempt(policy, ...)`。

配图阶段：
- 独立 `GenerationBudget(deadline=now+18s, max_calls=3)`。
- Call 1 只生成 illustration；确定性 sanitizer 不计 LLM call。
- 若 SVG 可规范化，Call 2 做题干/图/答案一致性审图；critic 输出 `passed` 或一个只含 illustration 的修复版本。
- 只有在 `remaining_seconds` 足以保留最终审图窗口时才允许修复。推荐门槛：修复前至少 7 秒、最终审图至少保留 3.5–4 秒；任何步骤都使用 `phase_deadline/call_timeout`，不能把全局剩余时间一次吃光。
- 最多一次 illustration repair；修复后仍须确定性 sanitizer，并在预算允许时做最终一致性确认。没有最终通过就不发布。
- 绝不回退到“再生成一整道题”。

需要补充预算单测：27/18 deadline、call 上限、修复保留窗口、超时后不再启动下一 LLM call、同题并发去重。

## 5. SVG 安全兼容修复

`backend/app/core/quiz_illustration.py` 保留 XML parser、字节/节点/深度/path segment、尺寸、颜色、禁止事件处理器/外部资源等安全边界，但扩展**受控安全子集**：

- 允许 `defs` 与 `marker` 容器；marker 仅允许本地 id、有限 markerWidth/Height/refX/refY/orient/markerUnits；
- 允许 `marker-start|marker-mid|marker-end="url(#local-id)"`，引用必须指向本 SVG 中允许的 marker；任何 http/data/javascript/external URL 仍拒绝；
- 允许常见 presentation `style` **属性**，但先解析成白名单声明再规范化为显式属性；只收 stroke/fill/stroke-width/stroke-linecap/stroke-linejoin/stroke-dasharray 等当前安全集合。不要开放任意 CSS；
- 不开放 `<style>` element、`script`、`foreignObject`、`image`、animation、filter、external href。`defs` 中同样执行递归白名单；
- 对根 `<svg>` 的常见无害 metadata/version/accessibility 属性采取“验证后丢弃或规范化”的兼容策略，不因模型写 `version`/ARIA metadata 就拒掉整图；最终输出仍由服务端重建 canonical root；
- 增加 marker/defs/style 正例、external url/script/foreignObject/style element/恶意 CSS 负例、canonical hash 稳定性测试。

## 6. 前端改造

`assessment/page.tsx` 在收到 start/next/active 的文字题后立即进入 `asking`，并并行触发 illustration enrichment。新增局部状态而不是扩大整个页面 stage：`idle | generating | ready | not_required | failed`。

`QuestionCard.tsx`：
- 文字题立即渲染；
- `generating` 时在图位置显示轻量 skeleton/spinner + “正在生成配图，不影响作答”；
- ready 后用现有 `QuestionIllustration` 组件替换提示，不复制 SVG 注入逻辑；
- failed 时显示非阻塞状态，输入/提交不禁用；
- question_id/revision 变化时取消旧请求结果写回，避免上一题 SVG 串到下一题。

`ConfigCard.tsx` 同时修复 invariant 与现有 E2E accessibility 契约：避免 retired `conceptState` 标识；“允许题目插图”使用稳定的 button/`aria-pressed` 和可访问名称，既可被用户操作也可被 Playwright 稳定定位。

## 7. 测试、真实 LLM 与 CI

后端专项：
- sanitizer compatibility/security；
- text-first CAT：required 请求首响应无 illustration、无“配图未完成”污染、27 秒预算；
- enrichment ownership/revision/cache/idempotency/concurrency；
- 18 秒 phase budget 与只补图不改题；
- critic 不一致/答案泄漏/非法 SVG 不发布；
- required/auto/none/off 策略真值表。

前端/E2E：
- required：先看到可答文字题和“正在生成配图”，随后同一 question_id 出现 `data-testid` 可观测 SVG；
- 生图期间输入与提交可用；
- 失败提示不把错误写入题干；
- 快速切下一题不会串图；
- 刷新同题命中缓存；
- 修复 `quiz-open-answer.spec.ts` 的 DOM detach 根因，不能仅增加 timeout；
- 保留现有 chat illustration smoke，防止本次 CAT 改造回归聊天题卡。

真实模型验收必须在合并前完成。优先复用仓库已经配置的 quiz provider（`get_llm("quiz")` / `QUIZ_MODEL`），从实际前端测评页选择“必须配图”，至少连续多次走完整 `start -> 文字题可见 -> generating -> SVG ready`。每次记录 question_id、文字返回耗时、配图耗时、是否经过 repair、sanitizer/critic 结果；验收目标为所跑样本全部最终看到规范化 SVG，且无“配图未完成”前缀、无整题重生。如果当前 GitHub Actions 没有 LLM secret，则不伪造“真实模型已测”：利用已配置部署或可用 secret lane；若连接权限无法读取/设置 secrets，只能明确记录该外部权限阻塞，不能以 fake LLM 替代真实验收结论。

CI 收口：
- 先修代码造成的 invariant/E2E 失败；
- workflow 只做必要的稳定性修正（例如测试隔离/并发），不删除 gate；
- branch Actions 全绿后再建 PR；如 Actions 写权限允许，重跑失败 job 验证非偶发；
- 合并 `main` 后再检查 main SHA 的 workflow run，main 未绿不算完成。

## 8. 完成标准

只有以下全部满足才结束：

1. required CAT 首先稳定返回可作答文字题，题干中不再出现“配图未完成”内部回退文案；
2. 配图只针对已成功文字题生成，不再调用完整题目 `attempt(policy, ...)`；
3. 文字 deadline ≤27s，配图 deadline ≤18s，总体验预算 ≤45s；配图没有足够审图余量时不启动不可完成的修复；
4. sanitizer 接受安全的 defs/marker/行内 style 常见 SVG，同时继续拒绝执行代码、外部资源与危险 CSS；
5. 前端能观测 `generating -> ready`，且等待配图不阻塞答题；
6. TaskSnapshot/rubric/question_revision 不被异步补图原地篡改；
7. 现有 backend、frontend type/lint/build、repository invariants、Playwright smoke 全绿；
8. 真实 quiz LLM + 实际前端 required 模式连续验收通过，并有可核对记录；
9. PR 审核 diff 无死代码/旧重复路径，文档更新与实现一致；
10. 合并到 `main`，并确认 main CI 通过。
