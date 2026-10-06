# Notes（笔记仓库与笔记智能体 · M-Notes）

> 一句话职责：把学习痕迹沉淀为可长期温习的个人知识库——每用户一个 Obsidian 式 Markdown 仓库 + 每笔记专属笔记智能体（问答/计划/授权三模式），并与学习编排（温故复习卡）深度联动。

## Purpose / Scope

- 独立子系统：不进 M1 Supervisor 链路、不复用 chat 页面与提示词。回答「学习痕迹如何沉淀为可长期温习的个人知识库」。
- 三部分能力：笔记仓库（文件夹树、wiki 链接、标签、修订历史、导出）、笔记智能体（`/notes/generate` 来源化生成 + `/notes/chat/stream` 每笔记专属 ReAct-lite 对话）、M9 深度同步（学习温故复习卡）。
- 前端为 `/notes` 三栏页面（侧栏 | 编辑/预览/图谱 | AI 面板）；本模块拥有其数据契约与 SSE 事件面。

## Owned code

- `services/api/app/notes/__init__.py`：仓库存储（vault 索引、正文、修订、智能体状态、资源链接解析、关系图现算）。
- `services/api/app/notes/templates.py`：内置模板骨架与自定义模板。
- `services/api/app/agents/notes_agent.py`：智能体（来源组装、生成管线、对话循环、工具矩阵、计划批复状态机）。
- `services/api/app/api/v1/notes.py`：全部路由。
- 前端 `apps/web/src/` 的 `/notes/[[...noteId]]` 页面与笔记组件（消费本模块契约）。

## Public contracts

**REST API**（前缀 `/api/v1/notes`，身份一律 `resolve_student_id()`，越权 404）：

- 仓库：`GET /vault`（含 stats：链接数/未解析/到期温故）、`GET /search`、`GET /graph`、`GET /reviews/due`（只回 `note:` 前缀到期卡并 join 笔记元数据）。
- 笔记 CRUD：`POST /notes`（限流 30/min）、`GET /notes/{id}`、`PUT /notes/{id}`（乐观并发：携带 `base_revision`，不匹配返回 409 + 服务器最新内容）、`PATCH /notes/{id}`（改名/移动/标签/温故开关）、`DELETE /notes/{id}`（经 trash）；`POST /bulk/move`、`POST /bulk/delete`（统一进回收站）。
- 修订：`GET /notes/{id}/revisions`、`GET /notes/{id}/revisions/{revision}`、`POST /notes/{id}/revisions/{revision}/restore`。
- 智能体：`GET|PATCH|DELETE /notes/{id}/agent`（模式/历史/待批复计划；PATCH 枚举校验，未知值 422）。
- 温故：`POST /notes/{id}/review`（`quality ∈ {5 记得, 3 模糊, 1 忘了}`）。
- 组织与导出：folders CRUD（含 `parent_id`、安全上移删除、循环校验）、templates CRUD（自定义 ≤20）、`GET /notes/{id}/export`（.md + YAML frontmatter）、`GET /export?folder_id=`（zip）。
- SSE：`POST /generate`（限流 6/min）、`POST /chat/stream`（限流 30/min）、`POST /upload`（限流 20/min，图片 OCR/文档提取，存笔记侧 uploads）。

**SSE 事件词汇**（沿用 chat 惯例）：`run_start / step / thinking{is_delta} / answer{is_delta} / retry / tool_start / tool_result / note_updated{note_id,title,content,revision,summary} / mode_changed / plan_card / run_end / error / done`。事件只暴露阶段与工具摘要，原始思维链不落盘；live 思考直播（`REASONING_LIVE_MAX_CHARS` 门控）仅作显示流发给前端，不进对话历史。

**智能体三模式与工具矩阵**（`normalize_agent_mode` 把旧值 suggest/collab/cowrite/auto 映射为 plan/plan/authorize/authorize）：

