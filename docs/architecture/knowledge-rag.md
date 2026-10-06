# Knowledge & RAG（教材摄取 · 检索 · 知识图谱）

> 一句话职责：把上传的教材与资料变成可检索、可溯源、可构建知识图谱的知识底座——解析/OCR、结构化切块、BM25 基线（向量可选）混合检索与证据门、资料库 Library、教材管线与 M5 教材知识图谱。

## Purpose / Scope

- 本模块覆盖「文件 → 知识」的完整链路：上传解析（PDF/DOCX/PPTX/TXT/MD/图片）、扫描件 OCR、图表结构化标记、结构化切块与索引、混合检索运行时（查询变体、RRF 融合、证据门、分级证据、上下文重建）、资料库（Library）与教材库（Textbook）、教材知识图谱构建与合并视图（M5 知识智能）。
- 检索基线是**确定性 BM25**，向量轨是可选增强；证据门在无足够证据时返回 partial/NOT_FOUND 而非静默编造（反幻觉 evidence gate）。
- 知识只来自教材：考纲 seed 已彻底移除（无 seed 代码路径，主图初始为空），图谱 = 公用教材图谱 ∪ 自有教材图谱；`scope=public` 公用教材所有账号可读、仅管理员可写。
- 不在本模块范围：对话编排（chat/supervisor）、出题与测评（M4）、工作区公共记忆——它们是检索结果的消费方。

## Owned code

- `services/api/app/agents/knowledge/`（15 个 Python 文件）：
  - `manager.py`（KnowledgeService：`graph_for` / `retriever_for` 合并视图）、`store.py`（图谱持久化）、`graph.py`（KnowledgeGraph 本体、环检测与邻接索引）、`schema.py`（节点/多类型边模型）；
  - `textbook_builder.py`（章节切片 + 教材构建编排）、`custom_graph.py`（spec → graph 确定性合并）、`taxonomy_normalizer.py`（章名/标题规范化）；
  - `retriever.py`（ConceptRetriever）、`content.py`（ContentResolver）、`context_builder.py`（知识指令块组装）、`reasoning.py`（Dependency Reasoner）、`bridge.py`（M5 → SkillGraph 投影）、`scope_primitives.py`（考纲 seed 包与空聚合层已删除，主图初始为空）。
- `services/api/app/core/`：`file_parser.py`（上传解析）、`multimodal_parser.py`（内嵌媒体对齐）、`pdf_ocr.py`（逐页择优 OCR）、`pdf/`（pypdf/pdfplumber/pypdfium2 门面 + `PDFIUM_LOCK`，ADR-0015）、`ocr.py`（视觉 OCR 单页调用 + tesseract 回退）、`ocr_policy.py`（OCR 动态策略）、`textbook_ocr.py`（教材后台 OCR 持久状态机）、`textbook_pipeline.py`（构建队列/有界并发策略）、`figure_harvest.py`（原生 PDF 图表收割）、`text_quality.py`（文本层质量分级）、`structured_chunker.py`（Structured V2.2 切块）、`rag_index.py`（staging 质检 + BM25/向量发布）、`retriever.py`（`chunk_text` + BM25）、`hybrid.py`（BM25+向量 RRF 融合）、`evidence_gate.py`（证据门）、`evidence_context.py`（证据摘录/上下文重建）、`knowledge_store.py`（会话级 KnowledgeStore）、`library.py`（资料库存储与 `chunks_for` 惰性切块）、`textbook.py`（Textbook 记录服务）、`file_summary.py`（文件级摘要）、`vector_store.py`、`embedding.py`、`vector_jobs.py`、`public_vector_artifact.py`（向量轨支撑）。
- `services/api/app/api/v1/`：`library.py`、`textbook.py`、`knowledge.py`；工作区上传端点在 `workspace.py`（`POST /workspaces/{id}/upload`，落 Library 专属夹）。
- 工具：`services/api/app/tools/knowledge_search.py`、`knowledge_read.py`；检索触发统一在 `services/api/app/agents/material_signals.py`。
- 前端：资料中心 `/resources/files|textbooks`、知识图谱页 `/knowledge`（属前端模块，此处只定义契约）。

## Public contracts

**Agent 工具**（经 `core/tool_protocol.py` 统一协议）：

- `knowledge_search`：混合检索 + 证据门。结果字段稳定输出 `file_id / filename / page / chunk / section / chapter / source_scope / location_label / retrieval_strategy / block_type / printed_page`；`location_label` 双轨——教材印刷页码（`printed_page`，显示为「教材第 N 页」）优先，否则「PDF 第 M 页」，无法确定时明确「未标页」。输出带置信档位（高 ≥0.75 / 中 ≥0.45 / 低）与 `partial` 前缀；NOT_FOUND 文案附已尝试核心词，允许模型换篇名/概念名重试一次。问句类查询由 `is_natural_question` 识别，先剥口语尾巴（`question_core`）与学段/必修语境词，跳过短语覆盖门（相关性由词项覆盖 + 阈值门判定）；非问句的专业短语门保留。
- `knowledge_read`：按 chunk 序号 / PDF 页码读取完整原文与相邻片段（`span=current/prev/next/both`，`chars ≤ 4000`），纯读取零 LLM；乱码片段附警示。

