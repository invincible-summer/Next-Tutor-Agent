# Evaluation（M7 教学系统评估智能）

M7 回答「这个教师 Agent 自己是否越来越好」：纯观察者地捕获每轮 TurnTrace、规则诊断失败发生地、跨轮聚合策略有效性，并周期性产出开放式教学指导提案——批准与应用永远经人工确认（Improvement Advisor，不是自动自我修改的 Optimizer）。

## Owns

- `manager.py` — `EvaluationService`：`evaluate_turn` 写钩子、`maybe_advise`（advisor 频率门）、`report()` 聚合、`traces` / `proposals` / `approve_proposal` / `reject_proposal`。
- `schema.py` — `TurnTrace`、`FailureType` 七类、`StrategyEffectiveness`、`ImprovementProposal`、`ADVISOR_FREQUENCY_GATE` 常量。
- `store.py` — `students/<id>.eval_traces.jsonl` / `.evaluation.json` 读写。
- `trace_analyzer.py` — `diagnose` 优先级瀑布诊断 + `apply_diagnosis`（承重件，零 LLM）。
- `strategy_analyzer.py` — 跨轮 (mode, subject) 成功率聚合：`analyze_traces` / `refresh_effectiveness` / `best_strategies`。
- `advisor.py` — 唯一 LLM 件：提案生成与解析（解析失败静默丢弃）。
- `context_builder.py` — `build_evaluation_directive` 渲染 `[评估智能·…]` 软指令（读侧 3f）。
- `window.py` — `teaching_window_report` 时间窗严格只读投影。

## Does not own

- 学生测评归 `agents/assessment/`（M4）与 M2 统一学习评价域——注意 `test_evaluation_jobs.py` 等 evaluation 前缀测试属 M2 学习评价域，不是本模块。
- per-student 策略成功率归 `agents/memory/procedural.py`（M6）；本包只做系统级跨轮聚合，不复制原始数据。
- 单轮表达质量评估归 ux 域 `ResponseQualityEvaluator`（M8）。
- 教学指导输入态文件 `teaching_guidance.json` 归 M3 teaching engine；本包仅经 API 层在提案 `applied` 时写入（分析器代码从不 import M3）。

## Design

- PURE-OBSERVER：只读 M2/M3/M6 投影，绝不反向写回；只拥有自己的 traces / 聚合 / proposals。
- DETERMINISTIC-FIRST：trace 捕获、失败诊断、策略聚合均为纯函数零 LLM；advisor 是唯一 LLM 件且频率门控，不在每轮关键路径。
- HUMAN-IN-THE-LOOP：提案（title / applicability / guidance / cautions / confidence）永不自动应用；`applied` 即部署进 M3 guidance store，吊销即回滚（条目置 inactive 保留审计）。
- 无增益字段纪律：旧 before/after 掌握度增量已删除，TurnTrace 与提案不含学生能力数值；`test_evaluation_strategy_analyzer.py` 以 `BANNED_KEYS` 钉死这些字段不回流。
- 权威事实源见 [docs/architecture/evaluation.md](../../../../../docs/architecture/evaluation.md)。

## Tests

`services/api/tests/`：

- `test_evaluation_strategy_analyzer.py` — 策略聚合与 `BANNED_KEYS` 无增益字段断言。
- `test_teaching_guidance.py` — 提案 applied 部署、guidance 消费与吊销回滚全链。
- `test_supervisor_hooks.py` — 6e 写钩子不静默失败。

## Key entry points

- `EvaluationService.evaluate_turn`（6e 写钩子）/ `maybe_advise` / `report` — 写侧与聚合读侧。
- `trace_analyzer.diagnose` / `strategy_analyzer.analyze_traces` — 确定性诊断与聚合。
- `context_builder.build_evaluation_directive` — 每轮软指令（3f）。
- `window.teaching_window_report` — 时间窗报告（site assistant / 洞察页消费）。
