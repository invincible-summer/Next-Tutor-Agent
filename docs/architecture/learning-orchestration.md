# Learning Orchestration（M9 学习编排智能）

M9 回答「未来几周到几个月怎么持续成长」：纵向编排层，拥有长期目标→周计划→周任务→子任务→今日任务的计划层级、SM-2 间隔复习与学习习惯统计，只读消费其他智能层并经事件流外溢影响。

## Purpose / Scope

- **计划层级**：`长期目标（多个，上限 4）→ 周计划（多周）→ 周任务（WeekTask）→ 子任务（SubTask）→ 今日任务（DailyTask）`。旧 `Milestone` 类与 `state.milestones` 仅为读取旧数据保留，不再产出；长期任务层已删除。
- **多目标**：`state.goals`（每目标独立 id `g_{n}`）与 `goal_states` 差距分析 1:1 配对；重规划把各目标 required_skills 按目标顺序合并去重后喂入同一份共享周计划。
- **SM-2 间隔复习**（承重件）：经典 SM-2 调度，与统一学习评价正交（复习间隔是 M9 的，能力判断是 M2 评价域的）。
- **容量可行性**、习惯统计（habit）、任务启动绑定（launch/episode）与作答证据直供（outbox 消费）。
- 边界：不直接写 M2/M3/M5/M6 存储；通过 EventEmitter 事件流让 M6 决定是否固化长期记忆。作答判分本身属 [assessment.md](assessment.md)。

## Owned code

- `services/api/app/agents/learning_orchestration/`
  - `manager.py` — `LearningOrchestrationService` 唯一 facade（`build_directive` / `record_turn` / `record_quiz_evidence` / `consume_evaluation_outbox` / 目标与任务 CRUD / `regenerate_plan` / `today_tasks` / `summary`），`is_enabled()` 读 `ORCHESTRATION_MODE`。
  - `schema.py` — 计划层级数据模型、`ORCHESTRATION_EVENT_TYPES` 白名单、上限常量（`_MAX_GOALS=4` 等）。
  - `goal_analyzer.py` — 逐目标 gap 分析、`unknown/weak` 语义、`estimate_schedule` 时间容量区间、`summary().needs_replan`。
  - `goal_manager.py` — 目标追加/编辑/删除（上限 4，超出 400）。
  - `weekly_planner_llm.py` — LLM 周规划器（校验门 + 确定性 fallback）、`weeks_from_skeletons`（周一对齐、id `wt_{week}_{seq}` / `st_{task}_{seq}`）。
  - `learning_planner.py` — 确定性周规划（复用 M3 `build_learning_path`；`_MAX_CONCEPTS_PER_WEEK=5`）与 `derive_tasks_fallback`。
  - `daily_composer.py` — LLM 每日编排器（候选池 + 校验门 + 确定性回退）。
  - `task_executor.py` — `materialize_day` gap-fill、`today_tasks`、完成语义（`completion_source=quiz_evidence|self_report`）与标题守卫。
  - `spaced_repetition.py` — SM-2 实现、`quality_from_verdict`、`submit_review`。
  - `schedule_engine.py` — `capacity_report` 纯函数、周起点/窗口计算。
  - `subtask_advisor.py` — 单任务 LLM 拆解（2-4 个可执行子任务）。
  - `event_emitter.py` — `OrchestrationLearningEvent` 白名单 + 去重。
  - `context_builder.py` / `habit_tracker.py` / `history.py` / `store.py` — 指令块构建、习惯统计、事件回放、持久化。
- `services/api/app/core/learning_episodes.py` — 任务启动绑定投影 `students/<id>.learning_episodes.json`（episode active→completed/abandoned + revision；许可的可重建投影，非权威账本）。
- API 路由：`services/api/app/api/v1/orchestration.py`。

## Public contracts

**REST API（前缀 `/api/v1/orchestration`，身份一律 `resolve_student_id()`）**

