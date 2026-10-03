# M8 交互体验智能（UX Intelligence）

回答「怎么表达最适合这个学生」：横向输出适配层——不改变教什么（[teaching-engine.md](teaching-engine.md) 职责），只改变怎么表达；不用代码改写回答，只产「如何表达」的软指令，LLM 在边界内自主表达。

## Purpose / Scope

- 四层输出适配管线（信号→推理→适配→渲染），每轮关键路径零 LLM。
- 只拥有 M2 没有的 UX 维度：tone（语气）/ detail_level（篇幅）/ visual_preference（图示）/ pacing（节奏）/ patience（耐心）。
- 单轮表达质量评估（ResponseQualityEvaluator，区别于 [evaluation.md](evaluation.md) 的跨轮聚合）与激励表达（连续天数 streak、里程碑）。
- 个性化开场白 greeting 与按日活动视图的数据出口。
- 不负责：学术讲解偏好（M2 `LearningStyle` 只读投影，绝不重复拥有）、长期记忆归 M6（M8 只拥有激励的表达方式）、学习证据与教学策略（绝不写）。

## Owned code

`services/api/app/agents/ux_intelligence/`（12 个 Python 文件）：

- `__init__.py`——模块契约（SINGLE TRUTH SOURCE / HORIZONTAL ADVISORY / GRACEFUL / DETERMINISTIC-FIRST）。
- `schema.py`——`UXProfile` / `InteractionStyle`（tone/detail_level/visual_preference/pacing/patience）/ `FeedbackType` / `Tone` / `DetailLevel` / `MotivationState` / `UXEvent`。
- `feedback_analyzer.py`——规则分类反馈（FeedbackType）：`explanation_too_hard / explanation_too_long / explanation_too_short / too_fast / too_slow / praise`（`classify` / `is_experience_signal`）；表达体验而非学术对错（对错是 M4 verdict）。
- `engagement_tracker.py`——长度窗口折叠 + abandon 启发式 + 反馈窗口维护（`apply_engagement_to_style` 纯函数推断）。
- `learner_profile.py`——UX 画像推断桥：`m2_learning_style_snapshot` 防御性只读 M2 学术偏好（失败返回 None），保持单真相源。
- `interaction_style.py`——UX 维度 → 表达指令文本（tone/detail/visual/pacing 指南与 `preferred_style_block`）。
- `explanation_adapter.py`——`build_directive`：教学计划 + UX 上下文 → `ResponseDirective`（含反馈调整、学术风格注记、动机行、意图守卫）。
- `context_builder.py`——渲染 `[交互智能·…]` 软指令块与个性化 greeting（`?lang&grade`）。
- `response_quality_evaluator.py`——单轮表达评估：feedback 措辞 + 追问次数 + 回答长度 vs 耐受 + M4 verdict 只读 → `communication_score(0-1)` + `ExpressionFailure`（抽象过高/冗长/过简/节奏/语气）+ 调整建议；`apply_score_to_profile` 只落 UXProfile。
- `motivation_engine.py`——streak / 里程碑 / `motivation_snapshot`（活动数据读统一聚合层）。
- `manager.py`——`UXService`：`build_directive` / `greeting` / `record_turn` / `profile` / `engagement` / `motivation` / `activity`。
- `store.py`——`students/<id>.ux_profile.json`（工作集）与 `.ux_events.jsonl`（黑盒 append）。
- 相邻只读层（非本包拥有）：`app/agents/activity_aggregator.py`——五源按日学习活动合并（journal/teaching log/orchestration events/ux events/eval traces 的本地日 union，旧 episodic 仅空集兼容回退并标注来源）；M8 motivation/greeting、M9 习惯与 `/ux/activity` 全部读它，不各存一套。

## Public contracts

- API（前缀 `/api/v1`，`app/api/v1/ux.py`，全只读、防御性永不抛错）：
  - `GET /ux/profile`——UX 交互画像（tone/detail/visual/pacing/patience、recent_feedback 计数、平均回答长度、abandon 信号）；前端「学习画像」面板数据源。
  - `GET /ux/engagement?limit=`——UXEvent 事件流。
  - `GET /ux/motivation`——streak 与里程碑快照。
  - `GET /ux/activity?days=`——按日活动计数（作答/教学/复习）+ streak 摘要与数据来源标注（`aggregated` vs `legacy_episodes`），days 默认 14、1–90。
  - `GET /ux/greeting?lang&grade`——个性化开场（继续提示 + 连续天数；`grade_zh` 参数保留兼容）。
- supervisor 钩子契约：读钩子 `_ux_directive_for_turn`（3g）返回 `[交互智能·…]` 表达适配指令；写钩子 `_ux_record_turn`（6f）分类反馈 + 折叠回答长度 + 表达质量评估回画像，纯函数零 LLM。
- 对 M2 的数据出口：`UXProfile.recent_feedback`（近 12 条规则分类）是 M2 `style_inference` 的信号源——方向 M8 采集、M2 折叠写入（见 [student-model.md](student-model.md)），M8 自身不写 `LearningStyle`。

## State & storage

| 路径 | 内容 |
|------|------|
| `students/<id>.ux_profile.json` | UX 画像工作集（style/motivation + 滚动信号：recent_feedback、recent_response_lengths、abandon_signals、`last_milestone_surfaced`） |
| `students/<id>.ux_events.jsonl` | UX 事件黑盒（append-only） |

