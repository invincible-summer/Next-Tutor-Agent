# CAT 题目插图：文字先行 + 素材库组件补充

本文件描述 `/assessment`（M4 CAT）当前有效的插图合同。题图统一走“需求声明 → 项目本地模糊检索 → 候选组件构图 → 确定性编译”链路；模型不能直接生成 SVG。聊天 `generate_quiz` / `fit_quiz` 也使用同一组件协议。

## 1. 核心不变量

CAT 题目分为两个彼此独立的阶段：

1. **文字题阶段**：`/assessment/start` 与 `/assessment/next` 生成、审核并注册一份完全自洽的文字题，硬截止 27 秒。即使请求 `illustration_request="required"`，题干也必须在无图时独立可答，不允许写入“如图”“见下图”之类尚不存在的必需条件，也不允许把内部回退说明写进题干。
2. **插图 enrichment 阶段**：文字题已经在浏览器显示后，前端针对同一个 `(question_id, question_revision)` 调用 `POST /api/v1/assessment/questions/{question_id}/illustration`。该调用只可生成补充 `illustration`，不得改题干、选项、答案、解析、量规或 revision。

`TaskSnapshot`、`rubric_hash` 与 `question_revision` 仍是冻结学习证据，不因后到的插图改变。插图属于题目身份上的附加视觉 enrichment；它不得增加文字题中没有的解题必需条件，也不得泄露正确答案或待求结论。

## 2. API、所有权与历史读取

请求：

```text
POST /api/v1/assessment/questions/{question_id}/illustration
{"question_revision": 1}
```

服务端只信任 JWT 解析出的 `student_id`，并从该学生的 learning-evidence journal 读取权威 `TaskSnapshot`。客户端不能提交题干、答案或 SVG。

新生成与历史读取采用不同权限：

- 题目必须真实属于该学生的某个 CAT instance；否则 404。
- **只有进行中 CAT 的当前/最后 question_ref 可以启动新的 LLM enrichment**。旧题、已结束测评的最后一题、手工重放请求都不能重新烧模型预算。
- 已经通过审核并写入 cache 的历史 enrichment 仍可只读返回，即使账户或运维后来关闭“生成新插图”；关闭开关只禁止新生成，不破坏已经交付的历史题面。
- 已经通过 active-current 校验并开始执行的单次 enrichment 可以在学生提交答案/结束本轮后自然完成并写 cache；后续重放不会再次生成。

响应状态：

- `ready`：返回由本地素材组件编译、通过确定性安全检查和（开启时的）语义审图的规范化 SVG；
- `not_required`：有效策略为 off，或 auto 判断不需要图；
- `failed`：在本地素材无匹配、直接 SVG 被拒绝或（开启时）语义审图未通过。失败只影响图，文字题仍可作答；常见模型协议漂移、空 `diagram_scene`、构图超界和一次性 provider 超时会先走本地确定性恢复，不再直接落到该状态。
- `illustration_disabled`：required 请求与账户/运维开关冲突，按现有 409 合同返回。

成功和 `not_required` 决策按 `(student_id, question_id, question_revision)` 写入账户私有 cache。并发控制采用真正的 **single-flight**：同题同时到达的请求 await 同一个后台 Task，因此成功与失败都只消耗一套 LLM 调用；单个浏览器请求取消不会取消共享底层生成。共享 Task 完成后从内存 registry 自动移除，只有后续显式重试才允许新一轮尝试。

## 3. 预算合同

enrichment 在文字题已经显示之后异步执行，不再位于学生的阻塞路径上；两阶段预算独立计量：

- 文字题：≤27 秒（不变）；
- 插图：≤30 秒，最多 3 次 LLM 调用；文字题已经冻结，不因配图重生成。

插图阶段：

1. Call 1：模型只声明需要的器材、物体、图表或几何对象；项目本地执行模糊检索；
2. Call 2：模型读取候选的真实尺寸、锚点、参数契约、类别和场景意图，负责选择素材、组合结构、计算位置与比例、调整参数和已知标签，并只引用返回候选的 `asset_id` 输出 `diagram_scene`；项目确定性编译 SVG；
3. Call 3（仅在账户开启“生成后审查题图”时）：独立核对图与冻结题目/答案的一致性。

需求声明和构图响应都经过闭合字段投影，兼容常见的 `data`/`result` 包装、字符串需求、裸场景和 camelCase 字段。模型响应为空、超时或构图不可编译时，服务端使用已经检索并授权的强语义候选生成留白均衡的最小场景，并优先使用完整实验/几何模板，再走同一编译器；它不会把候选列表按顺序平铺成网格。候选不存在时才如实失败或按 `auto` 降级为无图。响应 `metrics.diagram_recovery=1` 表示使用过这条本地恢复路径。该恢复不读取网络图片、不接受 `fragments` 或模型 SVG，也不使用图表预览样例替代题目真实数据。