**REST API**（前缀 `/api/v1`）：

- 资料库：`GET /library`、`POST /library/folders`、`PATCH|DELETE /library/folders/{id}`（专属夹 400）、`POST /library/upload`、`POST /library/files/{id}/move`、`DELETE /library/files/{id}`、`GET /library/files/{id}/download`（原件字节一致，RFC5987 编码；无原件的老文件 404，前端按 `has_original` 隐藏）、`GET /library/files/{file_id}/page/{page}`（PDF 原件页快照 PNG，Cache-Control 1 天，自有→公共解析序，外人/非 PDF/越界 404——图表证据「查看原页」通道）。
- 教材库：`POST /textbooks/upload`（multipart，限流 10/min；`group` 组名成组、`group_id` 追加卷；上传必选学段，用户选择优先于骨架 LLM 推断）、`GET /textbooks`、`GET /textbooks/{id}`（含章节 outline，building 进度前端 2s 轮询）、`PATCH /textbooks/{id}`（title/subject/level/栏目名/组备注）、`GET /textbooks/{id}/figure-status|quality|download`、`GET /textbooks/{gid}/volumes/{fid}/download`、`PATCH /textbooks/{id}/volumes/{fid}`（卷显示名）、`DELETE /textbooks/{id}`（级联：图谱归档删除 + Library 文件 + 向量 + 记录）、`DELETE /textbooks/{gid}/volumes/{fid}`（剩余卷自动重建/删空删组）、`POST /textbooks/{id}/rebuild_graph`（`mode ∈ rag_graph|graph_only|full_ocr|quality_ocr`）、`POST /textbooks/{id}/cancel`（合作式终止解析）、`GET|PUT /textbooks/{id}/graph-policy`、`POST /textbooks/bulk/rebuild|cancel`。公用教材（`scope=public`）的写操作全部仅管理员。
- 知识：`GET /knowledge/graph`（合并视图；支持 `textbook_id` 隔离与 group/volume 范围、`overview/chapter/search/full` 视图）、`GET /knowledge/concepts/{id}`、`GET /knowledge/taxonomy`（学段 → 学科 → 教材组只读投影，每教材组附带 `textbook_id/topic_key/node_ids`/卷文件/备注）、`GET /knowledge/catalog|custom`。
- 管理：`GET|PUT /admin/ocr-policy`（并发 1–100、重试间隔 0–3600s 在线调整）、`GET|PUT /admin/textbook-pipeline`。公共教材的批量升级走 `POST /textbooks/bulk/rebuild`（管理员对 `scope=public` 记录发起 `rag_graph`/`quality_ocr`）。

**Python 契约**：

- `KnowledgeService.graph_for(student_id) / retriever_for(student_id)`：合并视图 = learned ∪ 公用图谱 ∪ 自有教材图谱；双命名空间 mtime stamp 缓存、写后失效；stamp 与 listing 忽略 `*.chunks.json`（chunks 是检索域数据，其重写不应使全量合并缓存失效）。`graph_for` 每学生一把构建锁 + 锁内双检。
- `Library.chunks_for(file_id)`：惰性构建并落进程级缓存（`(namespace, file_id) -> (mtime, chunks)`）；`remove_file` 主动失效、内容变更经 mtime 自动失效；所有检索/建图/向量读路径一律经它，不得直接读 `chunks_by_file`。
- `KnowledgeGraph`：`edges` 列表是序列化真相源；`add_edge` 去重走 `_edge_keys` 集合（O(1)），`_reaches/_prerequisites_of/descendants_of/neighborhood` 走 `_adj_out/_adj_in` 邻接索引（O(可达)）；语义等价由朴素参考实现测试钉死。
- 资料分类元数据唯一事实源在资料中心：三级目录固定为 学段（小学/初中/高中/本科/其他）→ 学科（资料中心 subject 原样）→ 教材组/栏目（`group_name`，单本教材也有独立栏目）→ 章节/概念；改名/改学科/改学段不触发重新 OCR、切块、向量化或图谱重建，下一次 taxonomy 请求立即反映新分类；`file_id`/`file_ids`/`topic_key` 永远稳定。

## State & storage

全部位于统一运行数据根（`NEXT_TUTOR_DATA_DIR`，默认 `.runtime/data`，见 `core/paths.py`）：

