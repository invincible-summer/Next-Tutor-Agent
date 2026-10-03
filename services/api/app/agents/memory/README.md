# Memory（M6 记忆智能）

M6 回答「系统该记住这个学生的什么、记多久」：管理有界的跨会话 prompt memory 精简画像、per-student 程序性策略成功率与学习习惯聚合，并保留旧情景/语义记忆的只读兼容层。

## Owns

- `manager.py` — `MemoryService`：`build_directive`（读钩子 3e）、`consume_turn`（写钩子 6d）、`retrieve`（兼容检索）、计数查询。
- `prompt_memory.py` — 活动 prompt memory 全部逻辑：窗口策略（`get_policy` / `set_user_window`）、`register_session` / `record_contribution` / `forget_session_contribution`、`_fold_into_core` / `_trim_overflow`、`[提示词记忆·精简画像]` 指令与 `public_view`。
- `procedural.py` — `students/<id>.procedural.json`：per-student 策略成功率（`record_outcome` / `injectable_strategies`）。
- `habit_pattern.py` — M9 习惯事件聚合（`consolidate_habit_events` / `read_habit_patterns`，产出 `SemanticFact` 结构，不保存正文）。
- `episodic.py` / `semantic.py` — 旧情景/语义存储的只读兼容读取（`add_or_consolidate` 仅服务于习惯聚合回退与兼容投影）。
- `retrieval.py` — 跨存储 BM25 检索（时间衰减 + scope 优先级，服务兼容审计视图）。
- `classifier.py` / `context_builder.py` — 记忆分类与上下文块组装。
- `schema.py` / `store.py` — 数据类与存储路径绑定。

## Does not own

- 题目/作答/判分证据归统一学习证据 journal（M2，`agents/student_model/`）：独立学习档案与 prompt memory 分离，来源对话删除后证据保留。
- 工作区公共记忆 `Workspace.public_memory` 归工作区系统：仅同工作区可见，与用户级 prompt memory（全局统一计数）隔离。
- transcript 跨会话召回（`tools/recall_history.py`，`CROSS_SESSION_MEMORY` 控制）是与 prompt memory 独立的另一条通道。
- 系统级策略聚合归 M7（`agents/evaluation/`）；UX 画像归 M8；教学日志归 M3 teaching engine。

## Design

- 只有总体学习情况 / 当前水平 / 语气偏好 / 讲解偏好进入 prompt；对话正文、题目内容、能力数值一律不入。
- 最近窗口 + core profile 双层结构：窗口内贡献按 `session_id` 独立可撤销；滑出窗口后 LLM 压缩合入不可拆分的整体画像并执行硬字符上限——已压缩内容无法安全反向拆分时宁可标 `legacy_unknown` 也不伪造归属。
- 旧 episodic / semantic 生产只读：不追加、不 consolidation、不进 prompt（审计 Tab 在读）。
- procedural / habit 有界：结构化聚合无正文；procedural 记 per-student 策略成功率，与 M7 系统级聚合互不复制原始数据。
- 权威事实源见 [docs/architecture/memory.md](../../../../../docs/architecture/memory.md)。

## Tests

`services/api/tests/`：

- `test_memory.py` — 服务面、检索、计数与降级。
- `test_prompt_memory_lifecycle.py` — 窗口滚动、core 折叠、撤销/遗忘语义、`legacy_unknown`。
- `test_cross_session_memory.py` — prompt memory 跨普通/工作区对话统一计数与召回边界。
- `test_supervisor_hooks.py` — 6d 写钩子不静默失败。
- `test_projection_api.py` — `/memory/*` API 面。

## Key entry points

- `MemoryService.build_directive`（3e）/ `consume_turn`（6d）— supervisor 双 hook。
- `prompt_memory.set_user_window` / `get_policy` — 窗口策略（默认 15，可选 5-30）。
- `procedural.record_outcome` — 策略结果入账。
