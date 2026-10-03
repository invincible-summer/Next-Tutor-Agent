# M2 学生模型（Student Model）

回答「这个学生会什么」：维护学生的长期画像（身份/学段/可变偏好），并以统一学习证据 journal 作为全部学习评价（作答、判分、概念主张）的唯一事实源。

## Purpose / Scope

- 长期画像 `StudentProfile`：身份、学段、学科活动、可变偏好与自述目标；不含任何能力数值字段（weak/strong 列表、概念状态记忆、旧 BKT 掌握度与能力投影均已删除）。
- 统一学习评价域（`evaluation/` 子包）：所有学习观察的受理、判分、语义评价作业、复核与读侧投影。两类且仅两类学习观察来源——`dialogue`（自然对话/题卡）与 `assessment`（测评中心/CAT）。
- 学习风格推断 `style_inference.py`：`LearningStyle` 的唯一生产写入方（见 [ux.md](ux.md) 的信号来源边界）。
- 不负责：怎么教（[teaching-engine.md](teaching-engine.md)）、是否学会的测评编排（assessment，属 M4）、跨会话提示词记忆（[memory.md](memory.md)）。

## Owned code

`services/api/app/agents/student_model/`（23 个 Python 文件）：

- 顶层：`__init__.py`、`manager.py`（`StudentModel` facade 与 `is_enabled`）、`state.py`（`StudentProfile` / `LearningStyle`）、`store.py`（`students/<id>.json` 读写、`DEFAULT_STUDENT_ID`）、`style_inference.py`（风格折叠规则）。
- `evaluation/` 子包（17 个文件）：
  - `schema.py`——journal 事务、`SourceReceipt`、`ConceptJudgment`、`TaskSnapshot`、job/复核等全部数据类。
  - `store.py`——`students/<id>.learning_evidence.jsonl` 事务 journal 与 `file_lock` 纪律。
  - `service.py`——`LearnerEvaluationService`，唯一事务提交 facade（提交前校验身份/范围/source revision/lease/generation）。
  - `dialogue.py`——对话来源划分与 eligibility、`register_dialogue_source`、`after_turn_hook`。
  - `grading.py` / `evaluator.py` / `llm.py`——MC 确定性判定、开放题语义评价与 LLM 调用。
  - `jobs.py` / `worker.py` / `schedule.py`——评价作业调度（lease/重试/优先级）、后台 worker（lifespan 启动、按 JobKind 路由、重启恢复、outbox 推进）、每日批次调度。
  - `scope.py` / `ports.py`——工作区→卷级授权范围解析；workspace/textbook/图谱读取经 ports 协议注入（默认适配在 `core/learner_runtime.py`，评价核心不 import API 层）。
  - `projections.py` / `readers.py`——可删除重建的 index/views 投影；教学消费者的唯一合法评价读取口。
  - `lifecycle.py`——复核、撤销、删除（generation 重写）。
  - `context.py`——ContextPack 历史组装与防自证循环。
- 集成侧（非本模块拥有，但构成主要入口）：`app/api/v1/learner_evaluation.py`、`app/api/v1/student.py`、`app/api/v1/quiz.py` / `assessment.py`（经 `evaluate_submission` 受理）、`app/core/learner_runtime.py`（composition root）、`app/core/quiz_submission.py`。

## Public contracts

- API（前缀 `/api/v1`）：
  - `GET /learner-evaluation/workspaces[/{wid}]`——评价工作区列表与详情。
  - `GET /learner-evaluation/workspaces/{wid}/{concepts[/concept_key], sessions[/ref], evidence}`——概念判断、会话来源、证据查询。
  - `GET /learner-evaluation/evidence/{source_id}`、`DELETE`（If-Match source_revision，409 冲突）。
  - `GET /learner-evaluation/jobs/{job_id}[/events]`、`POST /learner-evaluation/jobs/{job_id}/retry`。
  - 写面：`POST /learner-evaluation/workspaces/{wid}/synthesis`（语义重综合作业，重复在途幂等返回）、`POST /learner-evaluation/workspaces/{wid}/backfill`（显式来源重分析，≤50 条）、`POST /learner-evaluation/evidence/{source_id}/reviews`（异议复核）。
  - `GET /learner-evaluation/schedule`——普通用户只读调度说明（DTO 不含管理员配置）。
  - `GET /student/{profile,teaching-log,learning-path,error-notebook}`——学生投影；`/student/profile` 读画像，评价性端点（旧 mastery/bloom 等）已删除。
