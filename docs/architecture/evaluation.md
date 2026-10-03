# M7 评估改进智能（Evaluation & Improvement）

回答「这个教师 Agent 自己是否越来越好」：纯观察者地捕获每轮 TurnTrace、规则诊断失败发生地、跨轮聚合策略有效性，并周期性产出开放式教学指导提案——批准与应用永远经人工确认（Improvement Advisor，不是自动自我修改的 Optimizer）。

## Purpose / Scope

- 四层管线：observe → diagnose → propose → approve/deploy；M7 只做前三步，approve（`PATCH /evaluation/proposals/{id}`）与 deploy（applied 即部署）是人工确认门。
- 纯观察者（PURE-OBSERVER）：只读 M2/M3/M6 投影，绝不反向写回；只拥有自己的评估产物（traces/聚合/proposals）。
- 教学效果观测：以每轮 outcome（correct/wrong/engaged/…）与工具/成本（tokens/steps/duration）为主的教学系统质量事实；旧 before/after 掌握度增量已随统一评价删除（学生能力观测只在 [student-model.md](student-model.md) 学习证据域）。
- 不负责：per-student 策略成功率（[memory.md](memory.md) procedural）、表达质量单轮评估（[ux.md](ux.md) ResponseQualityEvaluator）。

## Owned code

`services/api/app/agents/evaluation/`（9 个 Python 文件）：

- `__init__.py`——设计契约（PURE-OBSERVER / SINGLE TRUTH SOURCE / GRACEFUL / DETERMINISTIC-FIRST / HUMAN-IN-THE-LOOP）。
- `schema.py`——`TurnTrace`、`FailureType` 七类、`StrategyEffectiveness`、`ImprovementProposal`、`ADVISOR_FREQUENCY_GATE = 15`、`EVAL_DIRECTIVE_WINDOW`。
- `store.py`——`students/<id>.eval_traces.jsonl` / `.evaluation.json` 读写。
- `manager.py`——`EvaluationService`：trace 记录、`maybe_advise`（advisor 频率门）、`report()` 聚合。
- `trace_analyzer.py`——`diagnose` 优先级瀑布诊断 + `apply_diagnosis` + `recurring_failure_pattern`（承重件，零 LLM）。
- `strategy_analyzer.py`——跨轮 (mode,subject) 成功率聚合：`analyze_traces` / `refresh_effectiveness` / `best_strategies` / `summarize`。
- `advisor.py`——唯一 LLM 件：`should_advise` 频率门、`_build_advice_prompt`、`_parse_proposal`（解析失败静默丢弃）。
- `context_builder.py`——`build_evaluation_directive` 渲染 `[评估智能·…]` 软指令（读侧 3f）。
- `window.py`——`teaching_window_report`：trace 的半开区间严格只读投影（区分 missing/partial/complete，不新增评分口径）。
- 集成侧：`app/api/v1/evaluation.py`（全部 API，含 applied 部署写 `teaching_engine/guidance_store`）；`app/agents/site_assistant/readers.py` 消费 `window.py`；`app/core/context_telemetry.py` 聚合结果投影到 `GET /evaluation/context-budget`。

## Public contracts

- API（前缀 `/api/v1`，`app/api/v1/evaluation.py`）：
  - `GET /evaluation/{report,traces,proposals,guidance}`——聚合报告、trace 黑盒、提案列表、已应用指导（含 `impact_turns` 回显）。
  - `GET /evaluation/context-budget`——上下文预算遥测投影（统计无 Prompt/正文/隐藏 reasoning）。
  - `PATCH /evaluation/proposals/{id}`——人工确认门，合法 status 为 `approved | rejected | applied`；`applied` 即部署：提案指导文本写入 M3 guidance_store，legacy target 型提案（prompt/policy/strategy 白名单）仅存于历史数据，向后兼容读写、applied 无 guidance 文本时不部署任何内容。
  - `DELETE /evaluation/guidance/{id}`——吊销即回滚（条目置 active=false 保留审计）。
- `ImprovementProposal`：开放式教学指导——`title` / `applicability`（适用范围文本，空=通用）/ `guidance`（指导原则文本）/ `cautions` / `confidence`；无值域、无参数赋值，不自动应用。
- supervisor 钩子契约：读钩子 `_evaluation_directive_for_turn`（3f）返回 `[评估智能·…]` 历史失败模式提醒（无可行动内容返回空串）；写钩子 `_evaluation_record_turn`（6e）捕获 TurnTrace + 规则诊断 + 累计 advisor 门计数。

## State & storage

| 路径 | 内容 |
|------|------|
| `students/<id>.eval_traces.jsonl` | M7 评估黑盒（append-only、不封顶，同 transcript 定位） |
| `students/<id>.evaluation.json` | 聚合 + proposals 工作集（含 advisor 频率门计数） |
| `students/<id>.teaching_guidance.json` | M3 拥有的指导输入态文件（M7 提案部署写入，active 标记可吊销；归属见 [teaching-engine.md](teaching-engine.md)） |

- `TurnTrace` 是原子分析单元：concept/subject/intent/grade/mode/outcome/tool_calls/steps/tokens/duration——无任何学生能力数值。
- applied 提案带 `impact_turns` = applied_ts 之后的 eval_traces 条数（「已影响最近 N 轮」）；历史数据无 applied_ts 时为 null（显示「影响未知」）。

## Main flows

