# Next Tutor Agent：CAT 两阶段题目配图稳定化执行计划

版本：2.2；更新日期：2026-09-16；目标分支：`fix/two-stage-quiz-illustration-20260916`；基线：`main@070e50384830d62af42b1133d7322164413c526b`。

本文是本分支的执行与验收合同。它只描述当前仍有效的架构、接口、测试和剩余门槛；已经被实现替代的“CAT 同次生成题面+SVG、90 秒共享预算、失败时把回退说明写入题干”等旧方案不再作为实施依据。聊天 `generate_quiz` / `fit_quiz` 已工作的同步结构化题图链保持现状，不为本次 CAT 修复重构。

## 1. 审计结论与改造边界

本轮已检查仓库 M4/CAT、统一学习证据 journal、Question/TaskSnapshot 冻结模型、SVG sanitizer、题目生成预算、前端 assessment/chat 题卡、Playwright、GitHub Actions 与相关存储清理路径。BM25 教材检索、知识图谱、长期学习评价、CAT 判分、量规冻结等现有模块没有本次根因，保持不动。

已确认的根因与对应处理如下：

- 旧 CAT 在文字题成功后，为了拿 SVG 再执行一次完整 `attempt(policy, ...)`，实际上重新生成整道题；现已删除该路径，CAT 先冻结文字题，SVG 单独 enrichment。
- 旧 `required` 无图时会把“配图未完成”内部回退说明写进 stem；现已删除，学生只看到可独立作答的正式文字题。
- 旧预算只看全局剩余 calls/deadline，无法保证审图窗口；现拆成文字 ≤27 秒、enrichment ≤18 秒且最多 3 次 LLM 调用。
- sanitizer 原先拒绝常见但可安全约束的 `defs/marker` 与 presentation inline style；现升级为 sanitizer v2 的闭合安全子集，同时保留主动内容/外链/CSS 拒绝。
- 异步补图不能修改已冻结 `TaskSnapshot`、rubric 或 revision；现使用独立 `(student_id, question_id, revision)` enrichment cache。
- keyed lock 只能串行，首请求失败时并发第二请求仍会重复烧模型；现改为真正 single-flight，同题并发共享同一后台 Task 的成功或失败结果。
- 历史测评最后一题过去仍可能被手工重放触发新生图；现只有 active CAT 当前题可以启动新 LLM，历史题只允许读取既有审核 cache。
- 先前 dark-mobile E2E 暴露“当前资料”fixed 抽屉默认覆盖 QuizCard 提交按钮；现桌面保持默认展开，移动端默认仅显示轻量入口，用户主动打开后才显示抽屉。
- Chat QuizCard 在 streaming tool-result 重挂期间可能丢失尚未提交的开放题草稿；现以权威 question identity 做有界内存 draft 恢复，服务端 submission 仍是唯一持久化真相。

## 2. 当前架构与不变量

CAT 配图采用两个独立阶段：

1. **文字题阶段**：`POST /assessment/start`、`POST /assessment/next` 只生成、审核、注册一份完全自洽的文字题。即使本次 `illustration_request=required`，题目也必须无图可答。`backend/app/agents/assessment/generator.py` 的 CAT deadline 固定为 27 秒；不再为了插图重新生成完整题。
2. **SVG enrichment 阶段**：文字题显示后，前端对同一个 `(question_id, question_revision)` 调用 `POST /assessment/questions/{question_id}/illustration`。服务端只从 journal 读取权威 TaskSnapshot，只生成 illustration；不得修改 stem/options/answer/explanation/rubric/revision。

题目身份与学习证据继续由现有 journal 承担。enrichment 是附加视觉材料，不成为新的学生能力证据，也不改变 rubric hash。图不得新增文字题没有的解题必需条件，不得泄露正确选项、最终结论或应由学生构造的辅助关系。

聊天结构化题图继续走既有同步冻结路径；本分支只改变 M4 CAT 的交付时序与 sanitizer 兼容性。

## 3. API 与权限合同

新增端点：

```text
POST /api/v1/assessment/questions/{question_id}/illustration
Content-Type: application/json

{"question_revision": 1}
```

客户端只提交题目身份。student_id 永远来自 JWT/`resolve_student_id`，题干、答案、策略、SVG 都不能由客户端覆盖。