- 受理契约 `evaluate_submission`（`agents/assessment/manager.py` 统一入口，quiz/assessment 路由调用）：完整答案指纹判重（同题同答案重放同 attempt、同题不同答案 409）；MC 字母比对零 LLM 确定性判定随受理事务落盘；开放题语义评价进同一 job 体系（queued/running/retry_wait/succeeded/abstained/failed/cancelled）；帮助事件在判分前入账，assistance_floor 决定该次表现能否计独立；作答回执 `evaluation.status ∈ ready|pending|unavailable|disabled`。
- 读侧契约 `readers.py`：M3/M5/M9 等消费者必须显式携带 workspace 经 scope 求交后的有效投影读取；无 workspace 只允许非个性化建议；读取零 LLM。

## State & storage

| 路径 | 内容 |
|------|------|
| `students/<id>.json` | 画像（profile-only blob，无能力数值） |
| `students/<id>.learning_evidence.jsonl` | 统一学习证据 journal（唯一事实源；每行一个完整事务：`generation+seq` watermark、canonical checksum） |

- journal 内的操作是封闭判别联合：`question_registered / source_registered / assistance_recorded / job_* / result_committed / review_* / interpretation_revoked / scope_* / synthesis_committed`。
- `TaskSnapshot`（题干/选项/答案/等价解/量规）在出题时注册；`question_revision + rubric_hash` 不变即冻结，量规指纹由服务端从冻结内容计算。
- index 与 `learner_views` 是可删除重建的投影（纯函数重放 journal，无需 LLM）；投影缓存不属持久承诺。
- 账号删除/永久删除走 generation 重写（不追加墓碑行）。

## Main flows

1. **对话来源受理（统一 turn hook）**：supervisor 回合结束后，`chat_agent._after_turn_dialogue_receipt` 从磁盘重新加载会话（只受理已可靠保存的学生消息），调用 `dialogue.after_turn_hook`——eligibility 排除空消息/纯操作命令/问候感谢/已被 assessment 拥有的片段，范围内教学语境进入 LLM applicability 判断，注册 dialogue 来源并按需排队语义作业。失败静默，不影响对话流。
2. **统一受理**：`/quiz/grade`、`/quiz/record`、`/assessment/answer` 全部经 `evaluate_submission` 进 journal；题卡/测评中心的作答与聊天轮解耦。
3. **语义评价作业**：lifespan 启动的 worker（`worker.py`）按 JobKind 路由执行，wall-clock 预算、同 workspace 串行、重启恢复（扫描认领 queued/retry_wait/过期 lease）；作业终态后推进 outbox（`consumer="m9"` 等消费方幂等消费，journal `consumer_ack`）。管理员可经 `/admin/learner-evaluation-policy` 选择即时（默认）或每日零点批次（业务时区）。
4. **读侧投影**：前端学习评价页、M3 概念证据状态、M5 前置补缺、M9 弱项全部经 `readers.py`/`projections.py` 读取统一评价，不再各存一套掌握度。
5. **风格折叠**：M8 每轮把反馈分类写入 `UXProfile.recent_feedback`（近 12 条）；`style_inference.py` 读取窗口折叠成风格翻转——「太长」≥2→basic、「太短」≥2→deep、「太难」≥2→step_by_step、矛盾或不足不动。纯规则、零 LLM。
6. **supervisor 读钩子（3b）**：`_adapt_for_turn` 读画像与评价投影，组装 TeachingContext 交给 [teaching-engine.md](teaching-engine.md)，渲染 `[学生智能·…]` 软指令（`STUDENT_MODEL_MODE=0` 时返回空）。

## Dependencies