- 读：`GET /plan`（附 `capacity`，`needs_replan` 同）、`GET /today`、`GET /habit`、`GET /review`、`GET /simulation`（确定性前向投影；前端已下线该卡片，端点保留）。
- 目标：`POST /goal`（**追加**一个目标，上限 4 超出 400；响应带 `weeks` + `first_task` kickoff CTA）、`PATCH /goal/{goal_id}`、`DELETE /goal/{goal_id}`（按目标 id 寻址，未知 404；三者尾部自动重规划）。
- 重规划：`POST /regenerate`（响应 `reason ∈ ""|"no_goal"|"empty_plan"`）。
- 今日任务：`POST /task`、`PATCH /task/{id}`（附当日 `capacity_warning` advisory）、`DELETE /task/{id}`、`POST /task/{id}/complete`。
- 任务启动绑定：`POST /task/{id}/launch` → `{episode_id, session_id, launch_url, resumed}`（404=不存在/已完成/非本人；未完成 relaunch 幂等复用同 episode/会话）。
- 周级：`POST/DELETE /week`、`POST/DELETE /week/{i}/concept[/{cid}]`、`POST/DELETE /week/{i}/task[/{tid}]`、`POST/PATCH/DELETE /week/{i}/task/{tid}/subtask[/{sid}]`、`POST /week/{i}/task/{tid}/suggest`（✨子任务推荐）。
- `PATCH /schedule`（返回重算 capacity 报告）。

**ID 与人工不可覆盖契约**

- id 前缀：目标 `g_{n}`；auto 周任务 `wt_{week}_{seq}`、人工周任务 `user_wt_{week}_{seq}`；子任务 `{user|auto}_st_{task}_{seq}`；今日任务 `custom=true` 表示用户创建。
- `source="user"` 的周（`origin=user` 整周保留）、周任务、子任务，任何重规划管线不触碰；`_merge_user_plan` 按周窗口（7 天 bucket）把 auto 周内的 user 任务并回重建后的计划，week_index 按 week_start 重排。

**事件与证据接口**

- `OrchestrationLearningEvent`：连击/进度等事件白名单（`ORCHESTRATION_EVENT_TYPES`）+ 去重，由 supervisor 6g 转发给 M6 consume_turn，M6 决定是否固化。
- `record_quiz_evidence(verdict, attempt_id, …)`：M4 判定 → SM-2 复习质量 + 任务归因的唯一入口（attempt_id 幂等；会话携带 task_binding 时只完成绑定任务）。
- `consume_evaluation_outbox(student_id)`：以 journal outbox 消费者身份领取 `consumer="m9"` 的 `OpResultCommitted` 事件并 ack。

**数据模型要点（`schema.py`）**

- `ScheduleConfig`：每日可学分钟（`daily_minutes`）与可用天，是 `estimate_schedule` 与 `capacity_report` 的容量基准。
- `WeekTask{focus, tasks[]}` / `WeekTask{title, kind, concept_ids, subtasks[], effective_done}` / `SubTask{title, estimate_minutes, source}` / `DailyTask{custom, completion_source, evidence_attempt_id, week_task_id/subtask_id 引用}`；`kind` 与 `phase` 为受控枚举（校验门只接受合法值）。
- `HabitStats`：学习习惯聚合（连击、活跃天数），`GET /habit` 读取。
- `ReviewItem`：SM-2 复习卡投影，`GET /review` 读取；前端并入今日任务「间隔复习」子栏。

## State & storage

- `students/<id>.orchestration.json` — 编排工作集（goals/goal_states/weeks/today/SRS 卡片/schedule）。
- `students/<id>.orchestration_events.jsonl` — 事件黑盒（append-only）。
- `students/<id>.learning_episodes.json` — launch 绑定投影（可重建）。
- 以上均在 `students/` 前缀下，账户删除（`core/account_data.purge_account`）与孤儿扫描按前缀通用覆盖；`chat_history/settings/learner_evaluation_policy.json` 为共享的评价调度策略（见下）。
- 持久化 fail-open：文件损坏/缺失视为空状态，绝不阻断对话或判分调用。
- **双模存储（ADR-0017）**：`orchestration=sql`（`DOMAIN_DOCUMENT_BACKENDS`，需 `DATABASE_URL`）时，工作集与事件日志写入 `orchestration_documents` 表（kind `state`/`events`；事件整条 JSONL 为一个文档，追加在单次行锁 mutate 内完成，等价文件态 `file_lock` 的跨进程原子性；坏行以 invalid_count 保持语义）；文件实现保留可回退。`learning_episodes.json` 投影与派生索引留文件态。历史数据用 `scripts/migrations/runtime_to_enterprise/import_documents.py --domain orchestration` 迁移并 `--verify` 摘要比对；SQL 态账户清除由 9 域 `purge_owner` 循环覆盖。

