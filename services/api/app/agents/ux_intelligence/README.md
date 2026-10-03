# ux_intelligence — M8 交互体验智能

回答「怎么表达最适合这个学生」：横向输出适配层——只改变怎么表达、不改变教什么；不用代码改写回答，只产「如何表达」的软指令，`ux_profile` 是 UX 维度的单真相源。

## Owns

- `schema.py` — `UXProfile` / `InteractionStyle`（tone/detail_level/visual_preference/pacing/patience）/ `FeedbackType` / `MotivationState` / `UXEvent`
- `feedback_analyzer.py` — 规则分类体验反馈（太长/太短/太难/太快/太慢/praise；对错归 M4 verdict）
- `engagement_tracker.py` — 回答长度窗口折叠、abandon 启发式与反馈窗口维护
- `learner_profile.py` — M2 `LearningStyle` 防御性只读桥（学术偏好不重复拥有）
- `interaction_style.py` — UX 维度 → 表达指令文本
- `explanation_adapter.py` — `build_directive`：教学计划 + UX 上下文 → `ResponseDirective`
- `context_builder.py` — 渲染 `[交互智能·…]` 软指令块与个性化 greeting
- `response_quality_evaluator.py` — 单轮表达质量评估（`communication_score` + `ExpressionFailure`，只落 UXProfile）
- `motivation_engine.py` — streak / 里程碑 / `motivation_snapshot`（活动数据读统一聚合层）
- `manager.py` — `UXService`：directive / greeting / record_turn / profile / engagement / motivation / activity
- `store.py` — `students/<id>.ux_profile.json`（工作集）与 `.ux_events.jsonl`（append-only 黑盒）

## Does not own

- 学术讲解偏好（M2 `LearningStyle` 单真相源，本包只读；本包只拥有 M2 没有的 UX 维度）
- 教什么与教学策略（M3：directive 只读接收教学计划，绝不回写；调整绝不碰 TeachingPlan）
- 学习证据与长期记忆（绝不写；长期记忆归 M6，活动事实读 `agents/activity_aggregator.py`）
- API 路由（`api/v1/ux.py` 全只读，为集成侧）

## Design

四层管线（信号→推理→适配→渲染）、滞后效应、M8↔M3 边界与降级行为见
[docs/architecture/ux.md](../../../../../docs/architecture/ux.md)。

## Tests

- `services/api/tests/test_ux.py` — 四层管线、滞后效应、abandon 保守性、里程碑一次性、M8↔M3 import-clean 边界
- `test_directive_arbitration.py` — 软指令仲裁（策略深度 vs UX 简洁的确定性收敛）
- `test_supervisor_hooks.py` — 6f 写钩子不静默失败
- `test_style_inference.py` — M8 反馈窗口 → M2 风格折叠链（信号归属 M8，写入归 M2）

## Key entry points

- `manager.py::UXService` — supervisor 3g/6f 钩子与 `/ux/*` 投影的服务入口
- `explanation_adapter.py::build_directive` — `ResponseDirective` 唯一生产方
- `store.py` — `ux_profile` 单真相源的持久化面
