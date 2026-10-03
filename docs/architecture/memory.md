# M6 记忆智能（Memory Intelligence）

回答「系统该记住这个学生的什么、记多久」：管理有界的跨会话 prompt memory、程序/习惯聚合与旧情景记忆的只读兼容层，并把「进 prompt 的记忆」收敛到少数几类稳定画像事实。

## Purpose / Scope

- 活动 prompt memory：`students/<id>.prompt_memory.json`，只允许总体学习情况、当前水平、语气偏好、讲解偏好进入 prompt；普通对话与工作区对话按用户全局统一计数。
- 最近窗口与 core profile 双层结构：窗口内贡献按 `session_id` 独立可撤销，滑出窗口后合入不可拆分的整体画像并执行 LLM 压缩与硬字符上限。
- 程序性记忆（策略成功率）与学习习惯聚合：有界结构化状态，不保存对话正文或具体教学内容。
- 旧数据兼容：episodic/semantic 存储只读展示与审计；生产回合不再追加详细 episodic、不再运行 semantic consolidation、不直接注入 prompt。
- 不负责：题目/作答/判分证据（统一学习证据 journal，[student-model.md](student-model.md)）；工作区公共记忆归工作区系统（`Workspace.public_memory` 仅同工作区可见，与用户级 prompt memory 隔离）。

## Owned code

`services/api/app/agents/memory/`（12 个 Python 文件）：

- `__init__.py`——模块契约与导出（`MemoryService` / `get_memory_service` / `is_enabled`）。
- `manager.py`——`MemoryService`：`build_directive`（读钩子）、`consume_turn`（写钩子，作答/策略事件入账）、`retrieve`（兼容检索）、计数查询。
- `prompt_memory.py`——活动 prompt memory 全部逻辑：策略读写（`get_policy`/`set_user_window`，默认 15、可选 5–30）、`register_session`/`record_contribution`、`forget_session_contribution`、`_fold_into_core`/`_trim_overflow`、`build_directive`（`[提示词记忆·精简画像]`）、`public_view`。
- `procedural.py`——`students/<id>.procedural.json`：per-student 策略成功率（`record_outcome` / `injectable_strategies`）。
- `habit_pattern.py`——M9 习惯事件聚合（`consolidate_habit_events` / `read_habit_patterns`，产出 `SemanticFact` 结构，不保存正文）。
- `episodic.py` / `semantic.py`——旧情景/语义存储的只读兼容读取（含 `add_or_consolidate` 仅服务于习惯聚合回退与兼容投影）。
- `retrieval.py`——跨存储 BM25 检索（时间衰减 + scope 优先级，服务兼容审计视图）。
- `classifier.py` / `context_builder.py`——记忆分类与上下文块组装。
- `schema.py` / `store.py`——数据类与存储路径绑定。
- 集成侧：`app/api/v1/memory.py`（记忆 API）、`app/api/v1/workspace.py` / `orchestration.py` / `knowledge.py` / `admin.py`（读记忆投影或账号级联清理时引用）。

## Public contracts

- API（前缀 `/api/v1`，`app/api/v1/memory.py`）：
  - `GET /memory/episodes`、`GET /memory/semantic`、`GET /memory/procedural`——旧存储与聚合的只读视图（审计/展示）。
  - `GET /memory/prompt-profile`——当前 prompt memory 公开视图（窗口、core profile、撤销状态）。
  - `PUT /memory/prompt-profile/window`——用户自选窗口大小（5–30）。
  - `GET /memory/prompt-profile/sessions/{session_id}`——单会话贡献归属状态（`active` / `compacted` / `forgotten` / `legacy_unknown`）。
- supervisor 钩子契约：读钩子 `_memory_directive_for_turn`（3e）返回 `[提示词记忆·精简画像]` 软指令（总体学习情况/水平/语气/讲解偏好）；写钩子 `_memory_consolidate_turn`（6d）消费本轮信号；M9 `EventEmitter` 事件经 `consume_turn` 入账（连击/进度白名单 + 去重）。
- 删除语义契约：归档对话时可选「永久遗忘提示词影响」（默认不选）；窗口内 contribution 可立即永久移除且恢复对话不重建；归档中心永久删除或到期清扫自动移除可单项归属的最近贡献；`compacted_session_ids` 仅保存会话身份归属，已压缩进整体画像的内容无法安全反向拆分（UI 明示）；旧版仅有压缩总数的数据标 `legacy_unknown`，不伪造精确归属。

## State & storage

| 路径 | 内容 |
|------|------|
| `students/<id>.prompt_memory.json` / `.prompt_memory_pref.json` | M6 精简提示词画像 + 最近会话窗口偏好 |
| `students/<id>.procedural.json` / `.habit_patterns.json` | 有界策略成功率 / 学习习惯聚合 |
| `students/<id>.episodes.jsonl` / `.episodes_archive.jsonl` / `.semantic.json` | 旧情景/语义兼容审计（生产只读；写侧已删，仅聚合回退与审计 Tab 在读） |

