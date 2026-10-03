# Skill Runtime（M10 学习能力运行时与证据门）

M10 是横向能力控制层：把分散的工具/教学能力收敛为有版本、前置条件、成功标准和可解释决策的 Agent Skill，并对每轮计划执行工具门（gate_plan）与结果后置条件校验，而不替代 M1-M9。

## Purpose / Scope

- **Skill 资产化**：`SkillManifest` 声明 id/version/kind/role/tool_name/intents/preconditions/postconditions/use_when/avoid_when/fallback/side_effects/risk/cost，启动注册时确定性校验；Registry 是能力的单一真相源。
- **决策与门控**：每轮构建 `TaskFrame`，对 Registry 候选做规则评分 + 前置条件硬检查（`decision.py` / `policy.py`）；`gated` 模式在 Planner 之后执行 `gate_plan` 收窄计划，Executor 按 PlanStep 逐步暴露工具并验证后置条件（`runtime.py`）。
- **通用工具协议**：全部工具经统一的 `ToolResult` 协议返回（status/error code），使 Agent 行为可诊断；本层拥有协议与四个会话工具的注册面，工具自身的业务语义（出题质量链、检索、批改）分属 [assessment.md](assessment.md) 与知识检索域。
- **证据纪律边界**：旧 E0-E5 六级证据门已随统一学习评价删除；现行学习证据纪律（两类来源、冻结量规、帮助入账、unknown 不落盘）在 M2/M4 评价域统一执行（见 [assessment.md](assessment.md)）。M10 自身的门只有计划级工具门与后置条件校验，不存在第二套证据分级。

## Owned code

- `services/api/app/agents/skill_runtime/`
  - `manifest.py` — `SkillManifest` / `SkillKind`（atomic/composite/advisory/validator），id 必须以 `agent.skill.` 开头，Atomic Skill 必须绑定工具。
  - `registry.py` — Skill 单一真相源（`get` / `all_active` / `for_role` / `for_intent` / `by_tool_name` / `capability_tool_map`）；内置 5 项 Skill。
  - `decision.py` — `TaskFrame` 构建（intent/subject/concept/grade/资料/历史/参考题/工具需求/置信度）与规则评分决策。
  - `policy.py` — 确定性前置条件检查（materials/reference_question/history/grade/concept_available）。
  - `runtime.py` — `SkillRuntime`：Skill↔Tool 绑定与后置条件验证（`validate_result`、`PostconditionReport`）。
- `services/api/app/tools/` — 会话工具实现：`quiz.py`（`generate_quiz`）、`fit_quiz.py`（`fit_quiz`）、`knowledge_search.py`、`knowledge_read.py`、`recall_history.py`。
- `services/api/app/core/tool_protocol.py` — `ToolResult` / `ErrorCode` 统一协议；`core/tool_base.py` — Tool 基类。
- `services/api/app/agents/pseudo_tool_guard.py` — 伪工具标签护栏（弱模型把工具调用叙述成正文时截断转发）。
- 集成点（属聊天内核文件，本层语义）：`agents/supervisor.py`（build frame/decide/gate_plan/skill cards 注入）、`agents/planner.py`（`skill_ids` 解析）、`agents/executor.py`（gated 逐步工具暴露 + 后置条件 + `skill_plan_advance`）、`agents/router.py`（`CAPABILITIES = capability_tool_map()`，不再单独维护第二份工具表）、`prompts/tutor.py::skill_cards_preamble`。

## Public contracts

- **SkillManifest 字段**：`id` / `version` / `role ∈ {knowledge, teaching, assessment, memory}` / `kind ∈ {atomic, composite, advisory, validator}` / `display_name` / `description` / `intents` / `tool_name` / `preconditions` / `postconditions` / `use_when` / `avoid_when` / `fallback_skills` / `side_effects` / `risk_level` / `cost_level` / `prompt_ref` / `metadata`。启动注册即校验，不合法直接抛错拒绝启动。
- **命名空间**：运行时能力统一使用 `agent.skill.*`；知识图谱里「学生要掌握的 Skill」由 M2/M5 管理，二者不得混用。内置 5 项：
  - `agent.skill.teaching.direct_explain`（分层讲解）
  - `agent.skill.knowledge.search_materials`（资料证据检索）
  - `agent.skill.assessment.generate_practice`（结构化练习生成）
  - `agent.skill.assessment.fit_variants`（参考题变式生成）
  - `agent.skill.memory.recall_history`（历史学习证据回忆）

  Skill 与工具/角色的绑定关系：

  | Skill id | role | tool_name | kind |
  |---|---|---|---|
  | `agent.skill.teaching.direct_explain` | teaching | —（advisory 讲解） | advisory |
  | `agent.skill.knowledge.search_materials` | knowledge | `knowledge_search` | atomic |
  | `agent.skill.assessment.generate_practice` | assessment | `generate_quiz` | atomic |
  | `agent.skill.assessment.fit_variants` | assessment | `fit_quiz` | atomic |
  | `agent.skill.memory.recall_history` | memory | `recall_history` | atomic |

  旧 `knowledge/teaching/assessment/memory` 四角色保留为兼容投影（`for_role`）。
