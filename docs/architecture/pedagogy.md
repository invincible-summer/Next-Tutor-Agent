# Pedagogy（教学法原则与工程落点）

> 一句话职责：定义系统采用的教学法理论——ECDL（证据中心学习设计）总架构、CLT（认知负荷理论）讲解支持层、RBT（修订版布鲁姆分类学）认知目标层——及其在教学引擎、测评与学习证据域中的代码落点。

## Purpose / Scope

- 本文档是**现行规范**：只记录系统当前实际采用的教学法原则、它们暴露的参数与策略、以及每条原则在代码中的落点。
- 三个理论的职责边界互补、不合成总分：**ECDL** 回答「凭什么从一次教学互动推断学生发生了学习变化、下一步为什么这样教」；**CLT** 回答「这次讲解应怎样组织、给多少支持」；**RBT** 回答「这道题究竟要求学生做哪一种认知加工」。不引入第四套核心理论，也不计算任何「教育学总分」。
- 教学法不拥有独立运行模块：原则全部落在 M2（学习证据域）/ M3（教学引擎）/ M4（测评）/ M10（证据纪律）的既有接口上，并以 `core/bloom.py` 为唯一认知过程词汇源（canonical vocabulary，不建第二份枚举）。
- 核心闭环：**学习目标 → 知识理解 → 练习训练 → 能力评估 → 调整**；教学法使该闭环成为可追踪的证据论证链（Claim → Task → Evidence → Inference → Action）。

## Owned code

教学法原则的代码落点（本模块不新增并行实现，只复用以下位置）：

- `services/api/app/core/bloom.py`：RBT 认知过程六级的唯一词汇源（含中文标签与别名映射）。
- `services/api/app/core/quiz_design.py`：两轮命题蓝图（ECDL Task Model 设计轮）。
- `services/api/app/core/quiz_verify.py` + `core/quiz_grounding.py`：出题质量门、逐题证据审核（QuestionAudit）、教材 grounding。
- `services/api/app/agents/teaching_engine/`：`policy.py`（TeachingStrategy 规则路径）、`strategy.py`（六模式确定性状态机）、`decision_adapter.py`（受限 LLM 教学决策 + `ASSISTANCE_LEVELS`）、`guidance_store.py`（人工批准的教学指导）。
- `services/api/app/agents/student_model/evaluation/`：`schema.py`（TaskSnapshot/TaskBlueprint/QuestionAudit/TeachingDesignReview/EvidenceCondition 等全部教学法规格）、`worker.py`（含 CLT 复盘 job）、`evaluator.py`、`llm.py`、`context.py`。
- `services/api/app/prompts/learner_evaluation.py` + `prompts/registry.py`：`learning_evidence_contract@1.0.0`（共享合同）、`quiz_blueprint@2.2.0`、`question_evidence_audit`、`teaching_clt_review@1.0.0`、`tutor_system@2.9.0`（讲解结构）。

## 对外暴露的教学法参数 / 策略

**认知过程（RBT，`core/bloom.py`）**：`remember / understand / apply / analyze / evaluate / create`，配中文标签（记忆/理解/应用/分析/评价/创造）与中文别名归一（如「辨析→analyze」「评判→evaluate」）。`bloom_level` 作为作答标签（≤24 字符）随判定落学习证据账本；布鲁姆**不是**题目难度、不是能力门槛——不存在「答对 N 道才升层」的机械阶梯，允许跳层/混层/回访。

**知识维度（RBT 第二维，`KnowledgeType`）**：`factual / conceptual / procedural / metacognitive`，随 TaskSnapshot 与逐题审核持久化。

**任务模型（ECDL，`TaskSnapshot` 冻结件）**：每道题在获得稳定题号时以 `question_revision + rubric_hash` 冻结——`target_claims`（≤8，本题主张知道学生什么）、`intended_processes`（RBT 目标）、`knowledge_types`、`task_family`、`novelty`（`BlueprintNovelty`：`same_form / changed_context / changed_representation / new_structure / indeterminate`——迁移证据的形态判别）、`assistance_plan`、`evidence_opportunities`（`{id, required_product, permitted_claim, limits}`——题目哪部分迫使学生展示目标认知）、`rubric_draft`、`grounding_refs`、`construction_brief`。改量规必须新 revision。

**逐题审核（`QuestionAudit` / `TaskVerification`）**：`answer_check ∈ valid|invalid|indeterminate`、`grounding_check ∈ supported|unsupported|indeterminate|not_required`、`actual_required_processes`（按最低可行解法判定的实际认知要求）、`alignment ∈ aligned|weaker_than_target|different_construct|indeterminate`、`opportunity_checks`（每个证据机会是否可观察）、`rubric_issues`、`proposed_status ∈ passed|revision_required|rejected|unreviewed`（每题恰好一次审核，遗漏即 unreviewed）。

