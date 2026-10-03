# Knowledge（M5 知识智能 · 教材知识图谱）

把上传的教材变成可构建、可合并、可检索的知识底座：知识图谱本体与合并视图（公用 ∪ 自有教材）、概念检索与内容解析、教材建图管线、概念 → chunks 预索引。

## Owns

- `manager.py` — `KnowledgeService`：`graph_for` / `retriever_for` 合并视图（双命名空间 mtime 缓存 + 写后失效 + 构建锁）。
- `store.py` — 图谱持久化（原子替换 + version 递增）。
- `graph.py` — `KnowledgeGraph` 本体：序列化真相源 `edges`、环检测、`_adj_out/_adj_in` 邻接索引。
- `schema.py` — 节点/多类型边模型。
- `textbook_builder.py` — 教材构建编排：章节切片回退链、骨架/逐章概念抽取、写图与收尾。
- `custom_graph.py` — spec → graph 确定性合并（命名空间 `custom.tb-<id>.*`、PREREQUISITE DAG 环守卫）。
- `taxonomy_normalizer.py` — 章名/标题规范化。
- `retriever.py` — `ConceptRetriever`：概念 BM25 + KG 遍历扩展 + 分数融合（零 LLM，学段感知）。
- `content.py` — `ContentResolver`：概念 → 教学内容级联回退，永不阻塞。
- `context_builder.py` — `[知识智能·…]` 知识指令块组装。
- `reasoning.py` — `DependencyReasoner`：新概念 prerequisite 边补全（本包唯一用 LLM 的组件，阈值门 + DAG 安全写入）。
- `bridge.py` — M5 → M2 SkillGraph plain-data 投影（与 legacy 种子永不并存，避免双真相源）。
- `scope_primitives.py` — public / student 命名空间原语。
- `seed.py` — seed 聚合入口。

## Does not own

- 文件解析、OCR、结构化切块、BM25/向量混合检索与证据门在 `core/`（`file_parser` / `pdf_ocr` / `structured_chunker` / `hybrid` / `evidence_gate` 等）——本包是这些能力的图谱层与编排层，检索融合（BM25 基线 + 可选向量，ADR-0003）的实现在 core 侧。
- 资料库 Library 与教材记录存储在 `core/library.py` / `core/textbook.py`；REST API 在 `api/v1/{library,textbook,knowledge}.py`。
- 检索工具 `tools/knowledge_search.py` / `knowledge_read.py` 与检索触发统一在 `agents/material_signals.py`。
- 对话编排、出题与测评（M4）、工作区公共记忆是检索/图谱结果的消费方，不归本包。

## Design

- 知识只来自教材：图谱 = 公用教材图谱（`scope=public`，所有账号可读、仅管理员可写）∪ 自有教材图谱；`graph_for` 把公用图谱并入每个学生视图。
- 确定性优先：建图终态是 spec → graph 零 LLM 合并，运行时读路径零 LLM；LLM 仅用于骨架/逐章抽取与 `reasoning.py` 的新节点扩展，坏输出无法腐蚀 DAG。
- 图谱每次写入做可达性环检测（一个环会腐蚀 M2/M3/M4 的学习顺序推理）；每 (owner, topic_key) 仅一个 active 图谱，发布前过质量门后原子替换。
- 概念 → chunk_ids 预索引（`<topic_key>.chunks.json`）把概念命中的检索域限定在其章节页码范围内。
- 权威事实源见 [docs/architecture/knowledge-rag.md](../../../../../docs/architecture/knowledge-rag.md)。

## Tests

`services/api/tests/`：教材管线 `test_textbook*.py` 系列（构建恢复、OCR 调度、组卷、图谱方向/策略、章节、谱系）；图谱与视图 `test_knowledge*.py` 系列（含 `test_knowledge_graph_index.py` 索引语义等价、`test_knowledge_scope_isolation.py` 命名空间隔离）；检索运行时 `test_rag_v2.py` / `test_rag_hybrid.py` / `test_evidence_gate_tiers.py` / `test_knowledge_read.py`。

## Key entry points

- `KnowledgeService.graph_for(student_id)` / `retriever_for(student_id)` — 合并视图唯一入口。
- `textbook_builder` — 教材后台建图管线入口。
- `ConceptRetriever.match_concept` / `ContentResolver` — 概念检索与内容解析。
- `SkillGraphBridge` — M2 SkillGraph 投影。
