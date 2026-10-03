# Learning Orchestration（M9 学习编排智能）

M9 回答「未来几周到几个月怎么持续成长」：纵向编排层，拥有长期目标 → 周计划 → 周任务 → 子任务 → 今日任务的计划层级、SM-2 间隔复习与学习习惯统计，只读消费其他智能层并经事件流外溢影响。

## Owns

- `manager.py` — `LearningOrchestrationService` 唯一 facade：`build_directive` / `record_turn` / `record_quiz_evidence` / `consume_evaluation_outbox` / 目标与任务 CRUD / `regenerate_plan` / `today_tasks` / `summary`。
- `schema.py` — 计划层级数据模型、`ORCHESTRATION_EVENT_TYPES` 事件白名单与上限常量（`_MAX_GOALS=4` 等）。
- `goal_analyzer.py` — 逐目标 gap 分析（`unknown/weak` 语义）、`estimate_schedule` 时间容量区间。
- `goal_manager.py` — 目标追加/编辑/删除（上限 4）。
- `weekly_planner_llm.py` — LLM 周规划器（校验门 + 确定性 fallback）、周一对齐与 id 生成。
- `learning_planner.py` — 确定性周规划（复用 M3 `build_learning_path`）与 `derive_tasks_fallback`。
- `daily_composer.py` — LLM 每日编排器（候选池 + 校验门 + 确定性回退）。
- `task_executor.py` — `materialize_day` gap-fill、完成语义（`quiz_evidence|self_report`）与标题守卫。
- `spaced_repetition.py` — SM-2 实现、`quality_from_verdict`、`submit_review`。
- `schedule_engine.py` — `capacity_report` 纯函数、周起点/窗口计算。
- `subtask_advisor.py` — 单任务 LLM 拆解（2-4 个可执行子任务）。
- `event_emitter.py` — `OrchestrationLearningEvent` 白名单 + 去重。
- `context_builder.py` / `habit_tracker.py` / `history.py` / `store.py` — `[编排智能·…]` 指令块、习惯统计、事件回放、持久化。

## Does not own

- 作答判分与受理归 M4/M2（`agents/assessment/`）；本包只消费判定结果，`record_quiz_evidence` 是判定 → SM-2 复习质量 + 任务归因的唯一入口（attempt_id 幂等）。
- 不直接写 M2/M3/M5/M6 存储：能力判断归统一学习评价域，事件外溢由 M6 `consume_turn` 决定是否固化。
- 任务启动绑定投影 `students/<id>.learning_episodes.json` 在 `core/learning_episodes.py`（许可的可重建投影，非权威账本）；API 路由在 `api/v1/orchestration.py`。

## Design

- 任务唯一性铁律：落盘即身份稳定，绝不替换删除；用户创建（`source="user"`）的周/周任务/子任务任何重规划管线不触碰。
- SM-2 与统一学习评价正交：只有已审核通过且非全演示帮助的作答证据进通过路径；`unknown` 判定返回 None 不进观察；自评与作答证据分开标记 `source`。
- 确定性优先：SM-2 / 习惯 / 调度 / 规划为纯函数，每轮关键路径零 LLM；LLM 调用只在 API 发起的异步路径且必有校验门与确定性回退。
- 关闭（`ORCHESTRATION_MODE=0`）时 supervisor 双 hook 全 no-op，M1-M8 行为逐字节不变；任何失败降级 no-op 绝不破坏对话轮。
- 权威事实源见 [docs/architecture/learning-orchestration.md](../../../../../docs/architecture/learning-orchestration.md)。

## Tests

`services/api/tests/`（聚焦运行：`cd services/api && python -m tests tests.test_orchestration`）：

- `test_orchestration.py` — SM-2 / 计划 / 任务 / 复习闭环、多目标与 fallback、双守卫、容量报告。
- `test_task_launch.py` — launch 绑定、幂等 relaunch、episode 生命周期。
- `test_learning_consumers.py` — 评价 outbox 消费幂等与审核门。
- `test_notes_m9_sync.py` / `test_assistant_orchestration.py` — 相邻模块对 M9 状态的联动消费。

## Key entry points

- `LearningOrchestrationService.build_directive`（supervisor 3h 读侧）/ `record_turn`（6g 写侧曝光与习惯）— 双 hook。
- `record_quiz_evidence` — 判定 → 复习与任务归因唯一入口。
- `consume_evaluation_outbox` — journal outbox 消费者（`consumer="m9"`，幂等 ack）。