**教学策略（M3，`TeachingStrategy`）**：`mode`（六模式）、`depth ∈ basic|deep|adaptive`、`exercise_level ∈ easy|medium|hard`、`focus/avoid`、`assistance`、`plan_hints`、`decision_id`（审计回链）。

**帮助阶梯（CLT guidance fading，`ASSISTANCE_LEVELS`）**：`full_demo → key_hints → independent`，语义分别是「先完整示范再模仿 / 只给关键步骤提示 / 独立完成」。它是支持撤除通道而非机械阶梯：新手、高交互复杂度任务、连续失败时允许提高支持；证据与任务复杂度共同决定升降。

**教学决策（`TeachingDecisionOutput`）**：`action ∈ explain|practice|quiz|review|clarify|summarize`、`assistance`（默认 `key_hints`）、`rationale`、`evaluation_refs`、`expected_observation`、`stop_condition`、`presentation_hints`——任何改变教学路径的决策必须声明希望下一步观察什么、什么证据出现后停止当前策略（可证伪的教学假设）。

**教学设计复盘（CLT，`TeachingDesignReview`）**：六维 `guidance_fit / element_interactivity_control / split_attention_risk / redundancy_risk / transience_segmentation / fading_readiness`，每维 verdict ∈ `pass / concern / not_observed / not_applicable`，带 `evidence_refs` 与 `reason`；至多一条 `priority_adjustment`（`description + expected_observation`）。只审设计风险，不输出认知负荷数值，不评价学生能力。

**证据条件（`EvidenceCondition`）**：`kind ∈ ordinary / transfer / retention / self_check`；时间间隔、提示时序等条件事实由服务端补充（`server_facts`），不接受模型或客户端自报。

## State & storage

- `students/<id>.learning_evidence.jsonl`：统一学习证据账本（单一真相源，无数值掌握度并行链路）。承载 TaskSnapshot 冻结件、作答与三级判定、`bloom_level` 标签、EvidenceCondition、SourceReceipt（真实时间、归属、帮助条件、答案指纹）与 ConceptJudgment（概念证据状态 `not_observed / emerging / supported_in_scope / fragile / conflicting`）。append-only，不因来源对话删除而删除。
- `students/<id>.teaching_log.json` 等教学日志：跨轮教学记忆（模式/概念/结局），是六模式状态机推进与 CLT 决策的证据输入。
- `students/<id>.eval_traces.jsonl` + `.teaching_guidance.json`：M7 教学质量观测与人工批准的教学指导（指导是 LLM 写的开放文本，compose 只做路由；吊销即回滚）。
- 量规 `rubric_criteria[{id,description,weight,critical}]` + `equivalent_solutions` 随题冻结（`rubric_hash`）；分数由服务端按冻结权重本地计算。

## Main flows

**讲解闭环（CLT）**：

1. 六模式确定性状态机（`strategy.py`，零 LLM）按教学上下文选模式：优先级为显式任务意图钉定 → 活跃误解或硬前置缺口强制 REMEDIATION → 掌握档位选择 INTRODUCTION/EXPLANATION/PRACTICE/CHALLENGE → 上轮干净答对可跨轮升档；跨轮推进（INTRODUCTION→EXPLANATION→PRACTICE→CHALLENGE）使导师能说「上次你方向判对了，今天我们套公式」而不是每轮重新介绍。
2. `policy.py` 规则路径产出 TeachingStrategy（focus/avoid/depth/examples_needed/学段调整）——纯规则、可审计、可降级，是故障时的权威 fallback。
3. `decision_adapter.py` 在触发门（评估完成/学生新约束/连续困惑）命中时做一次受限 LLM 决策：输出过 `validate_decision`（action/target/assistance 枚举白名单、target 限候选概念集、单一主要行动）才生效；`TEACHING_DECISION_MODE=rules|shadow|active`（默认 rules——零 LLM 零改动；shadow 只记 trace 对照；active 以受限调整落 TeachingStrategy）。任何失败保持规则策略。
4. `tutor_system@2.9.0` 定义默认讲解结构：知识定位 → 直观动机 → 定义 → 分步推导 → 例题 → 易错辨析 → 联系 → 小结；CLT 耦合方式为分段、worked example 优先（新手+高交互复杂度）、减少已知步骤解释、信息整合（标签与解释同位）；**学生显式格式/长度要求（一句话/简短/表格/分步骤）始终优先于默认教学结构**。
5. 讲解完成后的 CLT 复盘抽样（`worker.py::_run_clt_review`，JobKind.CLT_REVIEW）：以讲解片段为输入跑一次结构化调用产出 `TeachingDesignReview`；输入不足时弃权（不能用输出长度推算负荷）。结果只作为下轮教学建议经 outbox 事件投递给教学侧，**不写学生状态**、不新增学习观察。