- 资料库：`chat_history/library/<student_id>.json`（元数据 + RAG 索引状态）+ `chat_history/library/data/<student_id>/<file_id>.txt`（解析文本；PDF/.txt 是事实源）+ `<file_id>.orig<ext>`（原件）。
- 教材注册记录：`chat_history/library/<student_id>.textbooks.json`（原子写 + 文件锁；状态机、进度、章节/概念数、warnings、`ocr_state`、`graph_policy`）。
- `ocr_state` 是 OCR 重启恢复事实源：状态版本、force-full 模式、物理/目标/成功/待处理/暂停/空白页、逐页 attempts、next retry、错误码摘要、策略 generation、配置阻塞与 API/本地成功计数；不保存图片、密钥或模型原始响应。
- 图谱：`knowledge/custom/<owner>/<topic_key>.json`（唯一 active 图谱，原子替换 + version 递增）+ `<topic_key>.chunks.json`（概念 → chunk_ids 预索引）+ `<topic>.volume_specs/<file>.json`（完整规范化抽取缓存，限制变化只快速重合并）。`knowledge/graph.json` 仅承载 Dependency Reasoner 的学习边。
- RAG 索引随 library 文件元数据持久化：`bm25_revision`（`RAG_INDEX_VERSION:content_hash`）、`vector_revision`、`status ∈ bm25_ready|ready`（向量构建在单槽后台队列中完成后升级为 ready，失败维持 BM25 可用）、`content_sha256`、`staging_quality`（含 `failed_garble` / `excluded_by_garble` 计数）。
- 向量轨（可选）：Chroma collection 按模型/维度/chunk schema/归一化/RAG revision 指纹隔离，`scope` 元数据过滤（笔记侧为 `notes:<sid>`）。公共教材向量包以可校验 NPZ 分发、部署本地导入；混合私有数据的 `knowledge/vector_db` 绝不入库（构建/导入见 [../operations/semantic-rag.md](../operations/semantic-rag.md)）。
- 会话附件：`uploads/<id>.txt`（会话恢复时从其重建 KnowledgeStore，历史对话仍可检索已上传资料）。
- 删除经统一回收站（`chat_history/trash/items/<owner>/<trash_id>/`，`textbook` / `textbook_volume` / `library_file` bundle）；教材归档 bundle 含 graph policy、coverage、概念索引与 volume spec 缓存，恢复保持稳定 ID。

## Main flows

**1. 上传解析**（`file_parser` + `multimodal_parser`）：普通文件 ≤256MB（整本扫描教材）、图片 ≤20MB，超限前后端均给明确警告。PDF 按页取文本（页边界 `\f`）；DOCX 段落+表格按文档序展开；PPTX 递归提取组合形状/表格/备注页；TXT/MD 多编码回退（utf-8 → gbk 等）。docx/pptx 内嵌媒体、PDF 稠密页插图/表格（稀疏页仍整页视觉 OCR）、md data-uri 图统一走 `describe_embedded_image` 分型描述（题目照片→完整转录题干；插图→图述；装饰→丢弃），产出 `[图|文档图片 N]` 标记块；失败按「丢弃该图」降级，绝不阻塞上传。图片 OCR 双通道：本地 tesseract（chi_sim+eng）兜底，主 LLM 通道多模态模型优先；聊天图片经 `/chat/upload` 写入当前 session KnowledgeStore（原件 + OCR 文本 + chunks），发送问题时作为 current-turn attachment 强制触发 R10 检索，而不是只把 OCR 文本临时拼入 prompt。

**2. 文本质量路由**（`text_quality.classify_page`，纯函数零 LLM）：`good / corrupt / sparse / empty` 四级；corrupt 双门槛 = 乱码字符数 ≥8 且占比 ≥0.2%（PUA/U+FFFD/意外文字系统/全角字母数字/字体替换字集），辅助信号为「孤立短行占比 ≥0.35 且乱码 ≥3」。OCR 触发 = 稀疏 ∪ 乱码 ∪ 空（`pdf_ocr.pages_needing_ocr()` 统一供上传决策、OCR 调度、构建预算与同步回退门槛使用）；良好文本层页绝不 OCR 降质。

**3. OCR 双通道**：

- 同步通道（聊天附件/普通资料库/工作区）：async 入口不阻塞事件循环，受 `PDF_OCR_SYNC_MAX_PAGES=20` 昂贵页数上限保护，短重试 + 本地回退。
- 教材后台通道（`textbook_ocr`）：持久状态机逐页断点续传，与同步通道是两条独立故障契约：
  - 每轮仅处理 `ocr_state.volumes[file_id].pending_pages`；页级 `_settle` 逐页落盘（视觉模型单页可达 3 分钟，增量持久化使进程中途被杀不丢已结算页）；单页意外异常只记该页失败，不炸整轮。
  - `empty_content`（模型正常响应但页面无文字，典型为空白页/版权页）与 `render_failed` 属页面永久终态：达 `max_attempts` 后按空白页收尾（占位 `""`、记入 `empty_pages` 并计入完成数，教材继续构建）；已判空白页跨重建继承不重烧；瞬时错误（429/5xx/timeout）按策略持续等待重试。
  - 默认 `persistent_api` 模式不调用 tesseract，配置错误保持可见的 `ocr_waiting`；两种 bounded 模式分别在页级上限后转本地 psm3 或暂停。
  - 轮末 `_write_text_and_chunks` 按内容 hash 幂等跳过——零进展的等待轮不全书重切块与重写 library JSON。