1. **观察（6e 写钩子）**：回合结束 `_evaluation_record_turn` 从本轮 understanding/strategy/工具结果组装 TurnTrace 落 `eval_traces.jsonl`，`apply_diagnosis` 做规则诊断，频率门计数 +1；任何失败只记 trace。
2. **诊断**：`trace_analyzer.diagnose` 按优先级瀑布定位失败**发生地**，七类 FailureType：`TEACHING_DEPTH_MISMATCH / PREREQUISITE_MISSING / RETRIEVAL_MISS / ASSESSMENT_TOO_HARD / STRATEGY_MISMATCH / NO_ASSESSMENT / NONE`（零 LLM）。
3. **建议**：`strategy_analyzer` 跨轮聚合 (mode,subject) 成功率排序表（系统级——衡量教学系统质量而非学生能力）；`advisor` 每 `ADVISOR_FREQUENCY_GATE=15` 条 trace 触发一次、每次至多 1 条提案，LLM 输出经 `_parse_proposal` 校验，失败静默丢弃，落盘 status=proposed。
4. **部署（人工门）**：人工 `PATCH` 为 `applied` → API 层把 guidance 文本 `apply_guidance` 写入 `students/<id>.teaching_guidance.json`——这是 M7 影响教学的**唯一路径**，也是唯一被允许的跨模块写点（M7 分析器代码从不 import M3）。此后每轮 M3 `compose` 折入 focus/avoid（确定性适用范围过滤、每轮至多 2 条）。
5. **吊销/回滚**：`DELETE /evaluation/guidance/{id}` 置 active=false，M3 compose 立即停止消费，教学行为恢复原状；重新应用同一提案幂等（`applied_at` 锚定首次应用时间，影响统计不重置）。
6. **读侧（3f）**：`context_builder` 读最近 traces + 失败模式 + 聚合，渲染 `[评估智能·…]` 软指令（advisory，不强制 LLM）；`EVALUATION_INTELLIGENCE_MODE=0` 时返回空、6e 捕获 no-op。
7. **窗口报告**：`window.teaching_window_report` 提供时间窗内教学效果的严格只读投影（供 site assistant 与洞察页），不产生第二套评分。

## Dependencies

- 只读上游：M1 supervisor（轮次事实）、M3 教学模式与 outcome、M6 procedural（per-student 对照面，不复制原始数据——M7 只产出聚合层）、M4 verdict（单轮表达评估属 M8，跨轮教学系统质量属 M7）。
- 唯一写下游：`teaching_engine/guidance_store`（经 API 层，非分析器代码）。
- 消费方：`GET /evaluation/*` 前端系统洞察页、site assistant（`window.py`）、`core/context_telemetry.py`。
- 基础设施：`core/atomic.py` 原子写、LLM 客户端（仅 advisor 使用）。

## Invariants / security boundaries

- PURE-OBSERVER：读 M2/M3/M6 均为 plain projection，依赖方向单向（evaluation → 观察对象），零反向写。
- 与 M6 边界：M6 procedural 记「策略 X 对该生 success_rate=Y」（per-student）；M7 strategy_analyzer 记「跨所有轮次哪种模式成功率最高」（系统级聚合）。
- DETERMINISTIC-FIRST：trace 捕获、失败诊断、策略聚合均为纯函数零 LLM；advisor 是唯一 LLM 件且频率门控、不在每轮关键路径。
- HUMAN-IN-THE-LOOP：提案永不自动应用；status/target 白名单校验（坏 LLM 无法静默改写 prompt 或策略）。
- TurnTrace 与提案不含学生能力数值；吊销即回滚；所有钩子包 try/except，失败只记 trace 不影响对话流（统一护栏原则）。
- A/B 实验层已删除（`experiment.py` 零调用死代码已清理）；按 student_id 隔离。

## Configuration

| 环境变量 | 默认 | 语义 |
|------|------|------|
| `EVALUATION_INTELLIGENCE_MODE` | `1` | `0` 关闭 M7：评估指令返回空、6e 捕获 no-op（M1-M6 行为逐字节不变） |

- advisor 频率门 `ADVISOR_FREQUENCY_GATE = 15` 为 `schema.py` 常量（非环境变量）。

## Observability

- trace 事件：`evaluation_hook_error`、`evaluation_advise`（target/confidence）、`evaluation_advise_error`。
- `GET /evaluation/traces` 直接暴露黑盒；`report`/`guidance` 聚合可审计；`context-budget` 只返回统计（不返回 Prompt、用户正文、工具原文或隐藏 reasoning）。
- `window.teaching_window_report` 的 coverage 元数据（`exists`/`readable`/`invalid_count`/`truncated`，20000 行严格读取预算）向消费方明示窗口结论的完整性，缺文件与坏行不再被吞成「complete 空集合」。

## Tests / acceptance

`services/api/tests/`：

- `test_evaluation_strategy_analyzer.py`——M7 策略聚合在去除数值增益后仍可用（`analyze_traces` / `summarize`）。
- `test_teaching_guidance.py`——提案 applied 部署、guidance 消费与吊销回滚全链（与 [teaching-engine.md](teaching-engine.md) 共测）。
- `test_supervisor_hooks.py`——6e 写钩子不静默失败。
- 注意区分：`test_evaluation_jobs.py` / `test_evaluation_lifecycle.py` / `test_evaluation_worker.py` / `test_evaluation_review.py` / `test_evaluation_context.py` / `test_evaluation_routes.py` 属 M2 学习评价域（[student-model.md](student-model.md)），非本模块。

## Related ADRs

- ADR-0004 JSON 持久层 single-worker
- ADR-0002 运行数据统一 NEXT_TUTOR_DATA_DIR