| mode | 工具 | 边界 |
|---|---|---|
| `ask` | `notes_search` / `notes_read` / `knowledge_search` | 只读问答 |
| `plan` | 同 ask | 不能修改任何笔记，只能产出计划卡 |
| `authorize` | 另有 `notes_write` | 工具实例绑定当前笔记（`allowed_note_id` 不匹配直接拒绝——不能新建笔记、不能修改其他笔记） |

`knowledge_search` 三模式统一装配（只读，不改变模式边界）：语料 = 当前笔记 source（生成时溯源）+ 笔记页上传附件（向量 scope `notes:<sid>`），两者皆空时回退学生可选教材（封顶 12 个文件）。`knowledge_search` 一旦参与修改，证据标准化为去重的简短 `[知识卡]`（来源位置 + 1–2 句摘要 + 隐藏指纹元数据），禁止将完整 RAG 原文写入正文。

## State & storage

双模式路由（ADR-0017，notes 域）：`DOMAIN_DOCUMENT_BACKENDS` 含 `notes=sql` 且配置 `DATABASE_URL` 时，vault 索引 / 笔记正文 / 修订（聚合为单文档，行锁 mutate 内追加+裁剪）/ 每笔记智能体状态写入 PostgreSQL `notes_documents` 表（`app/notes/sql_store.py`，sync bridge 访问；`threads/` 遗留懒迁移只在文件模式执行）；上传原件/提取文本仍走文件/ObjectStore。导入：`import_documents.py --domain notes`（幂等 + `--verify`）。否则走下列文件布局，行为不变。

存储根 `notes/<safe_sid>/`（统一运行数据根下；`.gitignore` 以 `/notes/` 根锚定）：

- `vault.json`：仓库索引（folders / notes 元数据 / custom_templates）。folder 用 `parent_id` 组成多层树；笔记元数据（标题、文件夹、标签 ≤12、template_id、status、source 溯源、review 调度镜像、revision 版本号）只存索引；首次访问播种四个默认文件夹（错题修正/知识点总结/学习温故/章节笔记）。
- `notes/<note_id>.md`：笔记正文（纯内容）；导出时拼 YAML frontmatter（Obsidian 可直接导入）。id 形如 `note_<YYYYmmdd_HHMMSS>_<slug>`，路径经 `Path(name).name` 防穿越。
- `revisions/<note_id>/`：修订快照（`{rev:04d}_{ts}_{author}.md`，每笔记上限 20）；每次写入（用户保存 / Agent 直写 / 版本恢复）都追加快照，author 区分 user/agent。
- `agent/<note_id>.json`：每笔记专属智能体状态——mode、messages 对话历史（上限 200 截头，注入最近 12 条）、pending_plan、working。`note_id` 为空的仓库级对话存 `agent/_vault.json`；删除/归档笔记时同步清理（`delete_agent_history`）。
- `threads/`：遗留只读。首次加载某笔记历史时按消息 `context.note_id` 一次性懒迁移进 `agent/<note_id>.json`，之后不再写入；`conversation://notes/<id>` 旧链接按缺失展示。
- `uploads/`：笔记侧图片附件（原件 + OCR/提取文本）。
- 回收站：`notes_note` bundle（`archive_note`：note.json + content.md + revisions 快照；温故卡与该笔记智能体状态随归档摘除；restore 反向重建；purge 幂等清理）。

## Main flows

**写入契约（乐观并发）**：`write_note` 在 `file_lock` 内完成校验 → 写正文 → 快照 → bump revision → 存索引。`PUT /notes/{id}` 携带 `base_revision`，不匹配返回 409 + 服务器最新内容。重命名笔记默认全仓库改写指向旧标题的 wiki 链接（被改笔记同样留修订快照，历史不跳号）。智能体写入同样携带轮首版本快照 `base_revision`：学生在智能体运行期间保存的编辑触发 `StaleRevisionError`，返回「先 notes_read 重读再重试」而不是静默覆盖；成功写入 emit `note_updated`，前端热更新编辑器。

