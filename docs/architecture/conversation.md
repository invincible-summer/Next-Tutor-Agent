# conversation — 对话内核（M1 Supervisor + GSSC + SSE + Workspace）

本文档描述一轮对话的完整生命周期：从用户消息进入 `POST /chat/stream`，经 M1 Supervisor 八步管线编排、GSSC 上下文工程组装，到 SSE 事件流式返回与持久化；工作区（Workspace）作为"对话发生的容器"在此一并描述。

## Purpose / Scope（职责与边界）

- M1 任务智能（Supervisor）：这一轮对话怎么完成——理解 → 规划 → 工具执行 → 状态更新。
- 上下文工程（GSSC）：session/transcript 双存储、L1-L3 三层骨架、有损压缩与 JIT 找回、token 预算。
- SSE 契约：事件类型、会话绑定双保险、流式韧性。
- 回答语言策略：`output_language`（auto/zh/en）。
- 工作区系统：跨对话共享教材与公共记忆的容器；对话内上传与资料引用的可见性边界。
- 不负责：各智能层（M2-M10）内部逻辑（Supervisor 只经读写钩子集成）；RAG 检索与教材库本身；quiz 判分 API（`/quiz/*` 在题卡侧，但题卡事件经本管线 SSE 通道下发）。身份与请求鉴权见 [identity.md](./identity.md)。

## Owned code（拥有的代码路径）

`services/api/app/` 下：

| 路径 | 职责 |
|------|------|
| `agents/supervisor.py` | V2 编排器 `run`（八步管线、智能层读写钩子 3b-3h / 6b-6g） |
| `agents/chat_agent.py` | V1 legacy `chat_turn` 路径 + `run_turn` 按 `SUPERVISOR_MODE` 分发与错误分类 |
| `agents/task_understanding.py` | 四层任务理解回退 + `search_queries` 清洗 |
| `agents/planner.py` | hybrid 规划器（规则 fast path + LLM planner + validator） |
| `agents/router.py` | 能力 → 工具子集路由（M10 Registry 投影） |
| `agents/executor.py` | ReAct 执行循环 + 护栏全集（R1-R4/R10/R13、auto_invoke、思考预算） |
| `agents/state.py` | 跨轮 TaskState（current_goal/completed/remaining） |
| `agents/preresearch.py` | R10 确定性预检索统一判定源 |
| `agents/reasoning_live.py` | `LiveThinkingGate`（实时思考流门控） |
| `agents/reasoning_narrator.py` / `agents/reasoning_summarizer.py` | 过程叙事多阶段摘要 / 公开 reasoning_summary |
| `agents/pseudo_tool_guard.py` | 文字模拟工具调用防护 |
| `core/session.py` | 会话 CRUD、急切 id、路径防护（`_resolve`） |
| `core/context.py` | GSSC：`build_context` 组装、`estimate_tokens`、transcript append |
| `core/context_budget.py` | 按完整协议消息的输入预算核算与压缩决策 |
| `core/context_telemetry.py` | 上下文/用量遥测聚合（`GET /evaluation/context-budget`） |
| `core/message_protocol.py` | assistant tool_call + tool result 标准消息影子（native/legacy 自动回退） |
| `core/tool_context.py` | 工具语义的模型上下文投影 |
| `core/session_learning_card.py` | SessionLearningCard/OpenLoop 有界回合状态卡 |
| `core/llm_runtime/` | `ProviderCapabilities` 与阶段化 `ReasoningPolicy`（capabilities.py / reasoning.py） |
| `core/workspace.py` / `core/workspace_memory.py` | 工作区存储与 public_memory 更新 |
| `api/v1/chat.py` | `/chat/*` 端点（stream/upload/ocr/sessions/attach_library/download） |
| `api/v1/workspace.py` | `/workspaces/*` 端点（11 个） |
| `api/v1/sidebar.py` | `GET /sidebar` 组合快照（会话+工作区+详情，ETag/304） |
| `prompts/tutor.py` + `prompts/registry.py` | 四层 Prompt 控制与版本化注册表 |

## Public contracts（对外契约：API 端点/SSE/WS/数据结构）

### REST（前缀 `/api/v1`）

- `POST /chat/stream`（SSE；body 可带 `workspace_id`、`grade` 默认 `""`=自动）
- `POST /chat/upload`（同样接受 `workspace_id`，新建 session 即时绑定）、`POST /chat/ocr`
- `GET /chat/sessions`（仅本人；游客恒空）、`GET/PATCH/DELETE /chat/sessions/{id}`（PATCH 支持 `{title?, grade?}` 会话内切学段；GET 支持 `?tail=N` 渐进加载）
- `GET /chat/sessions/{sid}/files/{fid}/download`、`POST /chat/sessions/{sid}/attach_library`（`{file_ids}` 复制进会话；`sid=new` 先建会话）
- `GET /sidebar`（组合快照）
- 工作区：`GET/POST /workspaces`、`GET/PATCH/DELETE /workspaces/{id}`（PATCH：改名 + 调整来源 `{name?, folder_ids?, file_ids?}`）、`POST/DELETE /workspaces/{id}/sessions[/{sid}]`、`POST /workspaces/{id}/upload`、`DELETE /workspaces/{id}/files/{fid}`（三分支：专属夹文件删库 / 散选取消选入 / 他区文件 400）