- 首次读取 procedural/habit 允许从旧 `.semantic.json` 兼容投影；后续活动写入不再改写旧语义文件。
- 独立学习档案 `students/<id>.learning_evidence.jsonl` 与 prompt memory 分离：来源对话删除后证据保留（按删除语义归档），归 [student-model.md](student-model.md) 拥有。

## Main flows

1. **读钩子（3e）**：每轮 supervisor 调 `build_directive` 渲染 `[提示词记忆·精简画像]` 块（有内容才注入）；`MEMORY_INTELLIGENCE_MODE=0` 时返回空。
2. **写钩子（6d）**：回合结束 `_memory_consolidate_turn` 记录本会话 prompt contribution（按 `session_id` 归属）+ 折叠策略结果进 procedural + M9 习惯事件聚合；学生事件链已删除（作答结论已归 journal），只收作答事件（compat 空）。
3. **作答事件旁路**：`core/quiz_attempts.py::record_quiz_attempt` 在判分后把 `quiz_graded` 事件经 `consume_turn` 送入 M6 episodic 兼容层（unknown 判定整体跳过）；作答结论本体由统一受理链写 journal。
4. **窗口滚动**：超出窗口（默认 15 会话）的 contribution 经 `_fold_into_core` 合入整体画像，`_trim_overflow` 执行硬字符上限；整体 LLM 压缩频控运行。
5. **工作区公共记忆**：`Workspace.public_memory` 新会话边界整体压缩一次、仅同工作区会话读取、单聊删除不回退、随工作区 bundle 归档/恢复/永久删除（归工作区系统，非本模块状态）。
6. **跨会话召回边界**：transcript/详细跨会话召回由 `CROSS_SESSION_MEMORY` 控制（`tools/recall_history.py`）：默认 `workspace`（同工作区最近 8 个会话尾部各 600 行）、`all` 恢复该生全部会话、`off` 关闭——与 prompt memory 是两条独立通道。

## Dependencies

- 上游：M1 supervisor（3e/6d 钩子）、M9 EventEmitter（事件流）、判分端点（`record_quiz_attempt` 旁路）。
- 只读消费方：M7（procedural per-student 聚合的对照面）、M8（motivation 连续天数读 M6，见 [ux.md](ux.md)）、前端记忆审计 Tab 与 `/memory/*`。
- 基础设施：`core/atomic.py` 原子写、`core/retriever.py` BM25（retrieval.py 复用）、`core/config.py::settings.cross_session_memory`。
- 不拥有：学习证据（M2）、教学日志（[teaching-engine.md](teaching-engine.md)）、UX 画像（[ux.md](ux.md)）。

## Invariants / security boundaries

- 只有总体水平/学习概况/语气/讲解偏好进入 prompt；对话正文、题目内容、能力数值一律不入。
- 窗口内可按会话撤销；压缩进 core profile 的内容不可反向拆分——删除语义宁可标记 `legacy_unknown` 也不伪造归属。
- 旧 episodic/semantic 只读：生产回合不追加、不 consolidation、不进 prompt。
- procedural/habit 有界：结构化聚合无正文；procedural 记 per-student 策略成功率，与 M7 的系统级策略聚合（[evaluation.md](evaluation.md)）互不复制原始数据。
- 用户级与工作区级记忆隔离：prompt memory 全局统一计数；public_memory 始终只在同一工作区可见。
- 所有钩子包 try/except，失败只记 trace 不影响对话流（统一护栏原则）；按 student_id 隔离，`resolve_student_id()` 是唯一可信标识。

## Configuration

| 环境变量 | 默认 | 语义 |
|------|------|------|
| `MEMORY_INTELLIGENCE_MODE` | `1` | `0` 关闭 M6：精简画像指令返回空、写侧聚合 no-op |
| `CROSS_SESSION_MEMORY` | `workspace` | transcript 跨会话召回范围：`workspace` / `all` / `off`（不影响 prompt memory） |

- prompt 窗口默认 15、用户可选 5–30（`PUT /memory/prompt-profile/window`，持久化于 `.prompt_memory_pref.json`）。

## Observability

- trace 事件：`memory_consolidate_hook_error` 等失败记录；读钩子注入内容计入 `adaptation_recap`。
- `GET /memory/prompt-profile` 与 sessions 归属状态供前端明示撤销边界；episodes/semantic/procedural 端点为审计视图。

## Tests / acceptance

`services/api/tests/`：

- `test_memory.py`——服务面、检索、计数与降级。
- `test_prompt_memory_lifecycle.py`——窗口滚动、core 折叠、撤销/遗忘语义、`legacy_unknown`。
- `test_cross_session_memory.py`——prompt memory 跨普通/工作区对话统一计数与召回边界。
- `test_supervisor_hooks.py`——6d 写钩子不静默失败（沙箱钉死存储根）。
- `test_projection_api.py`——`/memory/*` API 面。

## Related ADRs

- ADR-0004 JSON 持久层 single-worker
- ADR-0002 运行数据统一 NEXT_TUTOR_DATA_DIR
