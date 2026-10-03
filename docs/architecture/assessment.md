# Assessment（M4 智能测评）

M4 回答「学生真的学会了吗」：统一承载练习题生成、统一作答受理、三级评分、量规冻结、CAT 自适应测试、提示与异议复核，以及错题本/最近习题投影。

## Purpose / Scope

- 测评域的**出题侧**：三条题目生成路径（聊天工具 `generate_quiz` / `fit_quiz`、M4 约束出题与 CAT）共用同一套质量门（结构校验 + critic 独立重解 + 命题蓝图两轮化）与生成预算。
- 测评域的**作答侧**：所有作答（聊天题卡、习题中心、CAT）经唯一受理入口 `evaluate_submission` 进入统一学习评价协议——服务端权威题目、答案指纹幂等、MC 确定性判分、开放题语义作业。
- 量规（rubric）在学生作答前冻结；帮助事件在判分前入账；异议走 journal 复核生命周期。
- 学习证据 journal 本身属 M2 统一学习评价域（`students/<id>.learning_evidence.jsonl`，见 [student-model.md](student-model.md)）；本文只描述 M4 视角的注册、受理与判分协议。
- 题图（question illustration）只影响出题与批改交付形态，其管线内部（检索/构图/审核/冻结）见 [diagrams-illustration.md](diagrams-illustration.md)；工具调用协议与 Skill 门控见 [skill-runtime.md](skill-runtime.md)。

## Owned code

- `services/api/app/agents/assessment/`
  - `manager.py` — 统一受理 facade：`evaluate_submission`、`register_task_snapshot(s)`、`record_assistance`、`load_task_snapshot`、`resolve_submission_binding`、`run_assessment_job` 与错误族（`QuestionNotFound` / `QuestionRevisionMismatch` / `QuestionAlreadyAnswered` / `AnswerTooLarge` / `SessionNotOwned` / `WorkspaceNotOwned` / `AssessmentBindingError` / `ScopeRevisionConflict`）。
  - `adaptive_test.py` — CAT 实例：难度步进 `next_difficulty`、停止判定 `should_stop`、报告 `report`、journal 持久化。
  - `generator.py` / `question.py` / `state.py` — M4 约束出题、题目模型 `Question` / `QuestionType`、`AssessmentContext`。
- `services/api/app/agents/student_model/evaluation/` — 统一学习评价域（journal 事务、语义作业 `jobs.py`、worker `worker.py`、投影 `projections.py`、复核 `lifecycle.py`）；M4 是其出题/受理侧的主要生产者与消费者（读侧 API 见 `api/v1/learner_evaluation.py`，属于学生模型文档范围）。
- `services/api/app/core/`
  - `quiz_verify.py` — 出题质量门（结构校验 + critic 重解 + `too_shallow` 深度拦截 + `RUBRIC_REQUIREMENT` 量规契约）。
  - `quiz_design.py` — 出题两轮化（命题蓝图 `quiz_blueprint`，two_pass/single）。
  - `quiz_generation_budget.py` — `ASSESSMENT_GENERATION_*` 预算（`BudgetedLLM`）。
  - `quiz_attempts.py` — 会话侧作答落点与 `latest_quiz_digest`、`record_quiz_attempt`。
  - `quiz_submission.py` — `GET /quiz/submission` 只读恢复投影。
  - `quiz_grounding.py` — 检索证据到命题输入的投影层（复用 `KnowledgeSearchTool`，不另造置信度标尺）。
- `services/api/app/tools/quiz.py`（`generate_quiz`）、`services/api/app/tools/fit_quiz.py`（`fit_quiz`）— 出题工具（生成质量链归本文；通用工具协议归 [skill-runtime.md](skill-runtime.md)）。
- API 路由：`services/api/app/api/v1/quiz.py`、`assessment.py`、`assessment_grounding.py`（教材 grounding）、`assessment_illustration.py`（CAT 补图启动，管线见 [diagrams-illustration.md](diagrams-illustration.md)）。

## Public contracts

**统一受理（chat 题卡与习题中心共用）**