- **ToolResult 协议**：每个工具返回 `{tool, status: success|partial|error, data, text, error, error_code}`；`ErrorCode ∈ {NOT_FOUND, BAD_ARGS, NO_TOOL, TOOL_ERROR, VALIDATION_ERROR, CIRCUIT_OPEN, TIMEOUT, DUPLICATE_CALL}`——模型与前端据此分支，这是 Agent 可诊断性的基础。
- **TaskFrame**（frozen dataclass）：`intent/subject/concept/grade/has_materials/has_history/has_reference_question/asks_for_variant/references_materials/requires_tools/confidence/has_textbook/material_grounding_required`。
- **SkillDecision**：mode（direct/execute/clarify）、候选分数、选中 Skill、理由码、失败前置条件。
- **PlanStep.skill_ids**：计划步骤携带可执行 Skill ID；Router 优先 `skill_ids`，未知 ID 不进入可见工具集；`suggested_tools` 仅保留读取旧会话/旧计划；`tool_args/auto_invoke` 只作为确定性履约提示，不把所有计划工具强制化。
- **后置条件**（可确定性检查的子集）：资料结果必须包含命中数与 `<material_excerpt>`（且证据摘录与 context hash 对齐）；出题结果必须包含 `questions`（M10 manifest 另声明 `questions_answer_verified` 后置条件，工具内强制、runtime 侧 advisory）；历史回忆必须有 `<history_excerpt>`。验证结果以 `PostconditionReport{skill_id, valid, passed[], failed[]}` 写入 Trace。
- **决策信号**（`decision.py` 内建规则）：变式请求识别（`仿照/类似的题/变式/拟合/同类型` 等触发 `asks_for_variant`）、资料提及（`资料/文件/教材/PDF/上传` 等）、参考题正文识别（题目/例题/参考题后跟正文或算式）、内容型问题判定（复用 `preresearch.is_content_question`）与教材信号（`material_signals.mentions_title`）。

## State & storage

- 本层**无自有持久化**：决策、门控与后置条件结果全部写入每轮 Trace（`traces/<run_id>`），Skill 定义在代码内随版本发布。
- 工具读写的数据（会话 quiz_history、检索索引、transcript）属各自模块的存储（见 [assessment.md](assessment.md) 与 [backend-runtime.md](backend-runtime.md) 的存储布局总表）。
- Skill Manifest 支持多版本并存（`registry.register(manifest, active=…)`），当前所有内置 Skill 均为 `1.0.0` 单版本；`turn_start.skill_versions` 钉住每轮实际生效版本。

## Main flows

1. **决策（每轮，`SKILL_RUNTIME_MODE != off`）**：supervisor 在 understanding + StudentSnapshot 之后 `build_task_frame` → `decide`（Registry 候选规则评分 + `policy.evaluate_preconditions` 硬检查）→ Trace 写 `skill_task_frame` 与 `skill_decision`（含 runtime_mode、候选分数、理由码、失败前置条件）。
2. **门控（gated）**：Planner 产出 TaskPlan 后执行 `gate_plan`：没有资料时移除检索、没有历史时移除回忆、普通练习只保留 generate、完整参考题变式只保留 fit、缺参考题则生成一个只询问关键输入的 clarification plan。
3. **执行与后置条件**：Executor 按当前 PlanStep 重算可见工具（gated）；每次工具返回后 `SkillRuntime.validate_result` 校验后置条件并写 `skill_postconditions` Trace；gated 下只有非 error 且后置条件通过才发 `skill_plan_advance` 推进计划；无工具绑定的 advisory teaching step 不阻塞后续 assessment step（记 `skill_plan_advisory` 后跳到下一个工具步骤）；失败留在当前步骤由既有恢复提示与 `MAX_STEPS` 控制。预检级（pre-retrieval grounding）后置条件在工具链早期即校验一次，不等整轮结束。
4. **动态 Skill Prompt**：`skill_cards_preamble` 只把当前 TaskPlan 引用的 Skill Card（id/version/用途/前置条件/成功标准）注入上下文，不注入完整 Registry；Tutor Prompt 使用通用 Skill 决策边界，Planner Prompt 要求 `skill_id` 必须来自当前能力列表且禁止编造。
5. **能力路由**：`router.CAPABILITIES` 由 `capability_tool_map()` 从 Registry 生成（role → 工具名集合），Router 与 Executor 的可见工具集全部以此为准。
6. **shadow 模式**：只记录决策与对照，不替换 Planner/ReAct 行为（裸 uvicorn 默认）；回滚时显式设 `SKILL_RUNTIME_MODE=shadow` 即可。

## Dependencies

