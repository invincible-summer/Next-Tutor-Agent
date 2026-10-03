# tests — 后端测试套件

FastAPI 后端的 `unittest` 回归套件：扁平 `test_*.py` 按前缀分域，全部经 `storage_sandbox` 强制重定向数据根，永不触网、永不使用真实 LLM 凭证。被测源码在 [`../app/`](../app/)，各域设计见 [docs/architecture/](../../../docs/architecture/)。

## 运行方式

- 全量：`cd services/api && python -m tests`
- 单模块：`cd services/api && python -m tests tests.test_classroom_api`（`__main__.py` 在进程级沙箱内跑 unittest）
- 环境搭建、浏览器 smoke/full 回归与 CI 所有权：[docs/development/testing.md](../../../docs/development/testing.md)

## 存储沙箱（强制）

- 一切测试继承 `tests/storage_sandbox.py::StorageSandboxTestCase`（自定义 fixture 也必须调用 `patch_all_storage_roots`）：运行数据根、全部存储根绑定、`prompt_memory`、`settings.trace_dir` / `chroma_dir`、StudentModel 与 vector-store 缓存均重定向进 `TemporaryDirectory`。
- 铁律：严禁写生产存储根（synthetic ID 曾泄漏出上千孤儿文件）；严禁无清理的裸 `tempfile.mkdtemp`；严禁网络依赖与真实 LLM。需走 turn 管线的用例必须 patch `get_llm` / `_build_tools`。

## 主要族群（按前缀）

- `classroom_*`（31 件）：课堂域全量——api / pipeline / worker / jobs / runs / revisions / block_edit / render / render_layout / composition / audio / exports / checkpoints / chat / images / research / sources / storage / lifecycle / health / prompts / schema 等。
- `assistant_*`（19 件）+ `b05–b11`：站点助手动作、目录、handoff、通知、undo、语音。
- `assessment_*` / `quiz_*` / `question_audit` / `unified_submission` / `submission_identity` / `chat_quiz_intent`：出题、评分、grounding、受理与 CAS。
- `textbook_*`（14 件）+ `library` / `attach_library`：教材注册、组卷、图谱构建与恢复。
- `evaluation_*` / `learner_*` / `evidence_*` / `b08_learning_history` / `learning_consumers`：M7 评估、画像投影与学习证据账本。
- `knowledge_*` / `concept_index` / `rag_*` / `local_rag` / `search_query_planning` / `preresearch` / `workspace_cat_grounding`：知识图谱与 BM25/向量检索。
- `orchestration` / `teaching_*` / `supervisor_*` / `memory` / `cross_session_memory` / `prompt_memory_lifecycle` / `ux` / `style_inference` / `dialogue_checkpoint` / `task_*` / `directive_arbitration` / `circuit_breaker`：编排与 M1–M9 智能层。
- `voice` / `voice_azure`：语音通话协议、切句清洗与 TTS provider。
- `diagram_*` / `illustration_*` / `quiz_illustration*`：图库目录、素材创作与题图 v2 装配。
- `notes` / `notes_agent` / `notes_m9_sync` / `trash` / `workspace_*` / `sidebar_api`：笔记、回收站与工作区。
- `admin_*` / `guest_*` / `security*` / `session_isolation` / `delete_account` / `user_*` / `projection_api`：管理端、游客、鉴权与账号级联。
- `chat_upload_pipeline` / `upload_limits` / `multimodal_*` / `file_parser` / `pdf_ocr` / `ocr_policy` / `p5a_ocr_repairs` / `text_quality` / `figure_harvest`：上传解析与 OCR。
- 运行时基建：`atomic_hardening` / `json_utils` / `cors_network` / `bootstrap_readiness` / `deployment_contract` / `requirements_contract` / `compat_api` / `docs` / `allowlist_sanitize` / `orphan_cleanup` / `prompt_registry` / `prompt_eval`。

## 共享 helper

- `storage_sandbox.py` — 沙箱基类与 `patch_all_storage_roots`（一切测试必经）
- `classroom_fake_llm.py` / `classroom_fixtures.py` / `classroom_provider_mocks.py` — 课堂 fake LLM、固定件与 provider mock
- `prompt_eval/` — prompt 评测辅助