### SSE 事件（`POST /chat/stream`；`POST /quiz/grade` 复用同一通道形态）

| 事件 | 时机 | 载荷要点 |
|------|------|---------|
| `step` | 管线阶段推进 | planning / executing 等 |
| `thinking` | 过程思考增量 | `summary:true` 模板/过程摘要；`summary:false` Provider `reasoning_content` 实时直播（不落盘、不进上下文、不 TTS） |
| `answer` | 回答增量 | token delta |
| `tool_start` / `tool_result` | 工具调用与返回 | name / result（quiz payload 在此） |
| `tool_warning` / `tool_progress` | 反射器/熔断告警 / 工具内进度 | message / 进度 |
| `retry` | 退避重试 | attempt / wait |
| `history_saved` | 历史落盘备份 | session_id |
| `done` | 轮次结束 | answer/thinking 全文 + lite tool_calls + trace_id，**stamp session_id** |
| `error` | 失败 | 错误码 + 恢复建议 |
| `heartbeat` | 保活 | 15s 间隔 |

**会话绑定契约（双保险）**：后端 `chat.py event_stream` 在转发 `done` 前 stamp `session_id`，再发 `history_saved` 备份；前端 `done` 分支不 early-return。违背会导致每轮新建会话、多轮记忆失效。

### 数据结构

- `TutorSession`：会话工作集（messages、`supervisor_state`、`compaction`、`context_card`、`output_language`、`learning_summary` 等）。
- `Workspace`：`ws_<ts>_<slug>`；字段含 `library_folder_id`（专属夹）、`selected_file_ids`（仅接受已注册教材）、`workspace_file_ids`（工作区自有共享上传）、`public_memory`（7 字段结构化摘要）。
- `output_language`：`auto`（默认，不注入指令）/ `zh` / `en`（preamble 强制指令，`forced=True`），持久化到 session，恢复对话保留；翻译练习例外按目标语言产出。

## State & storage（状态与存储布局，含 runtime data 路径）

均在数据根 `NEXT_TUTOR_DATA_DIR` 下（ADR-0002/0004）：

| 路径 | 内容 | 粒度 |
|------|------|------|
| `chat_history/<id>.session.json` | 会话工作集（含 supervisor_state/compaction/output_language） | 会话（带 student_id 戳） |
| `chat_history/<id>.transcript.jsonl` | append-only 全量黑匣，永不裁剪，`recall_history` 检索源；另含【出题记录】/【作答记录】系统条目 | 会话 |
| `chat_history/workspaces/ws_<ts>_<slug>.json` | 工作区（public_memory/selected_*/workspace_file_ids） | 账号 |
| `chat_history/workspaces/uploads/` | 工作区共享资料上传目录 | 账号 |
| `uploads/` | 会话上传解析文本（`<id>.txt`）+ 原件；删除会话级联清理 | 会话 |

## Main flows（关键流程）

### Supervisor 八步管线（`supervisor.run`，逐事件 yield SSE）

1. **understand**：`task_understanding` 结构化理解（intent/subject/concept/requires_tools + response_format/allow_followup_assessment + `structured_quiz_request` + `search_queries`）；模糊新题语义与理解器同一次低预算 LLM 调用识别。
2. **snapshot**：`derive_snapshot(session)` 派生轻量 StudentSnapshot，不臆造数值化掌握度。
3. **skill decision**：M10 构建 TaskFrame，确定性检查 Skill 前置条件，候选/拒绝原因入 Trace。
4. **plan + strategy alignment + gate**：`planner.make_plan` 产出 TaskPlan（步骤携带 `skill_ids`）；M3 策略的 `next_check` 由 M10 转成可执行的收尾检测；gated 模式硬门（移除不满足前置的 Skill、收窄歧义、缺参考题改最小澄清计划）。
5. **context assembly**：GSSC 组装 preamble（`[当前教材]` 块 + 学段细则/自动轻约束 + 语言 + 资料清单）、各层软指令与本轮 Skill Card。
6. **execute**：`executor.execute` ReAct 循环（原生 function-calling），逐事件转发并记录 Skill 后置条件。
7. **persist + update**：写 assistant 消息、更新 TaskState、持久化、更新工作区公共记忆，然后跑写侧钩子 6b-6g。
8. **done**：最终事件（含 trace_id）。

