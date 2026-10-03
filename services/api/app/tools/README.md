# tools — M10 会话工具实现

对话 Agent 的五个会话工具实现，全部遵循统一 `ToolResult` 协议返回（status/error code），使工具行为可诊断；工具语义分属出题质量链与知识检索域，本目录只拥有实现与注册面。

## Owns

- `quiz.py` — `generate_quiz`：按学段生成结构化练习（严格 JSON schema：stem/options/answer/explanation/knowledge_point/difficulty）
- `fit_quiz.py` — `fit_quiz`：由完整参考题生成同构变式（逆向考点结构，非数据替换）
- `knowledge_search.py` — RAG 检索（向量轨配置时 BM25+embedding RRF 混合，否则纯 BM25；契约两种模式完全一致）
- `knowledge_read.py` — 按证据卡 chunk 指针取完整原文及相邻片段（纯读取、零 LLM、越权面与 knowledge_search 一致）
- `recall_history.py` — JIT 历史找回：对 session transcript JSONL 做 BM25 并跨会话检索，使有损压缩可恢复

## Does not own

- `ToolResult` 协议与 Tool 基类（`core/tool_protocol.py` / `core/tool_base.py`）
- Skill manifest/registry 与门控（`agents/skill_runtime/`——工具经 `registry` 绑定到 `agent.skill.*`）
- 出题质量链语义（assessment 域）与检索索引/教材库存储（RAG/知识域）
- transcript/quiz_history 等存储（上下文工程域）；执行循环与护栏（`agents/executor.py`）

## Design

工具与 Skill 的绑定表、`ToolResult`/`ErrorCode` 契约、工具步预算见
[docs/architecture/skill-runtime.md](../../../../docs/architecture/skill-runtime.md)。

## Tests

- `services/api/tests/test_knowledge_read.py` / `test_recall_history.py` — 工具直接回归
- `test_quiz_card_contract.py` — 工具→Skill→题卡交付链
- `test_quiz_quality.py` / `test_unified_submission.py` / `test_quiz_submission_state.py` — 出题与作答链
- `test_local_rag.py` / `test_rag_hybrid.py` — 检索轨（BM25 与混合模式契约一致性）

## Key entry points

- 每个工具模块暴露单一入口函数（`generate_quiz` / `fit_quiz` / `knowledge_search` / `knowledge_read` / `recall_history`），经会话/工作区作用域构造，不暴露 `student_id`/`file_ids`
- 工具可见性由 `agents/router.py::CAPABILITIES`（M10 Registry 投影）决定
