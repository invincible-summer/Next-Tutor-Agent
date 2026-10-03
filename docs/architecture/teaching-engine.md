# M3 自适应教学引擎（Teaching Engine）

回答「这个学生现在该怎么教」：以只读 TeachingContext 为输入，经确定性规则产出教学模式、深度与 focus/avoid 软指令，并用跨轮教学日志支撑 INTRODUCTION→EXPLANATION→PRACTICE→CHALLENGE 的推进。

## Purpose / Scope

- 教学模式状态机与 per-mode 教学配方（六模式）、错误诊断、动态难度、学习路径建议、课堂小结锚点。
- 学段细则单一事实源（`stage_profile.py`：四学段×七维度），供 preamble、出题、批改等全链注入。
- LLM 教学决策适配（`decision_adapter.py`）与已应用教学指导（`guidance_store.py`，M7 提案的部署落点）。
- 不负责：学生知道什么（[student-model.md](student-model.md)）、表达方式适配（[ux.md](ux.md)）、跨会话记忆（[memory.md](memory.md)）。

## Owned code

`services/api/app/agents/teaching_engine/`（13 个 Python 文件）：

- `__init__.py`——设计契约（PURE-READ / RULE-BASED / GRACEFUL / CROSS-TURN MEMORY）与公共导出。
- `manager.py`——`TeachingManager`（`adapt` / `record_turn`）、`adapt_from_context`、`previous_mode_for`、`is_enabled`。
- `state.py`——`TeachingContext`（只读教学上下文投影）、`TeachingOutcome`。
- `strategy.py`——`TeachingMode` 六模式枚举与 `select_strategy` 状态机。
- `policy.py`——`compose`：模式+上下文+指导 → `TeachingStrategy`（focus/avoid/next_check/plan_hints/rationale）。
- `difficulty.py`——1-5 内部难度模型（最近作答准确率升降档、钳位、easy/medium/hard 映射）。
- `misconception.py`——四分类错误诊断（概念/步骤/计算/推理）与纠错配方。
- `curriculum.py`——学习路径（`build_learning_path`：前置已满足+按难度排序、复习建议）。
- `stage_profile.py`——`normalize_grade` / `is_auto` / `stage_profile` / `difficulty_anchor` / `example_style` 等学段细则。
- `session_summary.py`——确定性课堂小结与恢复锚点（润色经 `session_summary@1.0.0` prompt，仅在 STRUCTURED_ASSESSMENT_MODE=active 且本轮有作答判定时）。
- `decision_adapter.py`——`TeachingDecision` 有界 LLM 决策（触发门 `should_decide`、`validate_decision` 白名单校验、`apply_decision` 受限落策略）。
- `teaching_log.py`——`students/<id>.teaching.json` 读写（`record_turn_outcome` / `recent_for_concept` / `last_turn`，file_lock + 原子写）。
- `guidance_store.py`——`students/<id>.teaching_guidance.json`（`apply_guidance` / `revoke_guidance` / `load_active`）。
- 集成侧：`app/api/v1/student.py`（`GET /student/teaching-log`、`/student/learning-path`）、`app/api/v1/evaluation.py`（提案 applied 时写 guidance_store）、`app/api/v1/knowledge.py` / `textbook.py` / `chat.py`（stage_profile 注入）。

## Public contracts

- `TeachingStrategy`（policy.py）：`mode`、`explanation_depth`、`focus`/`avoid`（supervisor 只渲染 focus[:3]/avoid[:3]）、`suggested_quiz_difficulty`、`next_check`、`plan_hints`、`assistance`（full_demo|key_hints|independent）、`target_skill_id`、`decision_id`、`rationale`。
- `compose(ctx, mode, recent_outcomes, guidance)` 是策略组装唯一入口：规则模式选择 + 深度下限 + 指导折叠 + rationale 生成。
- `stage_profile` 系函数是学段细则单一事实源：grade_preamble 整块注入讲解上下文（tutor_system@2.6.0 只留注入约定），难度锚点/例题风格/典型错因行注入出题、拟合与批改 prompt。
- API（前缀 `/api/v1`）：`GET /student/teaching-log`、`GET /student/learning-path`（只读投影）。