任务理解四层回退：0) 待答上下文确定性匹配（显式选项点选 → `goal=answer_pending` 空计划）；1) 规则短路（寒暄直答）；2) 关键词+正则；3) LLM 兜底（300 token，失败回退规则）。

### 执行器护栏

`MAX_STEPS=6` 硬顶、重复调用防护；R1 参数校验、R2 结果反射器、R3 per-tool 熔断（连续 3 次失败禁用、60s 半开）、R4 截断落盘（>2000 字溢出持久化 tool_spill）、R13 错误恢复建议注入；`_lite_tool_calls` 瘦身回传；M10 后置条件验证写 Trace；教学策略明确要求且参数确定时的 `skill_plan_auto_invoke` 必执行兜底。R10 预检索：本轮上传/引用教材/明确"根据这份资料"必须先检索，当前轮附件定界 file_id，命中注入"严格基于原文"，未命中注入"如实告知"；`search_queries`（LLM 精炼 1-3 条检索词）优先占据查询变体槽位。

### GSSC 上下文工程

- L1 系统红线（不可压缩）→ L2 preamble（半稳定：教材块 → 学段适配 → 语言 → 文件清单 → 附件提醒 → 工作区公共记忆块）→ L3 历史（摘要 + 最近 N 轮 + todo_recap 置顶）。
- 预算：`core/context_budget.py` 按完整协议消息（含 Tool Message 的 call id/参数/result）在每次 Provider 调用前核算；工具结果挤占输出空间时自动下调 `max_tokens`，侵占答案保留区时优先关闭 thinking。压缩保留最近 `CONTEXT_RECENT_FULL_TURNS` 个完整回合、切点在保留区首个 user 之前；二次压缩合并既有摘要；压缩输入注入 `quiz_digest_for_session` 确定性出题/作答摘要（题目 payload 从不进消息文本）。
- 思考流：公开 `reasoning_summary` 持久化；`REASONING_LIVE_MAX_CHARS=-1` 时 `reasoning_content` 以 `thinking`（`is_delta:true, summary:false`）实时直播——不进 LLM 上下文、不写历史、不进 TTS；原始 CoT 全文绝不持久化。`finish_reason=length`/空答案时关闭 thinking 二次续写（`incomplete_answer_recovery`，续写指令禁止复述前文）。
- JIT 找回：`recall_history` 对 transcript 做 BM25 并跨会话检索（该生最近 8 个其它会话 transcript 尾部各 600 行，命中标注来源会话），有损压缩可恢复。
- 会话生命周期：首轮急切分配 `chat_<datetime>_<slug>`，一个窗口 = 一条历史；`round_count` = assistant 消息数；load/delete/rename 均经 `_resolve` 防路径遍历。

### 工作区（对话的容器）

- 来源：`selected_file_ids` 仅接受已注册教材（自有或公用，经 `resolve_textbook_file` 解析；教材组展开为有序卷级 file_id）；`workspace_file_ids` 为本区上传的共享资料（非全局教材，同区所有会话可检索）。`readable_stores()` 运行时拼装检索 overlay；`merged_knowledge_files()` 统一合并元数据供 snapshot/planner/router/preamble；`turn_start` trace 带 `visible_file_ids`；`material_sources()` 产出稳定只读来源清单（`source_scope=session/library/workspace/workspace_textbook`）。
- public_memory：每轮后 LLM 摘要更新（7 字段），注入 L2 preamble、不占 session 预算；重载合并防竞态；与检索原文冲突时以原文为准。
- 绑定双通道：先发消息（`/chat/stream` 带 `workspace_id`）与先传附件（`/chat/upload` 同样接受）都即时绑定，不产生孤儿 session。
- 可见性边界：对话内上传仅属该会话；「引用资料」多选复制进当前会话（不改动资料库、不跨对话）；跨对话可见的只有工作区自有上传与显式选中的教材。

## Dependencies（依赖与被依赖）

- 依赖：[identity.md](./identity.md)（`resolve_student_id`）；M2-M10 读写钩子（全部 try/except，失败只记 trace，绝不影响对话流）；`knowledge_search`/`generate_quiz`/`fit_quiz`/`recall_history` 工具（M5/M4/M6 域）；资料库/教材库（`resolve_textbook_file`）；LLM Provider（OpenAI 兼容）。
- 被依赖：前端聊天页与侧边栏（SSE + `/sidebar`）；quiz 题卡（payload 经 `tool_result` 下发）；语音通话轮次复用同一消息流；站点助手/笔记等领域的对话入口复用执行器。

## Invariants / security boundaries（不变量与安全边界）

