# agents — 对话编排与智能层根

对话回合的编排内核（understanding → TaskFrame → plan → 工具/技能执行 → 后置处理）与 M2–M10 各智能子包的宿主。

## Owns

- 根级编排文件：`supervisor.py`（V2 八步管线与智能层读写钩子）、`chat_agent.py`（`run_turn` 分发与 V1 legacy 路径）、`task_understanding.py`、`planner.py`、`router.py`、`executor.py`、`state.py`、`preresearch.py`、`reasoning_live.py`、`reasoning_narrator.py`、`reasoning_summarizer.py`、`pseudo_tool_guard.py`、`material_signals.py`、`activity_aggregator.py`、`notes_agent.py`（笔记智能体）。
- 智能子包（每行职责，详情见各自 README）：
  - `student_model/`（M2 学生模型：画像 + 统一学习证据账本）→ [student_model/README.md](./student_model/README.md)
  - `teaching_engine/`（M3 教学引擎：六模式状态机 + 跨轮教学记忆）→ [teaching_engine/README.md](./teaching_engine/README.md)
  - `assessment/`（M4 测评智能：三级评分 + 约束出题 + CAT）→ [assessment/README.md](./assessment/README.md)
  - `knowledge/`（M5 知识智能：教材图谱 + 概念检索）→ [knowledge/README.md](./knowledge/README.md)
  - `memory/`（M6 记忆智能：有界 prompt 画像 + 策略聚合）→ [memory/README.md](./memory/README.md)
  - `evaluation/`（M7 评估改进智能：TurnTrace 诊断 + 改进建议）→ [evaluation/README.md](./evaluation/README.md)
  - `ux_intelligence/`（M8 交互体验智能：UX 画像 + 输出适配）→ [ux_intelligence/README.md](./ux_intelligence/README.md)
  - `learning_orchestration/`（M9 学习编排：多目标 → 周任务 → 今日任务 + SM-2）→ [learning_orchestration/README.md](./learning_orchestration/README.md)
  - `skill_runtime/`（M10 技能运行时与证据门）→ [skill_runtime/README.md](./skill_runtime/README.md)
  - `site_assistant/`（领域：站内导航、领域动作与实体深链）→ [site_assistant/README.md](./site_assistant/README.md)

## Does not own

- HTTP 层（`api/`，见 [../api/README.md](../api/README.md)）：编排事件由 `api/v1/chat.py` 经 SSE 转发。
- 通用工具实现（`tools/`）与跨领域 primitive（`core/`）：executor 按工具协议调用，不拥有工具本体。

## Design

- M1 主管线（八步管线、执行器护栏 R1–R4/R10/R13、GSSC 上下文工程、钩子正交降级）：[docs/architecture/conversation.md](../../../docs/architecture/conversation.md)
- M1–M10 模块索引表与层次关系（M5 输入、M8 输出、M9 纵向编排、M10 能力控制）：[docs/architecture/README.md](../../../docs/architecture/README.md)

## Tests

- 编排相关回归在 `services/api/tests/`：`test_supervisor_*.py`、`test_task_understanding_context.py`、`test_task_launch.py`、`test_chat_quiz_intent.py`、`test_directive_arbitration.py`、`test_pseudo_tool_guard.py`、`test_circuit_breaker.py`、`test_preresearch.py`、`test_search_query_planning.py`、`test_reasoning_live_stream.py`。

## Key entry points

- `chat_agent.py`：`run_turn`（按 `SUPERVISOR_MODE` 分发 v2/legacy，统一错误分类）。
- `supervisor.py`：`run`（V2 八步管线，逐事件 yield SSE）。
- `planner.py`：`make_plan`（规则 fast path + LLM planner + validator）。
- `executor.py`：`execute`（ReAct 循环 + 护栏全集）。