- `POST /quiz/record`、`POST /quiz/grade`：请求体 `QuizSubmitRequest{question_id, question_revision, student_answer, session_id?, reply_message_ref?}`（`extra=forbid`），一律携带题目身份；旧 `stem/correct_answer/raw_grade/record=false` 契约已删除，旧客户端得到明确 422。两个端点均为**非流式**：校验完成后一次返回 JSON `202`（`attempt_id` 受理回执；`/quiz/grade` 不再是 SSE）。
- 判定三级 `[对]/[部分对]/[错]`（`correct/partial/wrong`）：MC 按声明题型 `q_type` 走确定性字母比对（零 LLM），随受理事务落盘；开放题语义评价是异步作业，回执 `evaluation.status ∈ ready|pending|unavailable|disabled`。
- `GET /quiz/submission?question_id=&question_revision=`：从学习证据 journal 纯读恢复受理状态、本题判分与反馈；未提交为 null，他人题 404，版本不匹配 409，绝不触发模型或追加受理。
- 幂等：完整答案指纹判重——同题同答案重放同 attempt；同题不同答案 `409 question_already_answered`；重试不产生二次评分与模型费用。
- 错误语义：归属错误一律 404「不可见」；`413 answer_too_large`（32 KiB）；`409 scope_revision_conflict`（教材范围变化）。

**测评中心 API（前缀 `/api/v1/assessment`）**

- `POST /submissions`（`Idempotency-Key` header 可选）、`GET /submissions/{attempt_id}`。
- `GET /questions/{qid}`（`QuestionPublic` 白名单投影，作答后揭晓 answer/explanation）、`POST /questions/{qid}/practice`（`mode=same|variant` 再练一次，新题带 `origin_question_ref`/`task_family`）、`POST /questions/{qid}/hint`、`POST /questions/{qid}/reveal`。
- `GET /records` — 本人原始作答档案分页（assessment 来源）。
- CAT：`POST /{start,answer,next,abandon}` + `GET /{report,active}`；`active` 三态（进行中当前题公开内容 / 终态 `stop_reason`+summary / 无会话 none）；停止结论是**诊断性** `stop_code` 枚举（`sufficient_for_current_claim|needs_clarification|max_questions|max_time|user_stopped|generation_failed`），前端映射为中性表述。start/next 载荷剥离 answer/explanation，判分全在服务端。
  `start` 支持 `illustration_mode=v1|v2`、`generation_hint` 与 `evaluation_mode=closed_loop|temporary`。账户默认配图方式缺省为 V1，单次测评可覆盖；V1 保留旧模型主导的组件绘图链，V2 使用最新素材库装配链。
  两条链都在一次补图请求内进行有界校正，V2 从冻结公开题文首次提取材料，不改题干/答案/量规；新版本可重建当前题的旧失败任务，已冻结图仍复用。配图预算和科学发布门见 [diagrams-illustration.md](diagrams-illustration.md)。
  `workspace_id` 与 `concept_keys` 都可以为空：无工作区时不调用知识检索，题目统一为临时出题；选工作区但不选概念时，服务端只按提示词对工作区教材检索一次，把检索证据缓存到 CAT 实例后复用。

**提示与异议（chat 侧）**

- `GET /quiz/hint`：从冻结量规派生关键步骤提示（不含答案/判定），服务端同步落 `hint_requested` 帮助事件。
- `POST /quiz/dispute`：登记 `review_requested` 并入队 review job；同一来源只允许一个 active review，复核走 `review_resolved` 生命周期，可撤销错误解释而不抹除原始证据。

**出题与量规**

- 三条生成路径统一过 `quiz_verify`（MC 答案字母须在选项内、选项非空去重、题干/解析非空；critic 独立重解 + `too_shallow`）与 `quiz_design` two_pass 蓝图（`generate_quiz` 与 M4 约束单题共用；`fit_quiz` 内置「拆题→五层变式策略」单轮两段式，不接蓝图）。
- 题目通过校验获得稳定题号（每套唯一前缀 `q_<uid>_<i>`）后，以 `rubric_criteria[{id,description,weight,critical}]` + `equivalent_solutions` 按 `rubric_hash` 冻结随题落盘；改量规必须新 `question_revision`，指纹由服务端复核。
- `GET /quiz/recent` 与错题本（`GET /student/error-notebook`，`evaluation/projections.py::wrong_answer_items`）均为 journal 实时投影，无物化副本；错题重练经 `?q=&send=1` 深链回到教练对话。

## State & storage

