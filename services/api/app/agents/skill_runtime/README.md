# skill_runtime — M10 Skill 运行时（能力控制层）

把分散的工具/教学能力收敛为有版本、前置条件与成功标准的 Agent Skill：每轮构建 TaskFrame 做决策，gated 模式下对计划执行工具门（gate_plan）与结果后置条件校验，不替代 M1-M9。

## Owns

- `manifest.py` — `SkillManifest` / `SkillKind`（atomic/composite/advisory/validator）；id 必须 `agent.skill.` 前缀，Atomic Skill 必须绑定工具
- `registry.py` — Skill 单一真相源（`get` / `all_active` / `for_role` / `for_intent` / `by_tool_name` / `capability_tool_map`），内置 5 项 Skill
- `decision.py` — `TaskFrame` 构建与规则评分决策（mode ∈ direct/execute/clarify）
- `policy.py` — 确定性前置条件检查（materials/reference_question/history/grade/concept_available）
- `runtime.py` — `SkillRuntime`：Skill↔Tool 绑定、`gate_plan` 计划收窄、`validate_result` 后置条件验证（`PostconditionReport`）
- 动态 Skill Card 注入的语义：只把当前 TaskPlan 引用的 Skill 卡片交给上下文（经 `prompts/tutor.py::skill_cards_preamble`）

## Does not own

- 学习证据的记录、判分与分级——统一学习评价在 M2 `agents/student_model/evaluation/` 与 assessment 域执行；旧 E0-E5 证据门已删除，本层只有计划级工具门与后置条件校验，不存在第二套证据分级
- 工具实现本体（`app/tools/`）与 `ToolResult` 协议、Tool 基类（`core/tool_protocol.py` / `core/tool_base.py`）
- 宿主管线：supervisor/planner/executor/router 中的集成点属聊天内核文件，本层不改变 SSE 契约与前端
- 出题质量链（assessment 域）、检索与索引（RAG/知识域）、transcript 存储（上下文工程域）

## Design

Skill 命名空间与绑定表、决策信号、门控流程、shadow/gated/off 模式与不变量见
[docs/architecture/skill-runtime.md](../../../../../docs/architecture/skill-runtime.md)。

## Tests

- `services/api/tests/test_prompt_registry.py` — Skill Card 注册与版本钉扎
- `test_context_budget.py`（Skill 注入的上下文预算）、`test_quiz_card_contract.py`（`skill_ids` 链路）
- `test_deepseek_tool_call_compat.py`（工具调用解析兼容）、`test_pseudo_tool_guard.py`（伪工具护栏）
- 相邻回归：`test_knowledge_read.py`、`test_recall_history.py`、`test_material_trigger_signals.py`、`test_task_understanding_context.py`
- 聚焦运行：`cd services/api && python -m tests tests.core.test_prompt_registry`

## Key entry points

- `registry.py::registry` — 能力唯一真相源（`router.CAPABILITIES` 由 `capability_tool_map()` 生成）
- `decision.py::build_task_frame` / `decide` / `gate_plan` — 每轮决策与计划门控
- `runtime.py::SkillRuntime.validate_result` — 工具结果后置条件验证