- UXProfile 全部由 UXEvents 自动推断（非表单填写）；store 写入防御性、永不抛错。
- 连续天数等活动事实不落本模块存储：motivation 每次实时读 `activity_aggregator` 的本地日 union（day key 为用户本地 `YYYY-MM-DD`），M8 只持久化表达性状态（`last_milestone_surfaced` 等）。

## Main flows

1. **信号层**：`FeedbackAnalyzer.classify` 对学生消息做规则分类，产出 FeedbackType；只有体验类信号参与推断。
2. **推理层**：`EngagementTracker` 折叠回答长度窗口与反馈窗口；`apply_engagement_to_style` 纯函数推断 InteractionStyle（如 terse 精确提问→FORMAL 倾向、连续失败→ENCOURAGING、抱怨长度→缩短 detail）；`learner_profile` 只读叠加 M2 学术偏好；`MotivationEngine` 读 `activity_aggregator` 活动日集算 streak 与到期里程碑。
3. **适配层**：`interaction_style` 把 UX 维度翻译为表达指令行。
4. **渲染层（3g 读钩子）**：`ExplanationAdapter.build_directive` 只读接收教学计划/策略信号 + UX 上下文产出 `ResponseDirective`；`ContextBuilder` 渲染 `[交互智能·…]` 块追加到 `adaptation_recap`（advisory system 消息）；`UX_INTELLIGENCE_MODE=0` 时返回空。多软指令冲突由 supervisor `_arbitrate_directives` 仲裁（学生显式约束 > 红线 > 教学策略 > 表达与交互适配；「策略要深入 vs 画像要简洁」确定性收敛）。
5. **写钩子（6f）**：`_ux_record_turn` 分类反馈 → 记 UXEvent → `ResponseQualityEvaluator` 评估上一轮表达（含 M4 verdict 只读）→ `apply_score_to_profile` 只调整 UXProfile；「还是不懂」→ 记 abstraction_too_high → 下轮提升 example_density——闭合回路绝不触碰 TeachingPlan。
6. **滞后效应（hysteresis）**：≥2 次同类信号才切换风格（防过度反应）；abandon 启发式刻意保守（仅明确抱怨才计）；同一里程碑只恭喜一次（`last_milestone_surfaced`）。
7. **M8↔M3 边界**：ExplanationAdapter 只读接收教学计划、产出 directive，绝不回写；数据流单向、模块级 import-clean，由边界测试冻结。

## Dependencies

- 只读上游：M2 `LearningStyle`（`learner_profile` 防御性快照）、M3 教学计划/策略（渲染输入）、M4 verdict（表达质量评估）、M6/统一活动聚合 `agents/activity_aggregator.py`（streak 数据，长期记忆归 M6）。
- 上游调用方：M1 supervisor（3g/6f 钩子、指令仲裁）、前端学习画像面板与空态 greeting。
- 下游数据出口：M2 `style_inference`（recent_feedback 信号，M8 不写 M2）。
- 基础设施：`core/atomic.py`（经 store 防御写）、无 LLM 依赖（全管线零 LLM）。

## Invariants / security boundaries

- 单真相源红线：学术讲解偏好归 M2；M8 只拥有 UX 维度，绝不重复拥有、绝不写学习证据/教学策略。
- 不用代码改写回答：只产软指令，LLM 在边界内自主表达；horizontal advisory 而非强制。
- 每轮关键路径零 LLM；所有推断为纯函数。
- 调整只落 UXProfile，绝不碰 TeachingPlan；对 M2/M3/M5/M6/M7 零写入。
- `UX_INTELLIGENCE_MODE=0` 时两钩子 no-op，M1-M7 行为逐字节不变。
- 所有钩子包 try/except，失败只记 trace 不影响对话流（统一护栏原则）；API 全只读、按 student_id 隔离（`resolve_student_id()`）。

## Configuration

| 环境变量 | 默认 | 语义 |
|------|------|------|
| `UX_INTELLIGENCE_MODE` | `1` | `0` 关闭 M8：交互指令返回空、6f 记录 no-op |

## Observability

- trace 事件：`ux_record_hook_error` 等失败记录；读钩子注入内容计入 `adaptation_recap`。
- `GET /ux/{profile,engagement,motivation,activity}` 提供人可检查的画像/事件/激励/活动视图；`activity` 返回数据来源标注（聚合 vs 旧 episodic 兼容回退），UI 据此标签。

## Tests / acceptance

`services/api/tests/`：

- `test_ux.py`——四层管线、滞后效应、abandon 保守性、里程碑一次性、M8↔M3 import-clean 边界（含 teaching_engine 只读交互）。
- `test_directive_arbitration.py`——软指令仲裁（策略深度 vs UX 简洁的确定性收敛）。
- `test_supervisor_hooks.py`——6f 写钩子不静默失败。
- `test_style_inference.py`——M8 反馈窗口 → M2 风格折叠链（信号归属 M8，写入归 M2）。

## Related ADRs

无（表达适配为确定性规则域；存储纪律遵循 ADR-0002 / ADR-0004，见 [student-model.md](student-model.md)）。