- 上游：M1 supervisor（3b 读钩子、G3 turn hook）、quiz/assessment 路由（受理入口）、M4 assessment 包（`evaluate_submission` 实现位于 `agents/assessment/manager.py`）。
- 下游消费者：M3（概念证据状态选模式、难度）、M5（前置补缺读 fragile/conflicting/emerging 概念）、M9（outbox 消费作答证据）、前端学习评价页与 `/student/*` 投影。
- 基础设施：`core/learner_runtime.py`（composition root，注入 workspace/textbook/图谱只读适配器）、`core/atomic.py`（file_lock 与原子写）、`core/learner_evaluation_policy.py`（调度策略持久化）。
- 不依赖：M3/M6/M7/M8 的内部状态（M8 的 recent_feedback 是 style_inference 的唯一外部信号源，方向 M8→M2 单向）。

## Invariants / security boundaries

- 画像无能力数值；一切「会不会」的结论只能出自 journal 的事务结果（ConceptJudgment 为证据式 observed/not_observed，unknown/indeterminate 不变成负分或假部分正确）。
- 同一来源版本只有一个当前有效解释；重复投递只有一次效果（答案指纹幂等）。
- 评价只在学习区（workspace 绑定教材/选卷/概念 revision）内进行；scope 归属 404 先于任何检索/LLM；跨教材同名概念不合并；仅 kind=concept 可评价。
- 持久化纪律：短临界区按 journal key `file_lock`，锁内禁止 await，LLM 网络调用期间绝不持锁；中部损坏 `journal_corrupt` 阻止受影响写入、不当空档案。
- 语义作业不阻塞对话/出题：`next` 的 409 `evaluation_pending` 只表示判断在途；硬故障 unavailable 不冒充 pending；`STUDENT_MODEL_MODE=0` 时评价层显式 `disabled`，不静默假装已评价。
- `resolve_student_id()` 是唯一可信学生标识；所有钩子包 try/except，失败只记 trace，绝不影响对话流（统一护栏原则）。

## Configuration

| 环境变量 | 默认 | 语义 |
|------|------|------|
| `STUDENT_MODEL_MODE` | `1` | `0` 关闭 M2：无画像/学习评价/策略注入（评价层按契约显式 disabled） |
| `STRUCTURED_ASSESSMENT_MODE` | `off` | 开放题量规分析旁路/权威判定模式（`shadow`/`active`），影响语义作业判定权威性 |

- 评价调度模式（即时/每日零点、时区）由管理员策略持久化于 `chat_history/settings/learner_evaluation_policy.json`，非环境变量。
- 学段缺省回退本科（`stage_profile` 契约见 [teaching-engine.md](teaching-engine.md)）；`grade=""` 为自动语义。

## Observability

- 每轮 trace 记录 `supervisor_adaptation`（模式/深度/难度建议/rationale）与受理、job、复核相关事件；trace 目录 `traces/`（`GET /trace/{run_id}`）。
- job 事件流可经 `GET /learner-evaluation/jobs/{job_id}/events` 观测；worker 重启恢复扫描有日志。
- 记忆边界相关：journal 不进 prompt、不进日志明文；原始 CoT 与学生隐私按全库红线处理。

## Tests / acceptance

`services/api/tests/`：

- `test_learner_schema.py`、`test_learner_scope.py`、`test_learner_projection.py`、`test_learner_evaluation_policy.py`
- `test_evidence_journal.py`、`test_evidence_query_core.py`、`test_evidence_gate_tiers.py`、`test_evidence_context_recon.py`
- `test_evaluation_jobs.py`、`test_evaluation_lifecycle.py`、`test_evaluation_worker.py`、`test_evaluation_review.py`、`test_evaluation_context.py`、`test_evaluation_routes.py`（均针对本模块 `student_model/evaluation`，非 M7）
- `test_unified_submission.py`、`test_submission_identity.py`、`test_quiz_submission_state.py`
- `test_style_inference.py`、`test_learning_consumers.py`（outbox 消费幂等）、`test_projection_api.py`（`/student/*` 投影面）

## Related ADRs

- ADR-0002 运行数据统一 NEXT_TUTOR_DATA_DIR
- ADR-0004 JSON 持久层 single-worker
