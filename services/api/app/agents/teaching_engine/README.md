# teaching_engine — M3 自适应教学引擎

回答「这个学生现在该怎么教」：以只读 TeachingContext 为输入，经确定性规则产出教学模式、深度与 focus/avoid 软指令，并用跨轮教学日志支撑 INTRODUCTION→EXPLANATION→PRACTICE→CHALLENGE 推进。

## Owns

- `manager.py` — `TeachingManager`（`adapt` / `record_turn`）、`adapt_from_context`、`is_enabled`
- `state.py` — `TeachingContext`（只读教学上下文投影）与 `TeachingOutcome`
- `strategy.py` — `TeachingMode` 六模式枚举与 `select_strategy` 状态机
- `policy.py` — `compose`：模式+上下文+已应用指导 → `TeachingStrategy`（focus/avoid/next_check/plan_hints/rationale）组装唯一入口
- `difficulty.py` — 1-5 内部难度拨盘（最近作答准确率升降档、钳位、easy/medium/hard 映射）
- `misconception.py` — 四分类错误诊断（概念/步骤/计算/推理）与纠错配方
- `curriculum.py` — 学习路径建议（前置已满足、按难度排序、复习建议）
- `stage_profile.py` — 学段细则单一事实源（四学段×七维度：难度锚点/例题风格/典型错因等，全链注入）
- `session_summary.py` — 确定性课堂小结与恢复锚点
- `decision_adapter.py` — `TeachingDecision` 有界 LLM 决策（触发门、白名单校验、受限落策略）
- `teaching_log.py` — `students/<id>.teaching.json` 跨轮教学日志（file_lock + 原子写）
- `guidance_store.py` — `students/<id>.teaching_guidance.json` 已应用教学指导（apply/revoke/load_active）

## Does not own

- 学生知道什么（M2 `student_model`：运行时零 import，一切学生状态经只读 TeachingContext 注入——Import-clean 不变量）
- 表达方式适配（M8 `ux_intelligence`）、跨会话记忆（M6）
- 教学指导的生产端（M7 评估经 API 层写 guidance_store，M7 从不 import 本包）
- 路由投影（`api/v1/student.py` 的 `/student/teaching-log`、`/student/learning-path` 为集成侧）

## Design

模式状态机、难度拨盘、指导消费、课堂小结与降级护栏的完整设计见
[docs/architecture/teaching-engine.md](../../../../../docs/architecture/teaching-engine.md)。

## Tests

- `services/api/tests/test_teaching_engine.py` — 模式状态机、难度拨盘、策略组装、import-clean 边界
- `test_teaching_decision.py` — 触发门、validate_decision 白名单、shadow/active、失败回退
- `test_teaching_guidance.py` — 指导部署/消费/吊销回滚
- `test_session_summary.py` — 小结骨架、恢复锚点、润色门
- `test_projection_api.py` — `/student/teaching-log`、`/student/learning-path` API 面

## Key entry points

- `manager.py::TeachingManager.adapt` — supervisor 3b 读钩子的策略入口；`record_turn` — 6c 写钩子
- `policy.py::compose` — 策略组装唯一入口；`stage_profile.py` — 全链学段细则来源
- `teaching_log.py` / `guidance_store.py` — 两个持久状态文件的唯一读写面