## Main flows

1. **目标→周计划**：`POST /goal`（或编辑/重规划）→ `GoalAnalyzer` 逐目标 gap 分析（无观测概念 `unknown` 不宣称缺口；有证据未达标才 `weak`；计划仍覆盖 unknown 概念）+ `estimate_schedule` 容量区间 → `weekly_planner_llm` 一次调用产出 N 周语义化周计划；required_skills 合并后超百时只规划前 `num_weeks×5` 概念的近端窗口；校验门（ids ⊆ 窗口、窗口全覆盖、非 review/summary 任务无重复概念、任务/子任务数上限、kind 合法）不过则回退确定性 `learning_planner` + `derive_tasks_fallback`。
2. **今日编排**：`daily_composer` 候选池 = SRS 到期 ∪ 本周未掌握概念 ∪ 本周未完成子任务（行动级，带引用）∪ M2 弱项 ∪ 昨日结转；LLM 挑 ≤ slots 个并产教练批注；校验门（id ∈ 池、kind/phase 合法、去重、≤ slots）失败回退确定性路径；`materialize_day` 只 gap-fill 缺失 (concept_id, kind) 键，绝不替换删除已落盘任务。
3. **任务启动绑定**：前端 TodayCard CTA 先 `launch` → 预创建携带 `task_binding{task_id, episode_id, concept_id}` 的会话 → 跳 `launch_url?q=首选消息&send=1`；launch 失败回退纯文本深链。判分端点把会话 task_binding 传给 `record_quiz_evidence`：SRS 复习键用任务规范 concept_id，作答只完成绑定任务；任务完成时 episode 置 completed。
4. **完成语义**：曝光轮（6g）只把概念匹配的今日任务 pending→in_progress；完成只有两条路——任务绑定证据（会话内已受理作答，`completion_source=quiz_evidence` + `evidence_attempt_id`）或显式勾选（`self_report`，零 M2 写入）；无绑定会话的作答不按概念名自动完成今日任务。完成带子任务引用的今日任务 → 对应 SubTask 置 done（驱动 WeekTask.effective_done）；**标题守卫**：位置 id 被重规划复用时只有标题仍匹配才记功。
5. **作答证据直供**：判分全部发生在聊天轮外；评价 worker（`evaluation/worker.py::_advance_outbox`）在作业终态后调用 `consume_evaluation_outbox` 幂等消费（journal `consumer_ack` + M9 侧 attempt_id 去重双层）；只有已审核（verification=passed）且非全演示帮助的结果才进通过路径延长 SM-2 间隔；复核/撤销事件重放受影响复习卡。`core/quiz_attempts` 侧直接喂入仅作受理路径兜底。
6. **SM-2**：`quality_from_verdict()`（correct→5 / partial→3 / wrong→1；**unknown 及未识别判定返回 None**——无有效召回证据就没有观察）；exposure 与 unknown 只建卡安排首次检查不进通过路径；`submit_review` 自评可调日程但事件带 `source: "self_report"` 与作答证据分开；新建卡 `last_quality=None`。
7. **容量**：`capacity_report` 纯函数按日汇总 estimate_minutes vs daily_minutes 标记超载日（窗口默认 30 天）；全部 advisory 不阻断（学生可自主超排），今日卡超载显示警示条。
8. **读侧注入与曝光**：supervisor 步骤 3h 调 `build_directive` 生成 `[编排智能·…]` 建议块（复习到期/今日任务/连击等可行动内容，无为空）；步骤 6g `record_turn` 记录曝光、更新习惯统计并把概念匹配的今日任务置 in_progress。
9. **进度投影与预测**：`GET /simulation` 提供确定性前向投影 + `headline()` 人话结论（零 LLM 模板）；前端已下线该卡片（抽象数字不可行动），needs_replan banner 改为「让教练帮我调整」对话深链，目标编辑保存仍是自动重规划入口。
10. **防死循环双守卫**：无目标永不提示；`last_plan_attempt` 区分「从未规划」与「规划了但无可安排内容」——empty_plan 是合法终态。

## Dependencies