- 构建调度：唯一驱动是 per-owner FIFO 队列（上传自动构建、手动刷新、失败重试、回收站恢复全部入队，`enqueue_textbook_build`）；同 owner 并发构建数 ≤ `build_concurrency`（默认 2，legacy 模式强制 1），同一本书按 (owner, textbook_id) 构建锁互斥；OCR 重试轮由队列项内门控驱动（等待卷 `next_retry_at` 到点就地续跑，spec 缓存有效即复用）；OCR 页并发与图表图述另经 `ocr_policy.run_page` 全局限流（与聊天附件/普通资料的串行 OCR 分治）。取消是合作式的：`parse_cancel_requested` 标记在 `_volume_spec` 入口/逐章抽取/OCR 逐页 settle/组卷循环各检查点生效，任一卷已有可用文本（.txt > 50B）→ `ready`，否则 `failed`；`cancelled` 状态不入恢复队列。启动对账：lifespan `reconcile_stale_builds()` 把中断 `building` 置回可恢复（OCR pending → `ocr_waiting`，有 intent → build_job `queued`），`resume_pending_textbook_ocr()` 按记录顺序重建队列、只重试 pending 页。
- 构建执行位置（ADR-0013 双模式）：默认在 API 进程内执行（上述队列即进程内 `_BUILD_QUEUES`）；配置 `TEMPORAL_ADDRESS` 后，`enqueue_textbook_build`/`_spawn_refresh` 在 API 进程改为派发 `textbook.build_intent`/`textbook.refresh` workflow（`app/workflows/textbook.py`，documents 队列）——activity 在 **worker 进程**（`services/api/worker.py`）内走同一 `enqueue_textbook_build` 的进程内队列路径，FIFO/并发/看门狗语义零漂移；启动对账/OCR 续跑/中断重入队随之移到 worker 启动时执行（`main.py` durable 分支只做 legacy 记录迁移）。API 侧 enqueue 返回的 Future 改由 workflow 终态结算，上游（手动刷新等待）无感。documents 队列保持单 worker 实例消费（域队列在 worker 进程内存中，与文件模式同一约束）。

**4. 图表结构化**：扫描书由逐页 OCR prompt（`core/ocr.py::_PAGE_OCR_PROMPT` v2）在转录正文的同时输出结构化标记——`[页码=N]`（页面自印页码，无则省略）、`[图 N|图注]` + 2–4 句客观图述（对象/方向/几何关系/数值趋势；纯装饰图标标 `[图|装饰]`）、`[表 N|表题]` + `|` 分隔行——随 `.txt` 事实源持久化（断点续传/empty_pages 机制不变）。原生 PDF（文本层书）由 `figure_harvest` 确定性收割（`RAG_FIGURE_HARVEST` 默认开）：`page.find_tables()` 提取表格 markdown（零 LLM，带反伪造门槛——≥2 行/≥2 列/≥4 非空单元格，且任意两列 ≥2 行内容完全一致判伪造丢弃）；`get_images()` 位图区域过滤 <60pt/<1% 面积小图标与 >90% 整页扫描图（单卷 ≤40 张成本护栏）后裁剪渲染走多模态图述；PDF page label 纯数字时输出 `[页码=N]`。收割块按页并入 `.txt`（无收获时文本 hash 不变，rag_graph 刷新零成本跳过）；图谱 spec 抽取仍用纯正文（标记不进 TOC/骨架抽取）；矢量图形不做聚类猜测（宁缺毋滥）。

**5. 教材构建管线**（`textbook_builder`，`TEXTBOOK_GRAPH_ENABLED` 开时）：上传后同步解析+切块+BM25 立即可检索（文本提取经 `asyncio.to_thread` 不阻塞事件循环），后台（分钟级，fire-and-forget）构建图谱：

1. 章节切片四级回退：PDF 书签目录（优先「章粒度」层级——某层 ≥2 条匹配「第N章/Chapter」才选它，避免「篇」容器级；带 Tier 1 垃圾标题质检：文件名/印刷段号/卷书名包装/无教学关键词长 ASCII 串剔除，噪声条目直接剔，垃圾占比过半整级弃用交 LLM 从正文定位）→ LLM 目录提取（文本开头 8000 字产章节名）→ 确定性短行识别（`第N章/单元/课/部分`）→ 整书单章「全册」（短讲义允许，长教材降级落 warning + `needs_reextract`，不把降级 cache 当健康缓存）。切片携带页码区间（Tier 1）供概念预索引限定检索域。
2. 骨架 LLM ×1（`disable_thinking=True`）推断 subject/level——仅补缺，用户上传所选学段/学科优先。
3. 逐章概念抽取 LLM ×N：每章 1 次（≤20000 字全文走单次快速路径；章文本预算 24000 字，超限按章首/章中/章末分层抽样避免稳定遗漏章末知识点）；章间零依赖并行、结算严格按章序（产出与串行一致）。
4. `spec_to_graph` 确定性合并（零 LLM）：id 命名空间 `custom.tb-<id>.*`、两遍扫描前向引用、PREREQUISITE DAG 环守卫、严格锚定主图 RELATED；全局 `name_to_id` 按名去重使跨卷同名概念合并为单节点；上限 30 章/400 概念（全局兜底），教材组 `graph_policy`（组默认 + `volume_overrides`，`null`=不限）逐卷覆盖；`chapter_order = 卷序号×1000 + 卷内章序`。
5. 写入 M5 store 并进入 `graph_for` 合并视图（`/knowledge/graph`、概念检索、supervisor 知识指令自动可见）。
6. 概念预索引：概念 name/aliases 在其章节页码范围内的 chunks 子串预过滤（每概念 ≤50），存 `<topic_key>.chunks.json`（随图谱删除联动）；`knowledge_search` 命中教材图谱概念时优先在该概念章节检索域内检索前置返回。
7. `_post_ready_rag` 收尾：chunk 重建 + 向量 upsert + 缺失剪枝 + 摘要；构建期间收割改写过 `.txt` 时补一次 RAG 重建保证 `content_sha256` 与最终文本一致。