- **M1 对话内核**：supervisor/planner/executor/router 是本层的宿主管线；本层不改变 SSE 契约与前端页面。
- **M2**：StudentSnapshot 进入 TaskFrame（学段/资料/历史信号的来源之一）。
- **M3**：advisory teaching step 的教学语义（无工具绑定、只注入提示）。
- **工具域**：`generate_quiz`/`fit_quiz` 的生成质量链属 [assessment.md](assessment.md)；`knowledge_search`/`knowledge_read` 属 RAG/知识域（BM25 基线 + 向量可选）；`recall_history` 属上下文工程域。
- **LLM 客户端**：工具经 `core/llm_async.py` 单通道执行；工具步的思考/信封预算由 `EXECUTOR_TOOL_*` 控制（聊天内核配置）。
- **Trace 基建**：`core/trace.py`（每轮 run）。

## Invariants / security boundaries

- 本层不引入多 Agent 进程，不把 M2-M9 改造成互相对话的子 Agent；前端「纸墨书院」视觉、页面结构、SSE 事件和现有工具名称不变。
- 所有 Skill 卡片、Trace 与 Manifest 禁止包含 API key/JWT secret；工具仍由会话/工作区作用域构造，资料权限边界不变（工具 schema 不暴露 `student_id`/`file_ids`/owner 命名空间）。
- 只有 `policy.py` 这一确定性层决定前置条件是否满足；LLM 可以提议 Skill，不能自批前置条件。
- Registry 是工具能力唯一真相源：`router.CAPABILITIES` 由 `capability_tool_map()` 生成，禁止另建第二份工具表。
- 每层读写钩子包在 try/except 内，任何失败只记 Trace 绝不影响对话流（统一护栏原则）；决策与门控路径永不抛出到对话主管线。
- `off` 关闭旁路决策与动态 Skill Card 时，Registry 仍作为 Router 能力真相源（角色投影不失效）。
- `gated` 的门只收窄、不放宽：被 `gate_plan` 移除的工具不会经模型编造的 tool 参数重新出现；clarification plan 只询问关键输入，不臆测参考题内容。

## Configuration

- `SKILL_RUNTIME_MODE`（`shadow|gated|off`；裸 uvicorn 默认 `shadow`，`start.sh` 与 `.env.example` 默认 `gated`；非法值回退 `shadow`，由 `core/config.py::_resolve_skill_runtime_mode` 白名单归一）：
  - `shadow`：只记录决策，不改变现有行为；
  - `gated`：完整路径——前置条件强制、缺输入澄清（clarification plan）、PlanStep 逐步工具暴露、后置条件推进门；
  - `off`：关闭旁路诊断与 Skill Card，Registry 仍供 Router 使用。
- 相关工具运行参数（`TOOL_CONTEXT_*`、`EXECUTOR_TOOL_*`、`AGENT_MAX_STEPS`）属聊天内核配置，见 [backend-runtime.md](backend-runtime.md) 的智能层开关总表。
- 本层不读取任何密钥类配置；LLM 凭证统一收敛在 `core/config.py`。

## Observability

- Trace 事件：`skill_task_frame`（本轮可审计任务条件）、`skill_decision`（mode/候选分数/选中 Skill/理由码/失败前置条件）、`turn_start.skill_versions`（本轮所有 active Skill 版本）、`skill_postconditions`（passed/failed）、`skill_plan_advance` / `skill_plan_advisory`（计划推进与跳过）。
- `skill_decision.mode ∈ direct|execute|clarify`：direct 直接讲解、execute 执行工具计划、clarify 走澄清计划；候选分数与理由码使「为什么没出题/没检索」可回溯。
- 工具结果审计（如出题 `verification` 块）由工具域写入并随 SSE/quiz_history/Trace 透出，见 [assessment.md](assessment.md)。
- Trace 可经 `GET /trace/{run_id}[/html]` 检视（全局观测基建）。
- prompt 版本经 `prompts/registry.py` 钉扎（当前 active：`tutor_system@2.9.0`、`understand_system@1.3.0`、`planner_system@1.1.0`）；Skill Card 文案由 Registry 版本化，不存在独立的 `skill_decision_system` prompt。

## Tests / acceptance

`services/api/tests/`：

- `test_prompt_registry.py` — prompt/Skill Card 注册与版本钉扎。
- `test_context_budget.py` — Skill/工具注入的上下文预算。
- `test_quiz_card_contract.py` — Skill 卡片与题卡交付契约（含 `skill_ids` 链路）。
- `test_deepseek_tool_call_compat.py` — 工具调用解析兼容（DSML 变体、fails-closed）。
- `test_pseudo_tool_guard.py` — 伪工具标签护栏（含 chat_agent 集成回归）。
- 相邻回归：`test_knowledge_read.py`、`test_recall_history.py`、`test_material_trigger_signals.py`（TaskFrame 资料信号）、`test_task_understanding_context.py`。
- 端到端门控行为由浏览器回归覆盖（[../development/testing.md](../development/testing.md)）；`shadow` 模式可直接在裸 uvicorn 部署上做旁路审计对照。

聚焦运行：`cd services/api && python -m tests tests.test_prompt_registry`。

## Related ADRs

- 无。
