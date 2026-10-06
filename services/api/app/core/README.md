# core — 后端共享基础设施

FastAPI 后端各领域共用的运行时基建：配置、存储根与路径绑定、原子写、限流、LLM 通道、检索/OCR/向量、会话、trace 与一批跨域共享的存储/工具模块。

## 准入规则（Admission）

`core/` 只接受真正跨领域的 primitive：

- 配置与路径（`config.py` / `paths.py`）；
- 原子写与 JSON 持久化基建（`atomic.py` / `json_utils.py` / `validator.py`）；
- LLM 通道与策略（`llm_async.py` / `llm_policy.py` / `llm_runtime/`）；
- 上下文预算与遥测（`context*.py`）；
- 通用工具协议（`agent_tools.py` / `tool_*.py` / `message_protocol.py` / `execution_policy.py`）；
- 通用限流（`ratelimit.py`）；
- 共享持久化 primitive（会话工作集、trace、uploads、workspace）。

**明确不允许新的领域服务进入 core**：新增领域逻辑应落在 `app/agents/<域>/`、`app/classroom/` 等所属域包。现存于 core 的领域文件（`guest_*`、`quiz_*`、`textbook*`、RAG/检索侧、`evidence_*`、`learner_*` 等）是历史布局，其所有权见各 [architecture 文档](../../../../docs/architecture/README.md)；按「随功能修改逐步迁出、不做为目录而目录的大迁移」的约定处理，guest 的收敛边界另见 site-assistant/identity 架构文档。

全景（物理拓扑、存储布局总表、智能层开关、部署边界）见 [docs/architecture/backend-runtime.md](../../../../docs/architecture/backend-runtime.md)，本 README 只做导航。

## Owns

- 配置与路径：`config.py`（全量环境变量/开关集中读取为 `settings`）、`paths.py`（`RuntimePaths`：`NEXT_TUTOR_DATA_DIR` 单一数据根解析、`bind_storage_path` 绑定与批量重定向，供测试沙箱与 demo 导出器复用）。
- 持久化基建：`atomic.py`（JSON 原子写 + 文件锁，所有 JSON 持久化必经）、`json_utils.py`、`validator.py`。
- LLM 通道：`llm_async.py`（OpenAI 兼容异步客户端，`trust_env=False` 直连、`disable_thinking` 防饿死）、`llm_policy.py`（管理员在线热调）、`llm_runtime/`（Provider 能力档案与 ReasoningPolicy）。
- 会话与运行时服务：`session.py`（会话工作集）、`ratelimit.py`（固定窗口限流）、`trace.py`（每轮 trace JSONL）、`bootstrap.py`、`context.py` / `context_budget.py` / `context_telemetry.py`。
- 检索与知识底座：`retriever.py`、`rag_index.py`、`hybrid.py`（BM25 基线 + 向量可选，ADR-0003）、`vector_store.py`、`vector_jobs.py`、`embedding.py`、`knowledge_store.py`、`structured_chunker.py`、`public_vector_artifact.py`。
- 文件解析与 OCR：`file_parser.py`、`file_summary.py`、`multimodal_parser.py`、`multimodal_context.py`、`ocr.py`、`pdf_ocr.py`、`pdf/`（PDF 引擎门面，ADR-0015）、`ocr_policy.py`、`textbook_ocr.py`、`text_quality.py`。
- 工具协议：`agent_tools.py`、`tool_base.py`、`tool_context.py`、`tool_protocol.py`、`tool_call_compat.py`、`execution_policy.py`、`message_protocol.py`。
- 账号与治理：`account_data.py`（注销级联 purge）、`orphan_cleanup.py`（孤儿数据扫描类别）、`guest_*`（游客策略/运行时/清理/学习）、`trash.py`、`uploads.py`、`workspace.py` / `workspace_memory.py`。
- 其余跨域共享模块：教材管线（`textbook.py`、`textbook_pipeline.py`、`library.py`）、出题配图侧（`quiz_illustration*.py`、`quiz_design.py`、`quiz_grounding.py`、`quiz_attempts.py`、`quiz_submission.py`、`quiz_verify.py`、`quiz_generation_budget.py`）、学习证据与画像消费（`evidence_context.py`、`evidence_gate.py`、`learner_evaluation_policy.py`、`learner_runtime.py`、`learning_episodes.py`、`session_learning_card.py`）、记忆与教学辅助（`memory_safety.py`、`bloom.py`）、使用文档（`usage_docs.py`）、`figure_harvest.py`。课堂/站内助手/笔记的域存储层已迁至所属域（`app/classroom/storage.py`、`app/agents/site_assistant/store.py`、`app/notes/`）。

## Does not own

- 领域编排与智能层（M1–M10）→ `app/agents/`；课堂域编排 → `app/classroom/`。
- API 路由与 schema → `app/api/v1/`、`app/schemas/`；提示词注册 → `app/prompts/registry.py`。
- 身份与鉴权 → `app/identity/`；MeloTTS sidecar → `services/voice/`。

## Design

两条硬规则：一切 JSON 持久化必经 `atomic.py`（tmp+replace，single-worker 不变量，ADR-0004）；新增 per-user 存储根必须经 `core/paths.py::bind_storage_path` 绑定，并在同一变更中登记 `core/orphan_cleanup.py` 的扫描类别。路径防护由 `_resolve` 统一剥目录防遍历。

## Tests

`services/api/tests/core/` 运行时/基建相关（`test_atomic_hardening.py`、`test_deployment_contract.py` 等）；检索/OCR 回归在 `tests/agents/knowledge/`，会话隔离在 `tests/identity/`，各域存储回归在 `tests/classroom/`、`tests/agents/site_assistant/`、`tests/notes/`（目录导览见 [tests/README.md](../../tests/README.md)）。

## Key entry points

- `paths.py` — 数据根所有权与 `bind_storage_path`
- `config.py` — `settings` 全量配置读取
- `atomic.py` — JSON 原子写与文件锁