教材组（多卷合一）：各卷独立「OCR+切片+抽取」得 spec，章节列表（章名加卷前缀 `卷名·章名`）合并成单个 spec 单次建图；上传 `group` 参数成组、`group_id` 追加卷（自动重建，已 OCR 卷零重 OCR）。任何异常落 `status=graph_failed`（教材仍可检索），可经 `rebuild_graph` 重试（归档 + 原子替换）；不产生半成品中间态（合并成功才写盘）。

**6. 结构化切块**（`structured_chunker`，schema `structured-v2.2`）：

- 尺寸与保护：确定性目标 220–480 tokens、软上限 520、硬上限 650；定义/定理/公式/表格/例题/figure 等结构块在硬上限内保持完整且受保护（不切分、不并段、前后块不得打包进它）；`[图...]/[表...]` 标记行为块边界，`图述/图注/题目转录` 前缀与 `|` 行为从属行。
- 课文结构：`annotation`（①…/〔注〕，信号在 NFKC 前抓取——① 经 NFKC 变 "1" 会与数字标题混淆）与 `vocabulary`（音标/词表行）独立成块、同种类连续行合块、不被后续正文打包稀释；课题标题（「1 春」「第10课 背影」，数字无小数点）打 `is_lesson`，其后同课文 chunk 带 `lesson` 字段（章级标题重置）——父子 chunk 分层（`parent_id/prev_id/next_id`）的地基。
- 页码：`[页码=N]` 在页首独立行或行内任意位置（取该页最后一个标记数字）均可解析为 chunk metadata `printed_page` 并从正文剥离；检索结果透传 `block_type`/`printed_page`，模型上下文 `[来源...]` 行带 `[图]`/`[表]` 徽标。
- 文本净化：断行修复（孤立小写字母行 = 脚注上标锚点，前行尾+后行头均 CJK 且后行 ≤2 字符合并保词，其余锚点行丢弃——大写单字母行保留，可能是答案键）与跨页重复运行页眉剥离（≥3 页页首/页尾、≤30 字符整行剥离）。
- 检索面包屑（`retriever.retrievable_text`，零 LLM contextual-retrieval）：索引 token = 「书名 · 课题 · 章节路径 · 印刷页码」+ 正文，BM25 与 embedding 同一入口，展示文本不变——修复课题类查询的词面覆盖缺失。
- 乱码准入：乱码 chunk 置 `garble_excluded` 且不生成 BM25 token / vector（chunk 保留在存储供 knowledge_read 与审计）。
- 发布纪律：重建先在内存 staging 质检唯一 ID、页映射、hash 与硬上限，再按教材组单次原子发布；同 owner 的刷新串行（避免公共库批量升级丢更新）；schema 不匹配的旧卷按指纹自动重建（免迁移）。

**7. 检索运行时**：

- 召回：原始问题 + 确定性关键词/中英术语扩展（`_query_variants` 接入内容词核变体）独立召回；BM25 轨（CJK bigram，常驻确定性）与可选向量轨（`EMBEDDING_PROVIDER=local|openai`，local 为模型无关离线接口、仓库不内置模型；任一故障自动回退 BM25）RRF 融合（k=60）；最终结果先按文件分桶保证相关教材卷覆盖、再按总相关度补齐（不相关卷不会被硬塞）。
- 证据门（`apply_evidence_gate`）：
  - 查询核提取级联（`effective_query`）：动词尾捕获（「是否讲到X」→X，剥首尾虚词）→ 问句内容词核 → 原查询，每级过 ≥2 非停用内容字守卫，词项集永不坍缩。
  - `title_match`：查询核（折叠标点）与书名/章/节/课题/section_path 的契合度——整串命中独立 0.5 档（课题类查询最强词面证据），bigram 部分命中 0.2 档。
  - 置信度公式（纯 BM25 诚实刻度）：`lexical*0.45 + bm25_rel*0.2 + title_signal + vector*0.1 + intent/phrase bonus − penalty`（`bm25_rel` 用池内相对分）。
  - 绝对/相对门 + 近重复与同页去重 + 乱码 drop（`text_garble_ratio ≥0.05`，全灭且主因乱码时 NOT_FOUND 附「文本层疑似乱码，建议重建索引」指引）。
- 分级证据（`GateResult.tier ∈ found|partial|not_found`）：没有达标项但存在弱信号（词项/标题部分命中、置信 ≥0.05）→ partial 返回低置信项（`confidence_tier=low`）而非 NOT_FOUND——「检索器没找到足够好的证据」≠「知识库里没有」；MMR 淘汰后不足额用弱信号池补位。
- 上下文重建（`evidence_context`，反碎片化）：公式感知切句（ASCII `.!?` 前后均非数字才切，「式（8.50）」「p.280」不断开）、定义句居中（是指/称为/定理/公式）、摘录上限 900 字符；同 (file_id, lesson) 选中块合并为「课文《X》节选（教材第a–b页）」≤1600 字符；摘录 <350 字符按 prev/next_id 取邻块头部；整包预算 6000 字符，溢出不再静默丢弃，降为一行指针「另有N条未展开：来源·页码·chunk」。
- 小库直通：chunks ≤ max(top_k, 8) 时跳过打分全量返回（小文件对转述式提问零命中会诱发「凭文件名编造」）。
- 检索触发统一（`material_signals`）：书名号《…》（≥2 字）即内容问题信号（`is_content_question`）、长度门槛 6 字、有资料时 `decision.material_grounding_required` 强制保留检索技能，chat intent 分类同源。