## State & storage

| 路径 | 内容 |
|------|------|
| `students/<id>.teaching.json` | 跨轮教学日志：每概念 (mode, outcome) 历史（承重件，支撑跨轮推进） |
| `students/<id>.teaching_guidance.json` | 已应用教学指导（M7 提案经 API 部署写入；active 标记可吊销；M3 拥有的输入态文件） |

- 两个文件均 file_lock + 原子写；teaching_log 按 concept key（图谱归一 key）存列表。
- teaching_log 读写统一用 `TeachingContext.concept_key` / `strategy.target_skill_id` 归一 key，消除写读不一致导致的拨盘空转。

## Main flows

1. **读钩子（3b，`supervisor._adapt_for_turn`）**：`TEACHING_ENGINE_MODE=1` 时组装 TeachingContext（学段、概念、意图、`previous_mode_for` 的跨轮状态），`TeachingManager.adapt` 内部：`select_strategy` 选模式 → 动态难度（`difficulty.compute_difficulty`：G4 中性起点恒 2，最近 5 条 assessed outcomes 准确率 ≥80% 升一档 / ≤40% 降一档，钳位 1-5；高中/本科新概念无作答证据时地板 ≥3（medium），CHALLENGE 无证据时 ≥4，一旦有 assessed outcomes 拨盘全权）→ `compose` 折入 guidance → 渲染 `[学生智能·…]` 软指令。`TEACHING_ENGINE_MODE=0` 时退回 supervisor 的轻量降级策略（`适配降级` rationale），渲染路径不变。
2. **LLM 教学决策**：`TEACHING_DECISION_MODE` 默认 `rules` 零调用；触发门（评估完成/学生新约束/连续困惑）命中时 `decision_adapter` 做一次有界 LLM 调用，`validate_decision` 过枚举白名单（action/target/assistance、target 限候选概念集、`allow_followup_assessment=false`、单一主要行动）后以受限调整落到 TeachingStrategy（mode/assistance/plan_hints/decision_id）；`shadow` 只记 trace 对照；任何失败保持规则策略。
3. **写钩子（6c）**：回合结束后 supervisor 调 `TeachingManager.record_turn` 持久化 (concept, mode, outcome)；outcome 尽力读取本轮 quiz 工具结果中的 verdict（对/错/部分），否则 ENGAGED（已讲未测）；chitchat 不记录。
4. **指导消费**：每轮 `adapt` 读取 `load_active` 生效条目，确定性适用范围过滤（当前学科/概念出现在 applicability 文本或为空=通用），每轮至多最新 2 条、每行 110 字封顶折入 focus/avoid，rationale 注明「已应用教学指导（提案 #id）」。
5. **错误诊断（misconception）**：`misconception.diagnose` 按规则把批改反馈 note 分入四类（概念/步骤/计算/推理）；`compose` 经 `correction_focus_avoid` 把每类错因的针对性纠错配方叠加到模式配方之上（每类贡献一对 focus/avoid + 纠正路径，如 REMEDIATION「先定位错在哪一步、重建直觉模型、不只给答案」），`strat.misconceptions` 保留最近 2 条供下轮针对根因。
6. **学习路径（3c，`_plan_learning_path`）**：intent=plan 时从 M5 图谱算「下一步学什么」（前置已满足、按难度排序）+「该复习什么」（掌握中等且久未触碰），注入学习路径建议。
7. **课堂小结（6c-bis，`session_summary.py`）**：每轮结束确定性重建 `session.learning_summary`（只引用 quiz_history 已接受判定；无测评→「已讲解，待验证」；含待答计数/开放问题/恢复锚点）；active 且本轮有作答判定时以骨架为唯一输入经 prompt 润色两行。学生离开 ≥30 分钟后回来，supervisor 在 preamble 注入「[上次学习小结]…恢复点：…」（短间隔连续提问不打断）。
8. **情绪弱信号**：学生最新反馈被 M8 规则分类为「太难了/看不懂」时，supervisor 合成层把收尾检测建议难度降一档（hard→medium→easy 触底不降）——是对 strategy 的输入叠加，不改 M3/M8 边界。