- `students/<id>.learning_evidence.jsonl` — 统一学习证据 journal（唯一事实源）：`question_registered` / `source_registered` / `assistance_recorded` / `job_*` / `result_committed` / `review_*` 等封闭操作；CAT 实例也持久化于 journal `assessments`（无单槽 `assessment.json`）。
- 会话侧（受理后回写，属聊天内核存储）：session quiz_history 的 `result{verdict, student_answer, attempt_id}`、transcript【作答记录】/【出题记录】；流式回合保存前经 `merge_quiz_results_from_disk` 合并盘上作答防覆写。
- 兼容缓存 `students/<owner>.question_illustrations.json`（v1 题图历史，见 [diagrams-illustration.md](diagrams-illustration.md)）。
- 所有账号数据经运行数据根统一管理，账户删除与孤儿清理按 `students/` 前缀覆盖。

## Main flows

1. **出题**（三条路径之一）→ `quiz_grounding` 投影工作区教材证据（CAT 的无概念工作区模式只检索一次；无工作区不检索）→ `quiz_design` 蓝图轮（fail-open 回退 single）→ 生成（`complete(disable_thinking=True)`，`BudgetedLLM` 受 `ASSESSMENT_GENERATION_*` 约束）→ `quiz_verify` 两层校验，草稿题不再作为 CAT 结果交付 → 题目（含冻结量规）注册 journal `TaskSnapshot`。用户提示词会作为风格、背景和题型偏好传给出题及 V1/V2 配图链，但不能覆盖教材事实、答案或安全约束。
2. **受理**：`/quiz/record`（MC）或 `/quiz/grade`（开放题）/`/assessment/submissions` → 归属校验（404 先于一切）→ `load_task_snapshot` 服务端权威题目 → `evaluate_submission`：帮助事件（`assistance_floor`）判独立资格 → 答案指纹判重 → MC 判定随事务落盘；开放题语义 `EvaluationJob` 入队 → `202` 受理回执。
3. **语义评价**：lifespan 启动的评价 worker（`evaluation/worker.py`）按 JobKind 路由执行（重启恢复、wall-clock 预算、同 workspace 串行）→ `result_committed` 落 journal → outbox 事件由 M9 幂等消费（见 [learning-orchestration.md](learning-orchestration.md)）。
4. **CAT**：工作区闭环 start（按评价投影门控）或无工作区/临时 start → next（当前题未答幂等重发；CAT 首题走 `get_llm("quiz")` 快速通道）→ answer。临时模式只保留本次运行的本地判分结果，不创建 `SourceReceipt`、语义评价作业或学习证据；report/active 可恢复本次运行状态。per-student 生命周期锁 + journal `file_lock` 防并发。
5. **帮助与异议**：hint/reveal 答前入账 → 判分自动携带 assistance 事件；dispute → review job → `review_resolved`（撤销解释保留原始证据）。
6. **恢复与投影**：`GET /quiz/submission` 按题目身份只读恢复（支持无学习区的 task-only 判分）；`/quiz/recent`、错题本从 journal 现算。

## Dependencies

- **M1 对话内核**：supervisor 收尾检测触发出题工具；`latest_quiz_digest` 注入 status recap；工具协议与 Skill 门控见 [skill-runtime.md](skill-runtime.md)。
- **M2 统一学习评价域**：journal、语义作业体系、`SourceReceipt`（`observed_at` / workspace 归属 / `provenance=live|migration|demo_fixture`）。
- **M3 教学引擎**：「部分对」是 REMEDIATION 根因信号；难度投影与 stage_profile 锚点注入出题 prompt。
- **M5 知识智能**：教材 grounding 检索域；概念 id 规范化。
- **M9 编排**：`record_quiz_evidence`（SRS 复习质量 + 任务归因，attempt_id 幂等）。
- **题图**：[diagrams-illustration.md](diagrams-illustration.md)（材料合同 `visual_role`、`essential` 发布门）。
- **LLM**：`core/llm_async.py` 单通道（`get_llm("quiz")` 等 profile），提取型调用固定关闭思考。

## Invariants / security boundaries