**8. M5 知识服务（图谱之上的运行时组件）**：

- `ConceptRetriever`：BM25 over concept search_text（复用 `core/retriever`）+ KG 遍历扩展 + 分数融合；确定性、零 LLM；`match_concept(level=...)` 学段感知（分数相近偏好学生学段节点，未指定不偏好）。
- `ContentResolver`：概念 → 教学内容，级联永不阻塞（教材内容 → 上传材料 BM25 回退，把讲解锚定到学生实际教材）；本体外概念返回空（M5 隐形不噪音）；命中经 `_GatedSearchStore` 适配器过同一证据门。
- `KnowledgeContextBuilder`：组装 `[知识智能·…]` 软指令块（概念定位/前置补缺/易错点/教学示例/教材引用/相关概念）；前置补缺读统一评价投影——仅提示 fragile/conflicting/emerging 概念的前置（未观察 ≠ 未掌握）。
- `DependencyReasoner`（M5 唯一用 LLM 的组件）：为新概念自动补 prerequisite 边——候选检索 → 规则过滤 → LLM 校验器（结构化 `{relation,confidence}`）→ 阈值门 ≥0.65 → DAG 安全写入 + 持久化；仅在扩展新节点，绝不在教学关键路径，坏输出无法腐蚀 DAG。
- `SkillGraphBridge`：M5 → M2 SkillGraph plain-data 投影；M5 关时 M2 回退自有 legacy 种子（独立模块），**永不并存**（避免双真相源）。
- `/knowledge` 个性化学习路径 `_personalized_next`（确定性、零 LLM）：四级优先——M9 本周计划未掌握概念 → 承接最近教学日志概念的可学后继 → M9 目标学科内可学节点 → 学生学段基础补全；每条带 `reason` 徽标，有作答记录的节点归入复习列表。

**9. 反幻觉组合**：R10 预检索硬约束；小库直通；`knowledge_search` 未命中时明确禁止编造；工作区公共记忆注入声明「与检索原文冲突时以检索原文为准」；会话恢复从 `uploads/<id>.txt` 重建 KnowledgeStore；有附件轮次的 attachment reminder；伪工具标签护栏（`pseudo_tool_guard`）——模型在正文里「叙述」工具调用（`<knowledge_search` / `<tool_call` / `<function=` 标签族）即停流，转真实检索、注入结果并继续环路，标记绝不进入最终答案与会话历史。上传后 fire-and-forget 生成 ≤150 字摘要 + 3–5 个主题标签，注入 planner/preamble（LLM 检索前即知每份资料覆盖什么）。

## Dependencies

- `core/atomic.py`（所有 JSON/文本落盘原子写）、`core/config.py`（全部开关）、`core/paths.py`（存储根绑定）、`core/orphan_cleanup.py`（孤儿扫描类别登记）。
- `core/llm_async.get_llm()`：主 LLM 通道的同一多模态模型承担视觉 OCR/图述与图谱小 JSON 调用（`disable_thinking=True`）；单页调用内置 3 次对异常/空 content 退避重试，耗尽回退 tesseract；未配置主通道走本地 tesseract。谱系构建 LLM 客户端取并发上限 8，实际节流由动态门 `llm_gate()` 负责（限额每次准入动态读取策略）。
- PDF 引擎（ADR-0015）：pypdf（页数/目录/page label/逐页文本）、pdfplumber（表格/位图 bbox）、pypdfium2（页面光栅化，`pdf.PDFIUM_LOCK` 全局串行，不跨 `await`、不持有长命文档对象）；业务层只 import `core/pdf` 门面，渲染逐页经 `render_page_png`，整书页数/文本层走 `pdf_page_count` / `pdf_page_texts`。损坏/加密以 `pdf_corrupt`/`pdf_encrypted` 错误码暴露。
- Chroma（可选向量）、numpy（NPZ 向量包）。
- `prompts/registry.py`：OCR/目录/逐章抽取 prompt 版本化，prompt 指纹（`_prompt_fingerprint`）进缓存失效（版本 bump 各书刷新时全量重抽一次）。
- 消费方：chat 工具循环（`knowledge_search`/`knowledge_read`）、M2 证据投影（`/knowledge/graph` 概念着色 = 评价投影 ∪ 教学日志）、M4 出题 grounding、M9 目标链/学习路径、笔记智能体（复用同一检索路径）、课堂备课 sources。
- 游客材料层：`KnowledgeStore(memory_only=True)` 只读公共教材文本，禁私有文件/附件/上传。

## Invariants / security boundaries