**统一资源链接与标签**：`[[标题]]` / `[[标题|别名]]` 按标题解析（重名取最近更新），并支持稳定 ID：`note://<note_id>`、`conversation://session/<session_id>`。解析结果携带资源类型、状态（resolved/missing/deleted）与摘要；删除目标不改写历史正文；解析不到的 wiki 链接是关系图幽灵节点。链接/标签扫描前剔除代码块与行内代码；反向链接、链接图、vault 概览由正文现算（进程内 mtime 缓存）。关系图节点覆盖笔记 / 被引用的普通会话 / 教材 / 幽灵：会话经 `conversation://session/...` 引入，教材从笔记 `source.textbook_ids` 派生 note→textbook 边（教材索引不可用时静默降级）；笔记助手线程不进图。

**生成管线**（`POST /notes/generate`）：来源三形态（`sources.source_mode`）——`sessions`（教材/文件经 `material_sources` 从所选对话自动推导）、`workspace`（`session_ids` 为限定子集，空 = 整个工作区）、`textbooks`（直接对教材写笔记）。管线 = 来源组装（会话优先 compaction 摘要、教材 outline+概念、工作区 public_memory、错题本；每类独立字符预算超限截断标注）→ 真实 RAG（`_build_retrieval_corpus` 聚合语料为 BM25 overlay + 向量 scoped stores；`notes_retrieval_queries` 一次小调用生成检索查询、失败降级确定性查询；`KnowledgeSearchTool` 与 chat 同一条混合检索 + 证据门路径）→ `notes_generator_system` 流式生成（含仓库概览供 `[[链接]]`；用户补充要求最高优先；多模态：RAG 图表证据页快照 + 所选会话图片附件 ≤3 张经主通道多模态识别，未配置时静默降级纯文本）→ 剥开场白/代码围栏 → 存为 draft 笔记（source 记录 source_mode/material_file_ids 溯源）。