服务端顺序固定：

1. 从本人 learning-evidence journal 找 `TaskSnapshot(question_id, revision)`；不存在返回 404。
2. 确认该题属于本人某个 CAT instance；非 CAT 题或他人题返回 404。
3. 若 TaskSnapshot 已带冻结图，直接返回 ready。
4. 若 enrichment cache 已有 `ready/not_required`，不检查当前生成开关，直接返回历史结果；“关闭插图”只禁止**新生成**。
5. cache miss 时要求该题是 **status=active 的 CAT 当前/最后 question_ref**；旧题或 stopped assessment 返回 `assessment_question_not_current`，不得调用模型。
6. 解析账户/运维有效 policy，required 与禁用冲突返回既有 409 `illustration_disabled`。
7. 进入 single-flight enrichment。

成功响应：

```json
{
  "status": "ready",
  "question_id": "q_...",
  "question_revision": 1,
  "illustration": {"kind":"svg","schema_version":1,"sanitizer_version":2,"...":"..."},
  "retryable": false,
  "metrics": {"generation_calls": 2, "generation_elapsed_ms": 1234}
}
```

还允许 `not_required` 与 `failed`；失败不影响文字题作答。当前端点采用同步 HTTP enrichment，不伪造 202 后台 job 状态。

## 4. Single-flight、缓存与取消语义

`backend/app/core/quiz_illustration_enrichment.py` 是该能力的单一实现：

- cache 文件位于账户私有 `students/<sid>.question_illustrations.json`，使用 `file_lock + atomic_write_text`；账号彻底删除时既有 `students/<sid>*` 清理规则覆盖该文件。
- cache key 为 `question_id:question_revision`；保存规范化 illustration 或 `not_required` 决策，不保存模型 reasoning/raw output。
- in-memory `_inflight` 以 `(student, question, revision)` 为 key；第一个请求创建后台 `asyncio.Task`，并发请求 await 同一个 Task。
- single-flight **共享失败结果**，所以两个同时到达的请求不会在第一次失败后串行启动第二套 LLM 调用。
- caller 使用 `asyncio.shield(shared)`；QuestionCard 卸载、fetch Abort 或单个 HTTP 客户端断开不会取消共享底层生成。已经通过 active-current 权限检查的在途任务可以自然完成并写 cache。
- `_run_singleflight` 在结束时从 registry 移除自身；后续用户显式 retry 且题目仍是 active-current 时才可启动新尝试，不形成进程生命周期内的锁表泄漏。

本项目部署仍是 single-worker 文件状态模型；不把本 single-flight 描述为跨进程分布式锁。repository invariant 已固定生产 `uvicorn --workers 1`。

## 5. 预算与 LLM 调用合同

总体验从文字题开始到 enrichment 最终完成最多由两个独立上限组成：27 秒 + 30 秒（2026-09-17 起；enrichment 已异步化、不在学生阻塞路径上，原 18 秒/7 秒按同步阻塞时代校准，真实模型 2–6KiB SVG 常在 7 秒单调用超时处被杀）。

文字阶段：CAT `GenerationBudget.deadline = min(existing_deadline, now+27s)`；只跑文字题 generation/必要 critic/既有有限修订，不调用插图 enrichment。外层 `_generate_cat_question` 不再把保底自检草稿当作交付成功，必须继续降档重采样；预算提供 2 次修复额度。critic 未返回/自身失败/被用户关闭时，结构合格题按 A06 交付为正常 `q_` 题（verification 诚实标注 unreviewed），`q_draft_` 只保留给全链失败的最后兜底。

插图阶段新建 `GenerationBudget(max_calls=3, deadline=now+30s)`：

- Call 1：根据冻结文字题只生成 illustration（单调用超时 18 秒）；required 不能返回 null，auto 可返回 null。
- 对 SVG 运行确定性 sanitizer；不合法时只有剩余时间 ≥8 秒且 repair quota 可用才允许一次生成修复。
- 有效 SVG 在账户开启「生成后审查题图」时进入独立语义 audit（超时 10 秒；纯判定收紧 token，剩余时间充足才允许审计内重画）；至少为最终 audit 预留约 4 秒。
- audit 若返回 repair，只能修改 illustration，修复结果重新 sanitizer，并在总 calls≤3 内再次 audit。
- 每账户审查开关（出题中心「生成审查选项」，`PUT /user/profile` prefs）：`quiz_illustration_review_enabled` 关闭时跳过语义 audit（确定性 sanitizer 始终执行）；`quiz_critic_enabled` 关闭时文字题跳过 critic。环境 `QUIZ_VERIFY_MODE` 保持最终裁量，用户开关只能降档不能升档。
- 时间不足、audit failed、SVG 无法规范化时返回 failed；绝不重新生成另一道题。