- **资料默认私有**：上传到资料库的文件不属于任何对话，只有被学习区/工作区显式选入才参与检索；按账号物理隔离（`chat_history/library/<sid>.json` + `data/<sid>/`）。`scope=public` 公用教材保留命名空间（文件/记录/图谱同构）所有账号可选用、仅管理员可写；`graph_for` 合并视图把公用图谱并入每个学生视图。
- 身份一律来自 `resolve_student_id()`；请求体/query 里的 student_id 字段一律忽略；越权访问 404（不泄露存在性）。
- **PDF/.txt 是事实源，RAG/图谱是可删除重建的派生物**。刷新各模式精确清理对应文件集合、绝不越界：

| 模式 | `.txt` 事实源 | 原 PDF | OCR 状态 | chunks/BM25 | 向量轨 | 图谱 | 概念索引 |
|---|---|---|---|---|---|---|---|
| `rag_graph`（默认） | 可被原生收割**追加**表格/插图标记 | 不动 | 不动 | 全量重建 | upsert 缺失 + 剪枝本 scope 孤儿 | 重合并（spec 缓存命中则复用；缺 cache 可调 LLM 但 skip_ocr） | 重建 |
| `graph_only` | 不动（skip_harvest） | 不动 | 不动 | 不动 | 不动 | 强制重抽 | 重建 |
| `full_ocr` | 页级全量重写（失败页保留旧文本，可用性优先） | 不动 | 清空重建 | 构建后全量重建 | 同 rag_graph | 重抽 | 重建 |
| `quality_ocr` | 按当前文本 verdict 逐页择优（稀疏∪乱码页 OCR，良好页保留） | 不动 | 清空重建 | 构建后全量重建 | 同 rag_graph | 重抽 | 重建 |
| 删除（组/教材） | 进归档区 | 进归档 | 进归档 | 进归档 | 进归档 | 进归档 | 进归档 |

- 每 (owner, topic_key) 仅一个 active 图谱：发布前质量门拒绝空章/非教学章/文件 URL 污染章/无概念章；质量校验后原子替换（version 递增），不生成隐藏历史快照；运行时读路径零 LLM。
- 图谱每次写入做可达性环检测（一个环会腐蚀 M2/M3/M4 的学习顺序推理）；索引语义由参考实现测试钉死，不得回退线性扫描；M5 合并插入新节点后必须 `invalidate_traversal_cache()`；async 端点内的同步重活必须 `asyncio.to_thread`。
- 良好文本层页绝不 OCR 降质；在途稀疏 OCR 轮永不升级为整本重 OCR（force_full 意图按卷钳制：正向继承保证全量升级不被稀疏化，反向钳制保证在途轮不被翻全量）；重试只跑 `pending_pages`，从头重来仅三种显式重建。
- 并发不变量：per-book 构建锁 (owner, textbook_id)；`ocr_state` 新鲜读-合并-写（每次结算重新读记录、只覆盖本卷键）；library 读改写段持 `rag_index._owner_rag_lock` 并在工作线程执行；向量孤儿剪枝只清本 scope（按 `where={"scope": scope}` 分页比对）。
- 删除守卫：构建轮内发现教材记录/库文件已被归档删除即停止一切写回（不复活孤儿 `.txt`）；`textbook_builder` 在图谱/概念索引写入点 re-check 记录存在。
- OCR 状态、trace、shadow telemetry 不含图片、密钥、模型原始响应、检索原文或思维链——候选只以 file/chunk 引用指纹记录。
- 账户删除时该模块全部存储根随 `purge_account` 清空且不留空目录；孤儿残留由 `core/orphan_cleanup.py` 扫描清理（`public`/`student_default` 共享命名空间受保护）。

## Configuration

| 开关 | 默认 | 语义 |
|---|---|---|
| `PDF_OCR_MODE` | `auto` | `auto` 逐页择优（稀疏 ∪ 乱码 ∪ 空触发）；`on` 上限内强制整本；`off` 禁用 |
| `PDF_OCR_MAX_PAGES` | `1024` | 教材后台 OCR 页数上限（调高自动续扩，rebuild 不重复 OCR 已有稠密页） |
| `PDF_OCR_SYNC_MAX_PAGES` | `20` | 对话/资料库同步段 OCR 页数上限（保护响应性） |
| `PDF_OCR_DPI` / `PDF_OCR_CONCURRENCY` | `200` / `20` | 渲染 DPI / bootstrap 并行批次；实际并发由 `/admin/ocr-policy` 动态治理（1–100，策略存 `chat_history/settings/ocr_policy.json`），账户 `prefs.ocr_parallel` 逐人覆盖 |
| `TEXTBOOK_GRAPH_ENABLED` | `1` | `=0` 时上传只解析+索引，跳过图谱构建直接 ready |
| `TEXTBOOK_GRAPH_MAX_CHAPTERS` / `MAX_CONCEPTS` | `30` / `400` | 图谱全局兜底上限；教材组 `graph_policy`（`null`=不限）与卷 override 优先 |
| `EMBEDDING_PROVIDER` | `off` | `off` 纯 BM25；`local` 部署方自备离线模型（CPU、懒加载、单槽 worker、归一化向量）；`openai` 兼容 Embedding API |
| `RAG_HYBRID` | `1` | `=0` 强制关闭向量轨 |
| `RAG_CHUNKER_MODE` | `v2` | `legacy` 旧 500 字段落切块 |
| `RAG_EVIDENCE_GATE` | `on` | `on / shadow（只记 telemetry）/ off` |
| `RAG_CONTEXT_COMPRESS` | `true` | 证据上下文压缩投影（补 `printed_page`） |
| `RAG_FIGURE_HARVEST` | `true` | 原生 PDF 图表收割；关闭降级纯文本层 |
| `RAG_EVIDENCE_EXCERPT_CHARS` | `900` | 单条证据摘录上限 |
| `TOOL_CONTEXT_CURRENT_MAX_CHARS` | `6000` | 整包证据注入预算 |