**对话循环**（`POST /notes/chat/stream`）：ReAct-lite ≤4 步。计划批复状态机：plan 模式回复尾部的 ```json 计划卡（`{"title","steps":[{title,detail}]}`，解析失败视为普通问答）→ `set_pending_plan` + `plan_card` 事件 → `action="approve_plan"` 仅当 status==pending 才放行：置 approved、持久切换该笔记 mode 为 authorize（emit `mode_changed`）、按 `<approved_plan>` 圈定执行、完成后置 executed；重复批复/无计划/驳回后批复均返回明确 error；`action="reject_plan"` 置 rejected 并停留在 plan 模式（不发 LLM 请求）。异常分支也完整收尾（error + 留痕 + run_end/done）。

**图片附件**：`POST /notes/upload`（图片 OCR / 文档提取，存 notes 侧 uploads）；请求带 `attachments` 时由主通道多模态模型识别（历史持久化仍纯文本，附件只记 id/filename 元数据），未配置降级用消息内 `<ocr_material>` OCR 文本。

**M9 深度同步（学习温故）**：`LearningOrchestrationService` 提供 `upsert_review_card`（幂等；重命名只更新 concept_name 不重置调度）/ `submit_review`（canonical SM-2 + `srs_review` 事件落 `.orchestration_events.jsonl`）/ `remove_review_card`。笔记卡 `concept_id="note:<note_id>"`、`concept_name=笔记标题`，进入 `review_queue` 后自然流入 daily_composer/今日任务（M9 消费端零改动）。笔记侧 `POST /notes/{id}/review` 走 `submit_review` 并把调度字段镜像回索引；删除（trash 归档）摘卡、恢复重注册。

## Dependencies

- `core/atomic.py`（索引原子写）、`core/trash.py`（回收站 bundle）、`core/paths.py`（存储根）。
- 检索：`tools/knowledge_search.py` + `core/hybrid.py` + `core/evidence_gate.py`（与 chat 同一条混合检索 + 证据门路径）、`core/vector_store.py`（`notes:<sid>` scope）。
- `core/llm_async`（主通道多模态模型）；`prompts/registry.py`：`notes_assistant_system@1.3.0`、`notes_generator_system@1.1.0`、`notes_retrieval_queries@1.0.0`（文本改动需 bump 版本）。
- 上游数据源：会话 compaction 摘要、工作区 `public_memory`、教材 outline/概念、错题本 `collect_error_notebook`。
- 下游：M9 `review_queue` / daily_composer（温故卡）；账户删除与孤儿清理覆盖 `notes/` 根。

## Invariants / security boundaries

- 每用户一个 `notes/<safe_sid>/` 仓库，物理隔离；身份只取 `resolve_student_id()`，越权 404。
- 路径安全：note_id 经 `Path(name).name` 归一，禁止穿越。
- 乐观并发是唯一写协议：`base_revision` 不匹配即 409 / `StaleRevisionError`，绝不静默覆盖。
- 智能体能力边界由服务端工具实例强制：`notes_write` 只绑定当前笔记；plan/ask 无写工具；mode/action 枚举 API 层校验（未知值 422，旧值经 `normalize_agent_mode` 映射放行），客户端不能自报越权。
- 原始思维链不落盘；SSE 事件不含笔记正文以外的私有 RAG 原文（证据以 `[知识卡]` 摘要 + 指纹形式注入正文）。
- 删除/归档笔记同步清理智能体状态与温故卡；restore 幂等重建。
- 修订历史不跳号、上限 20；重命名改写 wiki 链接同样留快照。

## Configuration

- `NOTES_AGENT_MODE`（默认开）：`off` 时 CRUD/导出不受影响，两个 SSE 返回降级错误事件。
- 限流（固定窗口）：`notes_create` 30/min、`notes_generate` 6/min、`notes_upload` 20/min、`notes_chat` 30/min。
- 界内常量：对话历史上限 200 条截头（注入最近 12 条）、标签每篇 ≤12、自定义模板 ≤20（`ct_` 前缀）、修订每笔记 ≤20、多模态附件 ≤3 张；`REASONING_LIVE_MAX_CHARS` 控制 live 思考直播。

## Observability

- `GET /vault` stats（链接数/未解析/到期温故）与 `GET /graph` 是健康面；未解析 wiki 链接以幽灵节点显形。
- SSE `step` 事件暴露阶段（来源组装 / retrieving / 生成）；`sources_summary` 含 retrieved 计数；工具活动经 `tool_start/tool_result` 摘要可见。
- 修订历史与 author（user/agent）区分提供完整审计轨迹；`mode_changed` / `plan_card` 事件使批复状态对前端可回放。

## Tests / acceptance

- `test_notes.py`：CRUD、并发 409、修订、wikilink 改写、图、导出、每笔记智能体端点与模式校验、trash 往返、双用户隔离、路径穿越、附件上传路由。
- `test_notes_agent.py`（fake LLM）：生成管线、三形态来源与真实 RAG 命中、检索查询降级、工作区整区展开、三模式工具循环与越权拒绝（authorize 跨笔记与新建拒绝）、计划卡解析与 pending 状态机、批复仅一次 + mode_changed 自动切授权、驳回路径、每笔记历史隔离、旧线程懒迁移、写入冲突重试提示、live 思考不落盘、异常分支完整收尾、knowledge_search 在 ask 模式可用、附件降级、旧值归一化、降级 SSE。
- `test_notes_m9_sync.py`：upsert 幂等、SM-2 规则、生命周期、到期 join。

## Related ADRs

- ADR-0001 source-only——笔记内容全部来自用户运行态数据，仓库不携带演示笔记。
- ADR-0002 运行数据单根——`notes/` 根位于统一运行数据根之下并登记沙箱/删除/孤儿清理。
- ADR-0003 BM25 基线 + 向量可选增强——笔记生成/对话复用同一检索路径，向量轨可选。
- ADR-0004 single-worker——智能体状态与温故卡写回依赖单 worker 进程内互斥。
- ADR-0005 Pages synthetic——演示站点不含笔记数据。