## Dependencies

- 输入（只读）：M2 画像与统一评价投影（经 TeachingContext 数据字段注入，运行时零 `student_model` import——Import-clean 不变量）；M5 图谱（学习路径、概念归一 key）；quiz 工具结果 verdict（6c outcome 信号）。
- 上游调用方：M1 supervisor（3b/3c/6c/6c-bis 钩子）、M4 出题与批改 prompt（stage_profile 注入）、前端 preamble（学段块）。
- 下游写入方：M7 评估（API 层写 guidance_store，是 M7 影响教学的唯一路径；M7 分析器代码从不 import M3）。
- 基础设施：`core/atomic.py`（file_lock/原子写）、prompts registry（`session_summary@1.0.0`、tutor_system 注入约定）。

## Invariants / security boundaries

- **Import-clean**：运行时零 `student_model` import；一切学生状态经只读 TeachingContext（纯 str/float/list 字段）注入，零循环依赖面。
- **纯规则确定性**：模式选择、难度、诊断、路径全为纯函数零 LLM（刻意不做 LLM 教学规划器——会让策略不稳定）；唯一 LLM 件是 decision_adapter 的有界调用与 session_summary 的条件性润色，均失败即回退规则。
- **graceful 降级**：任何失败降级为 no-op 策略；`TEACHING_ENGINE_MODE=0` 时上层行为与关闭前路径一致；读写钩子全部包 try/except，失败只记 trace 不影响对话流（统一护栏原则）。
- 指导只走 supervisor 已渲染字段（focus[:3]/avoid[:3]），对话链路零改动；吊销（`DELETE /evaluation/guidance/{id}` 置 active=false）后 compose 立即停止消费。
- 讲解深度下限（tutor_system@2.8.0）：默认讲解必须成篇讲透，禁止只给概要/提纲收尾；高中/本科 INTRODUCTION 的 depth 不落 basic。
- 归属：所有文件按 student_id 隔离，`resolve_student_id()` 是唯一可信标识。

## Configuration

| 环境变量 | 默认 | 语义 |
|------|------|------|
| `TEACHING_ENGINE_MODE` | `1` | `0` 退回 supervisor 内置轻量 adapt 路径（渲染标记不变） |
| `TEACHING_DECISION_MODE` | `rules` | `rules` 纯规则；`shadow` 触发门命中时旁路调用并记 trace 对照；`active` 校验通过的教学决策以受限方式调整策略 |
| `STRUCTURED_ASSESSMENT_MODE` | `off` | `active` 时课堂小结启用 LLM 润色（仅本轮有作答判定） |

## Observability

- trace 事件：`supervisor_adaptation`（mode/depth/quiz_difficulty/rationale/decision）、`teaching_engine_log`、`teaching_engine_log_error`、决策 shadow 对照记录。
- `GET /student/teaching-log` 暴露跨轮日志投影；M7 `GET /evaluation/guidance` 暴露已应用指导与 `impact_turns` 回显。

## Tests / acceptance

`services/api/tests/`：

- `test_teaching_engine.py`——模式状态机、难度拨盘、策略组装、import-clean 边界。
- `test_teaching_decision.py`——触发门、validate_decision 白名单、shadow/active 行为、失败回退。
- `test_teaching_guidance.py`——guidance_store 部署/消费/吊销回滚（与 [evaluation.md](evaluation.md) 部署路径共测）。
- `test_session_summary.py`——小结骨架、恢复锚点、润色门。
- `test_projection_api.py`——`/student/teaching-log`、`/student/learning-path` API 面。

## Related ADRs

无（教学策略本身为确定性规则域；存储与持久化纪律遵循 ADR-0002 / ADR-0004，见 [student-model.md](student-model.md)）。