- 动态策略（落盘 `chat_history/settings/`，env 仅作文件缺失默认）：`/admin/ocr-policy`（并发 1–100、重试间隔 0–3600s、`request_timeout_seconds ≤300`；教材后台全库共享 generation limiter，聊天附件与普通资料同步 OCR 串行）；`/admin/textbook-pipeline`（`build_concurrency` 默认 2、`volume_concurrency` 默认 2、llm 并发上限，legacy 模式强制全 1）。

## Observability

- 教材记录状态机：`building / ocr_waiting / ocr_paused / partial / ready / graph_failed / failed / cancelled`；`GET /textbooks/{id}` outline + 进度（前端 2s 轮询）；教材卡在解析中状态显示「终止解析」。
- `GET /textbooks/{id}/quality`（只读、零 OCR 成本）：逐卷 verdict 统计 + 乱码率 + staging 状态 + `recommended_mode`（corrupt ≥10% → `quality_ocr`，触发权在用户）；`GET /textbooks/{id}/figure-status` 探测旧卷 `.txt` 是否已含图表/页码标记（刷新弹窗推荐升级路径）。
- staging 质检：`staging_quality.status="failed_garble"`（corrupt 页占非空页 ≥10% 仍发布索引——降级可检索优于不可用，由质量报告显形 + 手动 quality_ocr 兜底）与 `excluded_by_garble` 计数。
- trace：`tool_result` 事件带 `error_code / error_message / gate_drop_reasons`；多模态轮记 `multimodal_routing`；`turn_start` 带 `visible_file_ids`（工作区可见性诊断面）。
- 证据门 shadow telemetry：候选/淘汰原因/no-hit/近重复率、候选指纹、selected context hashes 与延迟（无原文/CoT）。
- NOT_FOUND 语义细分：可见教材仍在 building/ocr_waiting 时提示「教材仍在后台解析中，完成后即可检索」。
- 知识谱系页面按学段分组 chips（小学/初中/高中/本科/其他）；跨教材章节总览 subtitle 由 taxonomy `node_prefix` 派生，不把书名写入持久化章节节点。

## Tests / acceptance

- 教材管线/OCR：`test_textbook.py`、`test_textbook_api.py`、`test_textbook_build_recovery.py`、`test_textbook_bulk.py`、`test_scheduler_queue.py`/`test_round_durability.py`/`test_parallel_mode.py`/`test_cancel_recovery.py`（原 OCR scheduler 巨件拆分）、`test_textbook_stall.py`、`test_textbook_pipeline_policy.py`、`test_pdf_ocr.py`、`test_p5a_ocr_repairs.py`。
- 图谱/谱系：`test_textbook_graph_direction.py`、`test_textbook_graph_policy.py`、`test_textbook_group.py`、`test_textbook_sections.py`、`test_textbook_taxonomy.py`、`test_textbook_taxonomy_normalizer.py`、`test_textbook_preamble.py`、`test_knowledge.py`、`test_knowledge_custom.py`、`test_knowledge_graph_index.py`、`test_knowledge_graph_views.py`、`test_knowledge_scope_isolation.py`。
- 检索运行时：`test_rag_v2.py`、`test_rag_hybrid.py`、`test_local_rag.py`、`test_evidence_gate_tiers.py`、`test_evidence_query_core.py`、`test_evidence_context_recon.py`、`test_knowledge_read.py`、`test_material_trigger_signals.py`。
- 资料库/多模态：`test_library.py`、`test_multimodal_parser.py`、`test_multimodal_context.py`、`test_multimodal_uploads.py`、`test_docs.py`。
- 验收基线：合成取证问句的查询核提取回归、迷你语料置信度校准（title_match / partial 分级 / 乱码排除 / 公式切句）、课文合并与溢出指针、书名号触发信号、KnowledgeGraph 索引与朴素实现的语义等价、刷新各模式文件集合边界；所有测试继承 `tests/storage_sandbox.py` 沙箱，不写生产存储根。

## Related ADRs

- ADR-0001 source-only——仓库不携带教材与派生数据资产，公共教材命名空间为部署本地运行态。
- ADR-0002 运行数据单根——本模块全部存储位于统一运行数据根之下。
- ADR-0003 BM25 基线 + 向量可选增强——检索以确定性 BM25 为基线，向量轨是可选增强（本模块核心决策，见 [../operations/semantic-rag.md](../operations/semantic-rag.md) 的向量包构建/导入契约）。
- ADR-0004 single-worker——持久化并发不变量以单 worker 进程为前提。
- ADR-0005 Pages synthetic——演示站点仅使用合成语料。
- [ADR-0013](../adr/0013-durable-workflows.md) durable workflows——教材构建/刷新的执行所有权在 `TEMPORAL_ADDRESS` 配置后移交 worker 进程，域队列与事实源不变。