`BudgetedLLM` 继续负责 per-call timeout 与 phase deadline；SDK/provider 自身重试不能绕过本阶段 deadline。

## 6. SVG sanitizer v2 安全合同

`backend/app/core/quiz_illustration.py` 仍使用 defusedxml 解析不可信 XML，再按白名单**重建** canonical SVG，而不是把模型字符串透传前端。

v2 新增的兼容能力仅包括：`defs`、`marker`、线/path/polygon 的 `marker-start|mid|end=url(#local-id)`，以及有限 presentation inline style。marker id 必须匹配受限标识符，所有 marker 引用必须解析到同一 SVG 内已允许的 marker；style 先解析、逐属性走既有数值/颜色校验，再转成显式属性。v2.1（2026-09-17）再增加常见安全写法：`opacity/fill-opacity/stroke-opacity`、`font-weight/font-style/font-family`（归一为通用族）、`dominant-baseline`、`stroke-miterlimit`、`letter-spacing/word-spacing`、`text` 的 `dx/dy`、数值 `px` 后缀剥离、根节点 `xmlns:xlink` 声明忽略；模型 JSON 的额外键被忽略、超长 alt/caption 截断（600/120），不再整体拒绝为 invalid_schema。

继续拒绝：`script`、`foreignObject`、`image`、`use`、`style` element、animation、event handlers、href/external URL、任意 CSS property、filter、DTD/entity/processing instruction、未知 namespace。24KiB、180 nodes、depth 10、path segment、坐标/尺寸/颜色/字体范围等原资源上限继续生效。

前端 `QuestionIllustration` 仅接受 schema v1 + sanitizer v1/v2、合法 content hash 与大小上限，并使用编码后的 SVG data URI 作为普通 `<img>`；不 inline SVG，不打开 Markdown raw HTML，不使用 object/iframe。

官方参考：
- MDN `<defs>`：https://developer.mozilla.org/en-US/docs/Web/SVG/Reference/Element/defs
- MDN `<marker>`：https://developer.mozilla.org/en-US/docs/Web/SVG/Reference/Element/marker
- MDN SVG `style` attribute：https://developer.mozilla.org/en-US/docs/Web/SVG/Reference/Attribute/style
- Python XML security：https://docs.python.org/3/library/xml.html

## 7. 前端状态与移动端交互

`useIllustrationEnrichment(question)` 只管理题图局部状态：`idle | generating | ready | not_required | failed`。QuestionCard 的答案控件 disabled 条件不依赖该状态，所以生成/审图期间学生可正常输入与提交。失败只显示非阻塞提示与 retry。

Assessment page 以 `key={qIndex}` 重挂 QuestionCard，旧题 hook state 不会串到下一题；进入 FeedbackCard 后会重新订阅同一题，服务端 single-flight/cache 保证不会重复生成。

Chat 侧 `QuizCard` 的未提交草稿用最多 100 项的内存 Map 按 `question_id:revision` 暂存，用于 streaming 重挂恢复；正式提交或服务端 submission 恢复后立即删除草稿。它不替代 journal、不改变幂等提交语义。

`ChatMaterialsPanel`：desktop 保持原有打开行为；mobile 默认不渲染全屏 fixed drawer，只显示右侧小入口。主动打开后才渲染 backdrop/drawer，关闭只收起 mobile drawer，不影响 desktop 父级开关。该修改直接修复 Playwright dark-mobile 中资料栏拦截题卡提交按钮的真实交互缺陷。

## 8. 测试矩阵与 CI 边界

后端专项覆盖：sanitizer v2 正/负例、marker 外链/未知引用、style element/任意 CSS/event/foreignObject 拒绝、canonical hash；required/null；audit repair + re-audit；18 秒超时后不启动下一 call；success single-flight；failure single-flight；私有路径别名拒绝；active-current 绑定；stopped cache hit 可读但 cache miss 零生成。

