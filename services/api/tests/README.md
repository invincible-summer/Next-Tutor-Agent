# tests — 后端测试套件

FastAPI 后端的 `unittest` 回归套件：`test_*.py` 按代码 owner 分入子目录，全部经 `storage_sandbox` 强制重定向数据根，永不触网、永不使用真实 LLM 凭证。被测源码在 [`../app/`](../app/)，各域设计见 [docs/architecture/](../../../docs/architecture/)。

## 运行方式

- 全量：`cd services/api && python -m tests`
- 单模块：`cd services/api && python -m tests tests.classroom.test_classroom_api`（`__main__.py` 在进程级沙箱内跑 unittest）
- 环境搭建、浏览器 smoke/full 回归与 CI 所有权：[docs/development/testing.md](../../../docs/development/testing.md)

## 存储沙箱（强制）

- 一切测试继承 `tests/support/storage_sandbox.py::StorageSandboxTestCase`（自定义 fixture 也必须调用 `patch_all_storage_roots`）：运行数据根、全部存储根绑定、`prompt_memory`、`settings.trace_dir` / `chroma_dir`、StudentModel 与 vector-store 缓存均重定向进 `TemporaryDirectory`。
- 铁律：严禁写生产存储根（synthetic ID 曾泄漏出上千孤儿文件）；严禁无清理的裸 `tempfile.mkdtemp`；严禁网络依赖与真实 LLM。需走 turn 管线的用例必须 patch `get_llm` / `_build_tools`。

## 目录（按代码 owner）

| 目录 | 覆盖 |
| --- | --- |
| `classroom/`（31） | 课堂域全量——api / pipeline / worker / jobs / runs / revisions / block_edit / render / composition / audio / exports / checkpoints / chat 等 |
| `agents/site_assistant/`（20） | 助手动作（含 b05–b11 批次）、目录、handoff、通知、undo、语音、学习简报 |
| `agents/assessment/`（16） | 出题、评分、grounding、受理、CAS 与统一提交（`unified_submission` 被多域复用） |
| `agents/knowledge/`（30） | 教材注册/组卷/图谱、OCR（`pdf_ocr` / `p5a_ocr_repairs`）、切片、BM25/向量检索、multimodal 解析 |
| `agents/student_model/`（14） | M2 画像、学习证据 journal 与统一学习评价域（`learner_*` / `evaluation_*` M2 面 / `evaluation_worker`） |
| `agents/supervisor/`（16） | 聊天管线、任务理解、preresearch、伪工具防护、推理流、circuit breaker、context budget |
| `agents/memory/`（5） | M6 记忆生命周期、跨会话记忆、prompt memory、风格推断 |
| `agents/teaching_engine/`（3） | M3 教学决策、指导状态机、会话小结 |
| `agents/evaluation/`（2） | M7 教学系统评估：策略分析器、教学指导部署 |
| `agents/learning_orchestration/`（7） | M9 编排 schema/store/SRS/planner/manager/API |
| `agents/skill_runtime/`（4） | 证据门、evidence context、素材触发信号 |
| `agents/ux_intelligence/`（1） | M8 UX 画像 |
| `api/`（16） | 管理端、教材/工作区 API 面、library、sidebar、docs、compat 门面、安全加固 |
| `identity/`（7） | 鉴权、会话/工作区隔离、游客、头像、账号删除级联 |
| `core/`（13） | 原子写、上传、file_parser、trash、orphan_cleanup、prompt 注册表与 eval、部署/依赖契约 |
| `diagrams/`（6） | 图库目录、扩展学科、guidance、素材、figure harvest |
| `illustration/`（4） | 题图 v2 全链、job 生命周期、出题侧配图策略 |
| `notes/`（3） | 笔记仓库、笔记智能体、M9 同步 |
| `voice/`（7） | 语音通话协议与 TTS provider（Azure/Melo） |

## 共享 helper（`support/`）

- `storage_sandbox.py` — 沙箱基类与 `patch_all_storage_roots`（一切测试必经）
- `classroom_fake_llm.py` / `classroom_fixtures.py` / `classroom_provider_mocks.py` — 课堂 fake LLM、固定件与 provider mock
- `prompt_eval/` — prompt 评测辅助（金标 `golden.jsonl`）

跨目录复用的测试基类（如 `classroom.test_classroom_pipeline`、`agents.assessment.test_unified_submission`）按 owner 归位后仍以 `tests.<owner>.<module>` 绝对导入共享；新增测试请放入对应 owner 目录，不要回到扁平根。