- 智能层钩子失败只记 trace，任一层关闭/异常不得破坏对话流（开关总表见 [backend-runtime.md](./backend-runtime.md)）。
- 原始 CoT 不持久化、不进语音链路（voice WS 显式丢弃 thinking 分支）；持久化的只有公开摘要。
- 题目 payload 从不进入压缩消息文本；判分只在 `/quiz/*` 题卡 API。
- 会话/工作区按 owner 物理分文件；外人 404 不泄露存在性；游客恒空列表。
- transcript append-only 永不裁剪；session/workspaces JSON 全部原子写。
- `response_format`/`allow_followup_assessment`（"一句话/不要出题"）在 plan gate 后二次校验并 Prompt 尾部重申，教学策略不得覆盖学生显式输出契约。

## Configuration（环境变量与开关）

| 变量 | 默认 | 说明 |
|------|------|------|
| `SUPERVISOR_MODE` | `v2` | `legacy` 走 V1 `chat_turn`；v2 异常显式报错，仅 `SUPERVISOR_LEGACY_FALLBACK=1` 时回退 |
| `AGENT_MAX_STEPS` | `6` | ReAct 步数硬顶 |
| `EXECUTOR_TOOL_THINKING` | `1` | 工具步保留 LOW 思考；预算被压缩到答案保留区下仍强制关闭 |
| `EXECUTOR_TOOL_MAX_OUTPUT_TOKENS` | `6000` | 工具步输出信封 |
| `REASONING_LIVE_MAX_CHARS` | `-1` | `-1` 全量实时思考流；`0` 恢复隐藏 CoT；`>0` 只直播前 N 字 |
| `LLM_CONTEXT_WINDOW` | `165536` | 输入窗口（预算核算基准） |
| `LLM_MAX_OUTPUT_TOKENS` | `20000` | 单次输出上限 |
| `LLM_CONTEXT_SAFETY_MARGIN` | `2500` | 输出安全区 |
| `CONTEXT_SOFT_TRIGGER_RATIO` / `CONTEXT_HARD_TRIGGER_RATIO` | `0.72` / `0.88` | 压缩触发阈值 |
| `CONTEXT_HISTORY_MAX_TOKENS` | `30000` | L3 历史封顶 |
| `CONTEXT_RECENT_FULL_TURNS` | `4` | 压缩保留的完整回合数 |
| `TOOL_MESSAGE_MODE` | `native` | assistant tool_calls + tool result；Provider 400 自动转 legacy 重试 |
| `TOOL_CONTEXT_PROJECTION_MODE` | `on` | 工具语义投影进模型上下文 |
| `CROSS_SESSION_MEMORY` | `workspace` | transcript 详细跨会话召回范围 |

## Observability（trace/日志/指标）

- 每轮 trace：understanding/TaskFrame/Skill 候选与拒绝原因/plan/tool 调用/后置条件/`grounding_reason`/`grounding_file_ids`/`query_source`；查询接口 `GET /trace/{run_id}[/html]`（详见 [backend-runtime.md](./backend-runtime.md)）。
- `core/context_telemetry.py` 按 trace_id 聚合 context_budget、llm_usage、双通道 token、compaction、tool projection 与 fallback，`GET /evaluation/context-budget` 投影到系统洞察页（只返回统计，不返回 Prompt/正文/隐藏 reasoning）。
- `tool_warning`/`retry` SSE 事件面向用户可见；`incomplete_answer_recovery` 等护栏动作记 trace。

## Tests / acceptance（测试索引）

`services/api/tests/` 下：

- 管线与分发：`test_supervisor_v2_compat.py`、`test_supervisor_hooks.py`、`test_chat_quiz_intent.py`、`test_task_understanding_context.py`、`test_pseudo_tool_guard.py`、`test_directive_arbitration.py`
- 执行器：`test_circuit_breaker.py`、`test_preresearch.py`、`test_search_query_planning.py`
- 上下文工程：`test_context_budget.py`、`test_reasoning_live_stream.py`、`test_recall_history.py`、`test_cross_session_memory.py`、`test_session_summary.py`、`test_dialogue_checkpoint.py`
- Prompt：`test_prompt_registry.py`、`test_prompt_eval.py`
- 会话与上传：`test_session_isolation.py`、`test_chat_upload_pipeline.py`、`test_attach_library.py`、`test_upload_limits.py`、`test_multimodal_uploads.py`、`test_sidebar_api.py`
- 工作区：`test_workspace_isolation.py`、`test_workspace_files.py`、`test_workspace_knowledge.py`、`test_workspace_shared_materials.py`、`test_workspace_sources.py`、`test_workspace_cat_grounding.py`
- 题卡契约：`test_quiz_card_contract.py`

## Related ADRs

- ADR-0004 JSON 持久层 single-worker 不变量（session/transcript/workspace 原子写与并发约束）
- ADR-0002 运行数据统一 `NEXT_TUTOR_DATA_DIR`
- ADR-0003 BM25 基线 + 向量可选（`recall_history`/检索轨）