构图缓存同时记录近期题图的素材 ID、场景 hash 和规范化图片 hash。自适应下一题会把近期素材作为“有替代时避开”的提示交给构图模型；若模型仍返回完全相同的构图，服务端优先保留模型已经选择的物理组合，仅对节点整体做可验证的小幅布局变体，只有变体无法编译时才启用本地恢复。成功去重会记录 `metrics.diagram_deduplicated=1`，不会改变文字题、答案或参数事实。

实现保留约 4 秒尾部预算缓冲。预算不足时直接失败，不再启动“注定做不完”的新阶段，也绝不重新生成另一道完整题；审查失败也不会触发模型重画 SVG。

### 3.1 每账户审查开关（2026-09-17 起）

出题中心（`/assessment` ConfigCard「生成审查选项」）提供两个持久化用户偏好（`PUT /user/profile` prefs），默认均为关闭：

- `quiz_illustration_review_enabled`（默认关）：开启后增加一次配图语义审计 LLM 调用，仅确定性组件编译仍始终执行；
- `quiz_critic_enabled`（默认关）：开启后增加独立审题（critic），关闭时结构合格题作为正常可答习题交付（`q_` 正常 id + verification.status=unreviewed，不冒充已审核）。环境变量 `QUIZ_VERIFY_MODE` 保持最终裁量：运维设为 basic/off 时用户开关无法升档。

### 3.2 草稿语义（2026-09-17 起）

`q_draft_` 保底自检草稿只保留给“生成+修订+降档重试全部失败”的最后兜底。critic 未返回/自身失败/被用户关闭时，结构合格的题目按 A06 交付为正常 `q_` 题并诚实标注 unreviewed；外层 `_generate_cat_question` 循环不再把草稿当作交付成功，必须继续降档重采样（预算给 2 次修复额度）。

## 4. SVG 安全边界

`app/core/quiz_illustration.py` 使用 defusedxml 解析后**重新构建** canonical SVG；组件编译器只绘制项目素材的白名单节点和连接，绝不透传模型字符串。组件图使用 `sanitizer_version=3`。

- `<defs>` + `<marker>`；
- `marker-start|marker-mid|marker-end="url(#local-id)"`，仅允许本 SVG 内本地 id；
- 有限 presentation inline `style`，解析后转换为显式属性；
- 根节点少量无害 metadata/accessibility 属性可输入，但 canonical 输出会丢弃并重建根属性；
- v2.1（2026-09-17）：`opacity/fill-opacity/stroke-opacity`、`font-weight/font-style/font-family`（归一为通用族）、`dominant-baseline`、`stroke-miterlimit`、`letter-spacing/word-spacing`、`text` 的 `dx/dy`；数值属性容忍并剥离 `px` 后缀；根节点忽略 `xmlns:xlink` 声明。模型侧额外 JSON 键被忽略而非整体拒绝，超长 alt/caption 截断到 600/120。

仍禁止：`script`、`foreignObject`、`image`、`use`、`style` element、动画、事件处理器、外部 href/URL、任意 CSS、DTD/实体/处理指令、`class` 属性。尺寸、节点数、深度、path segment、数值范围、颜色、字体大小等原预算继续生效。

历史题面仍可读取 sanitizer v1/v2；新组件图始终以 `img` 的 SVG data URI 图片上下文渲染，不把任何模型 SVG 以内联 DOM 方式执行。

## 5. 前端状态与切换语义

测评页收到文字题后立即进入可答状态。插图有独立局部状态：

`idle -> generating -> ready | not_required | failed`

`generating` 显示“文字题已可作答，正在生成并审核配图”，但输入、选项和提交按钮不被禁用；`ready` 原位显示图；`failed` 显示非阻塞失败提示和重试入口。

`QuestionCard` 以题目序号 key 重挂，旧请求结果不会写入下一题。答题后切到 `FeedbackCard` 时会重新订阅同一题 enrichment；服务端 single-flight/cache 保证这不是第二次生成。移动端聊天“当前资料”侧栏默认只显示轻量入口，不再以 fixed 抽屉覆盖题卡和提交按钮，用户主动打开时才出现遮罩与抽屉。

## 6. 测试边界

普通 CI **不调用真实 LLM**。GitHub Actions 只运行：后端单元/安全回归、TypeScript/lint/build、repository invariants，以及使用 fake LLM 的 Playwright 产品链路测试。

真实模型验收是显式的**本地开发机测试**，不使用部署域名：

```bash
cd apps/web
LIVE_LLM_TEST=1 \
LLM_API_KEY=... \
LLM_BASE_URL=... \
LLM_MODEL=... \
pnpm test:e2e:live-llm
```

该命令使用 `playwright.live.config.ts`，本地启动隔离 backend（默认 8125）与 Next frontend（默认 3031），继承开发者 shell 的真实 quiz provider 配置；测试目录是 `e2e-live/`，默认 CI 的 `playwright.config.ts` 不会扫描。默认连续 3 次验证 required CAT 的 `text ready -> generating -> reviewed SVG ready`，并打印 question_id、文字/配图耗时、调用数、repair 次数和 sanitizer 版本。可用 `LIVE_ILLUSTRATION_RUNS=N` 调整样本数。

在真实本地验收完成以前，不把 fake LLM 结果表述为“真实模型已验证”。