**命题闭环（ECDL Task Model + RBT）**：

1. 两轮命题（`quiz_design.py`，`QUIZ_DESIGN_MODE=two_pass` 默认）：第一轮蓝图按 ECDL 任务设计产出每题 `target_claims / intended_processes / knowledge_types / evidence_opportunities / rubric_draft / task_family / novelty / construction_brief`（hard 必须落 analyze/evaluate/create，禁止纯记忆题充当；陷阱用于暴露典型误解，不是单纯加难）；蓝图渲染为 `[命题蓝图]` 块注入第二轮生成，生成必须逐题落实；蓝图失败自动回退单轮（fail-open）。
2. 质量门（`quiz_verify.py`，`QUIZ_VERIFY_MODE=critic|basic|off`）：确定性结构校验 + 独立重解 critic（`too_shallow` 判定丢弃明显降档题——easy 或未给目标难度不判，拿不准判 correct）+ 逐题证据审核（`question_evidence_audit`，受账户审阅开关控制）：`actual_required_processes` 按最低可行解法判定，`alignment` 不齐（声称测 Analyze 实际只需背公式）视为有效性不足；错题投递前被丢弃，错误答案不进入学习证据。审核结论以 `TaskVerification` 随题记录（未审核即 `unreviewed`）。
3. 教材 grounding（`quiz_grounding.py`）：蓝图/生成只能引用检索证据能支撑的概念、条件、公式；「教材应该讲过」不构成依据。检索命中只证明系统有材料（内容 provenance），绝不证明学生会（不是 learner evidence）。
4. 量规冻结：出题时学生未作答，天然满足「看答案前冻结」；判定只评 rubric 能观察到的东西，不因题目标签是 Analyze 就自动给 `reasoning=met`。

**证据闭环（ECDL Evidence Model + M10 纪律）**：

1. 所有作答经统一受理入口：完整答案指纹判重、MC 确定性判定、帮助事件在判分前入账（assistance floor——接受 full_demo 后的模仿成功不与独立成功等价计入）。
2. 三级判定 `[对]/[部分对]/[错]`；「部分对」是 REMEDIATION 的根因信号、CAT 难度步进的输入；`unknown`（未真正评分）一律不落盘。
3. 推断边界（`not_observed` 纪律）：讲解完成本身、学生自报「懂了」、复述定义不构成学习证据；即时答对不自动支持 retention（需时间间隔，`EvidenceCondition` + `server_facts`）；同模板换数字不自动支持 transfer（fit_quiz 五变式策略产真实变式并携带 `origin_question_ref` 同族关系，`novelty` 形态判别迁移）；无证据机会的能力维度输出 `not_observed` 而不是凭印象补全。
4. CAT（自适应测试）负责经验难度与题目选择（1–5 内部档、`stop_code` 诊断性枚举），与 Bloom 正交：Bloom 不决定 CAT 难度，CAT 不改写题目认知目标。
5. 概念证据状态（`supported_in_scope/emerging/fragile/conflicting/not_observed`）由账本派生：无观测即 not_observed，正负冲突即 conflicting；M3 前置补缺只提示 fragile/conflicting/emerging 概念的前置（未观察 ≠ 未掌握），M9 目标链差距分母据此计算（无观测概念 `unknown`——不宣称缺口）。

## Dependencies

- M5 知识/RAG：讲解与命题的事实来源（grounding）；边界为内容 provenance ≠ learner evidence。
- M6 记忆：跨对话画像注入教学决策；程序性记忆记策略成功率（per-student），与 M7 系统级聚合分工。
- M7 评估改进：Trace Analyzer 诊断教学失败发生地；人工批准的教学指导经 `guidance_store` 进入 compose（唯一被批准的跨模块写点）。
- M8 交互智能：只改表达不改内容；CLT 的分段/信息整合为 UX 提供约束，但 M8 不决定学习评价结论。
- M9 学习编排：把单轮证据闭环放大到多日计划；温故卡/SM-2 消费同一账本投影。
- 智能层正交开关：任一教学法相关层关闭或失败时自动降级（规则路径/二元评分/无蓝图直出），绝不中断对话流；每层读写钩子 try/except，失败只记 trace。

## Invariants / security boundaries

以下实现即使「看上去更教育学」也禁止进入系统：