- `resolve_student_id()` 是唯一可信身份；任何非空 `session_id` 在 LLM/评分/持久化之前过归属校验，他人资源 404「不可见」。
- 服务端权威：答案/选项/解析/量规/知识点从本人 journal/quiz_history 快照解析，客户端不可注入；解析失败降级 `unverified_practice`（`/quiz/record` 返回 `question_unresolved`，`/quiz/grade` done 标 `unverified: true`，两者零评价写入）。
- `unknown` 判定（未真正评分）一律不落评价写入；明确判错是有效负向观察。
- 量规先冻结后作答；帮助后表现不伪装独立；客户端自报帮助状态不采信。
- `AnswerRequest.raw_grade` 与 `StartRequest.mastery` 不存在——客户端无法注入批改或起点状态。
- 变式证据分级：fit_quiz 题套作答携带 `origin_question_ref` 同族关系进统一评价。
- 游客（guest token）仅开放文字聊天、临时出题与本题批改（`guest_learning` 内存域），不进入 journal 长期评价。
- 无工作区的测评中心出题与显式 `temporary` 模式均不进入学习评价闭环；只有绑定工作区且选择 `closed_loop` 才会提交正式评价观察。

## Configuration

- `LEARNER_EVALUATION_MODE`（`active|off`，默认 `active`）：统一评价域总开关。`off` = 暂停长期评价而非关练习——受理与 MC 判分照常，语义解释降级跳过；CAT `start/next` 受门控（off 期间不开新实例，在途实例可继续作答）。（旧 `ASSESSMENT_ENGINE_MODE` 已删除。）
- 账户 `profile.prefs.quiz_illustration_mode`（缺省 `v1`）控制测评中心默认 V1/V2；`POST /assessment/start` 的 `illustration_mode` 仅覆盖本次实例。
- `QUIZ_VERIFY_MODE`（`critic|basic|off`，默认 `critic`）：出题质量门。
- `QUIZ_DESIGN_MODE`（`two_pass|single`，默认 `two_pass`）：命题蓝图两轮化。
- `STRUCTURED_ASSESSMENT_MODE`（`off|shadow|active`，默认 `off`）：`off` 旧三级文本批改；`shadow` 旁路计算量规条目分析并落盘对照（不改变判定/不写能力）；`active` 有冻结量规的开放题以结构化分析为权威判定（分数由服务端按量规权重本地计算），done 事件与 `/quiz/record` 结果携带 `structured` 块。
- `ASSESSMENT_GENERATION_MAX_ATTEMPTS`（默认 2）/ `ASSESSMENT_GENERATION_MAX_CALLS`（默认 6）/ `ASSESSMENT_GENERATION_DEADLINE_SECONDS`（默认 90）：整组生成预算（嵌套题图也受此约束）。
- `LEARNER_EVAL_*`：评价 worker 并发、租约、预算、合成等待等运行参数。
- 管理员 `GET/POST /admin/learner-evaluation-policy`：语义解释即时（默认）或每日零点批次（业务 IANA 时区），策略文件 `chat_history/settings/learner_evaluation_policy.json`（expected_revision CAS）。

## Observability

- 出题审计元数据 `verification`（attempts/dropped/`dropped_shallow`/critic 状态/design two_pass 状态/grounding_gate tier）随工具结果进 SSE、quiz_history 与 Trace。
- journal 自身是审计账本：jobs/events、`generation+seq` watermark、canonical checksum、`OpConsumerAck`。
- 评价作业可观测面：`GET /learner-evaluation/jobs/{job_id}[/events]`（属于学生模型文档的读侧 API）。
- prompt 全部经 `prompts/registry.py` 版本钉扎（如 `quiz_generate@1.2.0`、`quiz_fit@1.2.0`、`quiz_blueprint@2.0.0`）。

## Tests / acceptance

`services/api/tests/`（`python -m tests tests.test_<module>`）：

- 统一受理：`test_unified_submission.py`、`test_submission_identity.py`、`test_quiz_submission_state.py`、`test_quiz_ownership.py`。
- 测评中心与 CAT：`test_assessment.py`、`test_assessment_lifecycle.py`、`test_assessment_identity.py`、`test_assessment_binding_cas.py`、`test_assessment_generation_fast.py`、`test_classroom_assessment.py`。
- 出题质量：`test_quiz_quality.py`、`test_quiz_design.py`、`test_quiz_card_contract.py`、`test_chat_quiz_intent.py`、`test_quiz_grounding.py`、`test_quiz_grounding_provenance.py`。
- 评价域与复核：`test_evaluation_jobs.py`、`test_evaluation_worker.py`、`test_evaluation_review.py`、`test_learner_evaluation_policy.py`、`test_evidence_journal.py`、`test_question_audit.py`。

## Related ADRs

- ADR-0002 运行数据单根（journal、兼容缓存均在统一数据根下）。
- ADR-0003 BM25 基线 + 向量可选（quiz grounding 复用混合检索，不引入第二套置信度标尺）。