fake-LLM Playwright 覆盖：assessment required 先显示文字题与 generating、答题控件可用、同题后到 sanitizer-v2 SVG；现有 chat SVG/card 回归继续保留；`quiz-open-answer` 覆盖 desktop/mobile、提交、重载、反馈和题卡 remount。

GitHub Actions 只运行确定性测试：`backend-core`、`backend-vector-regression`、`frontend-checks`（tsc/lint/build）、`repository-invariants`、`frontend-e2e-smoke`。**CI 不真实调用 LLM，不读取真实 LLM secret，也不使用部署域名。** GitHub workflow 规范参考：https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax

真实模型验收单独放在 `frontend/playwright.live.config.ts` + `frontend/e2e-live/`，只有开发者显式设置 `LIVE_LLM_TEST=1` 才运行。本地 backend/frontend 默认端口 8125/3031，继承开发机真实 provider 环境；默认 CI 的 `playwright.config.ts` 只扫描 `./e2e`，不会发现 live lane。

本地执行入口：

```bash
cd frontend
LIVE_LLM_TEST=1 LLM_API_KEY=... LLM_BASE_URL=... LLM_MODEL=... pnpm test:e2e:live-llm
```

当前会话无法取得开发机真实 LLM secret，因此不得把 fake LLM 或 GitHub CI 表述为“真实模型已验收”。

## 9. 当前执行状态与剩余门槛

截至本文件更新时，代码已完成：文字题 27 秒分阶段、enrichment API、18 秒/3-call budget、single-flight、历史只读/active-current 新生成边界、sanitizer v2、assessment UI、live-local lane、移动端资料栏修复、QuizCard draft remount 修复、专项后端与 fake-LLM E2E。

历史 CI 已验证过 backend-core、vector、frontend type/lint/build、repository invariants；一次 `frontend-e2e-smoke` 的 dark-mobile 失败已定位为 fixed 资料栏遮挡并按产品语义修复。后续提交又增加了 historical cost guard 与 single-flight，因此**最终结论必须以当前 head 的最新 CI 为准**，不能复用旧 run 的绿色结果。

合并前必须满足：

- 当前 PR head 五个确定性 CI jobs 全部 success；失败必须按日志修源码，不 skip gate、不 force click、不靠扩大 timeout 掩盖产品问题。
- 完整 PR diff 无无关依赖变化、无旧的 CAT 二次完整题生成路径、无 keyed-lock 失败后重复生成路径、无生产 secret/运行数据进入 Git。
- `docs/ASSESSMENT_ILLUSTRATION_PIPELINE.md`、本 `plan.md` 与实现一致。
- 若仍把“真实模型验收”作为发布门槛，则只在开发机本地执行上述 live lane并记录结果；不移入 CI，也不走部署域名。当前 PR 保持 Draft 直到该门槛被完成或明确豁免。
- 最终决定合并时使用当前 head SHA 防止 race；合并后再确认 main 对应 workflow 通过。

## 10. 验收标准

功能：required CAT 先稳定返回可作答文字题；配图只补同题；生成中/失败不阻塞答题；已审核历史图在关闭新图后仍可读；旧题/cache miss 不能重新烧模型；同题并发无论成功失败只执行一套生成链。

安全：服务端不信任客户端题干/答案/SVG；sanitizer 重建 canonical SVG；禁止主动内容与外部资源；路径与账户隔离成立；历史题不能越过 active-current 成本门。

性能/成本：文字 ≤27 秒阶段预算；插图 ≤18 秒、≤3 logical calls；预算不足不启动下一阶段；无 whole-question regeneration；取消单个前端订阅不导致共享生成重复。

前端：360/390px 下资料栏不遮挡答题；题图等待不禁用答案控件；QuestionCard→FeedbackCard 同题状态可继续；下一题不串图；sanitizer v1/v2 可显示且未知版本 fail-closed。

工程：backend-core/vector、tsc/lint/build、repository invariants、fake-LLM Playwright 全绿；`package.json` 只新增 live-local script，不改变依赖范围；changed-files 均与本修复或其真实回归有关。