1. 不把 ECDL/CLT/Bloom 加权成任何总分（三个 construct 不同，平均会掩盖关键失败）。
2. Bloom ≠ difficulty：不规定 hard 必须 Analyze、easy 必须 Remember（hard 的约束是「禁止纯记忆题充当」，不是层级映射）；difficulty 由 M3/M4 与 CAT 独立负责。
3. 不按字数判认知负荷；不为降负荷删必要推理链；不在无测量数据时输出认知负荷数值——CLT 复盘只有设计风险 verdict。
4. 一次正确 ≠ retention；换数字 ≠ transfer；正确 ≠ self_check（未观察检查/修订过程不得推断）。
5. RAG 命中 ≠ 学生掌握。
6. LLM 不得推翻 frozen rubric、本地算分、候选集、grounding 与评价纪律；理论审核输出只作建议。
7. 不建立机械 guidance ladder（不规定「做对两题必升 independent」）。
8. 学段（小学/初中/高中/本科）是表达与任务上下文，不是能力代理；不据 grade 直接推断能力状态。
9. 量规必须先于学生作答冻结（`rubric_hash`）；改量规必须新 `question_revision`。
10. 学生显式格式/长度要求优先于教学法默认结构。

## Configuration

| 开关 | 默认 | 语义 |
|---|---|---|
| `QUIZ_DESIGN_MODE` | `two_pass` | 蓝图两轮命题；失败 fail-open 回退 `single` |
| `QUIZ_VERIFY_MODE` | `critic` | `critic`（结构校验+独立重解+逐题审核）/ `basic` / `off` |
| `TEACHING_DECISION_MODE` | `rules` | `rules` 零 LLM；`shadow` 旁路记 trace 对照；`active` 受限调整 TeachingStrategy |
| `STRUCTURED_ASSESSMENT_MODE` | `off` | `off` 旧三级文本批改；`shadow` 旁路量规条目分析落盘对照；`active` 冻结量规开放题以结构化分析为权威判定（分数服务端按量规权重本地计算） |
| `ASSESSMENT_ENGINE_MODE` 等 | `1` | 各智能层正交开关，关闭即降级（M4 关闭时评分退回二元、无 CAT） |

## Observability

- 出题质量审计随工具结果进 SSE、quiz_history 与 Trace：`verification.design`（two_pass/single/fallback）、`verification.attempts/dropped/dropped_shallow`、critic 故障 fail-open 记入 `verification` 审计元数据。
- 教学决策：`decision_id` 审计字段回链；`rationale` 截断入 trace；shadow 模式保留规则/LLM 对照。
- CLT 复盘：job 终态 + outbox 事件（`consumer="teaching"`）投递六维 verdict 与 priority_adjustment，含 `limits` 弃权声明。
- 学习证据账本只读投影端点（`/learner-evaluation/*`）暴露概念证据状态、来源时间线与复核状态；`bloom` 分桶统计（未标层级的旧题不入桶）。
- M7 TurnTrace 捕获每轮教学事实（无学生能力数值）；`impact_turns` 回显已应用教学指导的影响轮数。

## Tests / acceptance

- 教学：`test_teaching_engine.py`（六模式状态机与规则策略）、`test_teaching_decision.py`（触发门、白名单校验、shadow/active 语义、失败回退）、`test_teaching_guidance.py`（人工批准/吊销回滚）。
- 命题：`test_quiz_design.py`（蓝图解析、fail-open、逐题落实）、`test_quiz_quality.py`（结构校验、critic 重解、too_shallow、Bloom 层级约束）、`test_quiz_grounding.py` / `test_quiz_grounding_provenance.py`（教材证据边界与溯源）。
- 证据域：`test_learner_schema.py`（TaskSnapshot 冻结、QuestionAudit/TeachingDesignReview/EvidenceCondition 规格）、`test_learner_evaluation_policy.py`、`test_learner_projection.py`、`test_learner_scope.py`、`test_evaluation_review.py`（复核与失效传播）、`test_evidence_journal.py`（受理/判重/幂等/损坏隔离）。
- 验收语义：无证据机会的维度输出 `not_observed`；受助与独立作答在账本中可区分；量规先于作答冻结；智能层任一关闭时对话流零中断。

## Related ADRs

- ADR-0001 source-only——教学法原则只落代码与 prompt，不依赖任何随仓库分发的教材数据。
- ADR-0002 运行数据单根——学习证据账本与教学日志位于统一运行数据根之下。
- ADR-0003 BM25 基线 + 向量可选增强——教学 grounding 消费的检索以确定性 BM25 为基线。
- ADR-0004 single-worker——账本受理/判重的并发不变量以单 worker 为前提。
- ADR-0005 Pages synthetic——演示站点不含真实学习证据。