- **M2 统一学习评价**：读 `learner_views` 投影作弱项/差距输入；outbox 消费依赖评价 worker（进程内 lifespan 启动，单 worker 假设）。
- **M3 教学引擎**：`build_learning_path` 作为单会话推理引擎复用（REUSE NOT BUILD）。
- **M5 知识图谱**：required_skills/概念 id 规范化来源。
- **M6 记忆**：EventEmitter 事件消费方。
- **M4 判分端点**（`/quiz/*`、`/assessment/answer`）：`record_quiz_evidence` 与 task_binding 的上游（见 [assessment.md](assessment.md)）。
- **supervisor**：`build_directive`（步骤 3h，读侧 `[编排智能·…]` 建议块）与 `record_turn`（步骤 6g，写侧曝光/习惯/任务推进）双 hook。
- **站内助手与笔记**：site assistant 的编排动作、M-Notes 的复习联动以 `LearningOrchestrationService` facade 为唯一入口，不直连内部模块。

## Invariants / security boundaries

- 观察者契约：只拥有编排状态（计划/调度/SRS 卡/任务执行），对 M2/M3/M5/M6 只读；事件外溢由 M6 决定持久化。
- 任务唯一性铁律：落盘即身份稳定，绝不替换删除；未完成任务跨天结转置顶；用户任务任何管线不触碰。
- SM-2 与统一学习评价正交：只审通过的作答证据进通过路径；自评与作答证据分开标记。
- `ORCHESTRATION_MODE=0` 时两个 supervisor hook 全部 no-op，M1-M8 行为逐字节不变；任何失败降级为 no-op 绝不破坏对话轮。
- 确定性优先：SM-2/习惯/调度/规划为纯函数，每轮关键路径零 LLM；LLM 调用只发生在 API 发起的异步路径且必有确定性校验门与回退。
- API 身份只经 `resolve_student_id()`；他人任务/episode 404。

## Configuration

- `ORCHESTRATION_MODE`（默认 `1` 开启；`0/false/off` 关闭）——由 `manager.py::is_enabled` 直接读取环境变量。
- 管理员 `GET/POST /admin/learner-evaluation-policy`：语义解释即时（默认）或每日零点批次（业务 IANA 时区；每日模式下受理冻结 `eligible_after_utc`，DailyPlanner 在本地零点后关闭上一自然日窗口 `OpDailyBatchClosed`，漏执行日期重启补关；切回即时立即释放入队）。该策略作用于共享评价 worker，见 [assessment.md](assessment.md)。

## Observability

- `students/<id>.orchestration_events.jsonl` 黑盒记录全部编排事件（可回放审计）。
- 读侧注入：每轮 preamble 的 `[编排智能·…]` 建议块（`build_directive`，无可行动内容时为空串）。
- `/plan`、`POST/PATCH /task`、`PATCH /schedule` 附带 capacity/capacity_warning advisory 字段，前端超载警示条直接消费。
- journal 侧：`OpResultCommitted` outbox 事件（`consumer="m9"`）与 `OpConsumerAck` 提供消费幂等审计；`needs_replan` banner 是对话深链「让教练帮我调整」（无手动重规划死按钮）。

## Tests / acceptance

`services/api/tests/agents/learning_orchestration/`（聚焦运行：`cd services/api && python -m tests tests.agents.learning_orchestration.test_manager`）：

- `test_schema.py` / `test_store.py` / `test_srs.py` — schema 往返与兼容、store 单一事实源边界、SM-2 复习与习惯追踪。
- `test_planner.py` / `test_manager.py` / `test_api.py` — 目标分析与计划生成、任务执行与写回、事件发射与 API 合同。
- `test_task_launch.py` — launch 绑定、幂等 relaunch、episode 生命周期。
- `test_learning_consumers.py` — 评价 outbox 消费幂等与审核门。
- `test_learner_evaluation_policy.py` — 即时/每日调度策略与补关账。
- `test_notes_m9_sync.py`、`test_assistant_orchestration.py` — 相邻模块对 M9 状态的联动消费。
- 存储纪律：所有测试继承 `tests/storage_sandbox.py::StorageSandboxTestCase`，编排三文件均在沙箱根内被覆盖（账户清除与孤儿扫描按 `students/` 前缀钉死）。

## Related ADRs

- ADR-0002 运行数据单根（orchestration/episodes 均在统一数据根 students/ 下）。
- ADR-0004 single-worker（评价 worker 进程内运行，outbox 消费依赖单 worker 假设）。
