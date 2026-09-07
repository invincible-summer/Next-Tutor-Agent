# 自备本地模型的语义 RAG 部署与公共向量发布

Edu_Agent 的语义检索是独立运行时：不 import Paper_Agent，不复用其虚拟环境、数据库或服务。
BM25 始终常驻；本地模型或 Chroma 故障时检索自动回退 BM25。

**本仓库不内置、不固定、不默认分发任何本地向量大模型。** 曾捆绑的本地 embedding
模型运行时（sentence-transformers、CPU PyTorch wheel 及其版本 pin）已从依赖与
constraints 中彻底移除，仓库与远端历史中也从未包含过任何模型参数权重或模型代码
文件。保留下来的只有模型无关的通用本地向量模型 RAG 接口（`backend/app/core/embedding.py`
的 `LocalEmbeddingClient`）：部署方自带具备许可的模型与推理运行时，接口负责懒加载、
单槽串行、离线约束与归一化输出。

## 1. 自备本地模型运行时

使用 Python 3.11 与 Edu_Agent 自己的虚拟环境。向量轨需要 Chroma：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip -r backend/requirements.txt -r backend/requirements-vector.txt
.venv/bin/python -m pip check
```

在此之上，**自行安装**你为所选本地模型准备的推理运行时依赖（如对应的
sentence-transformers / ONNX / 其他推理框架版本），并自行获取具备许可的模型文件。
本仓库不指定、不 pin、不分发任何模型或其运行时组合——许可合规由部署方对其
选用的模型与运行时负责。

生产 `.env` 示例（模型名与路径由部署方提供）：

```dotenv
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=<your-own-local-embedding-model-id>
EMBEDDING_MODEL_PATH=/opt/models/<your-local-embedding-model-dir>
EMBEDDING_CACHE_DIR=/opt/model-cache/huggingface
EMBEDDING_DEVICE=cpu
EMBEDDING_BATCH_SIZE=32
EMBEDDING_MAX_THREADS=2
RAG_HYBRID=1
CHROMA_DIR=/opt/edu-agent/knowledge/vector_db
OMP_NUM_THREADS=2
MKL_NUM_THREADS=2
```

`EMBEDDING_MODEL_PATH` 优先于模型名称/缓存；不设置 `EMBEDDING_MODEL` /
`EMBEDDING_MODEL_PATH` 时 local 轨道直接禁用（仓库没有默认模型）。本地 provider
强制 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1` 与 `local_files_only=True`，
运行时不会下载模型。服务使用一个本地 ML 槽串行加载/编码/索引；Uvicorn 保持
`--workers 1`。模型懒加载，不做启动预热。

默认 `EMBEDDING_PROVIDER=off`。`openai` provider 继续支持
`EMBEDDING_BASE_URL/API_KEY/MODEL`（只需 `requirements-vector.txt`，无需任何本地
模型运行时）。任何 provider 失败都不影响上传成功或 BM25 检索。

## 2. 公共教材向量包

向量包是部署本地的运行产物（不入库；可用任一自备模型在部署机上重新构建）。构建器只读取
固定 `public` Library namespace 的 UTF-8 文本、元数据和当前 Structured RAG
chunks，使用当前配置的 embedding client：

```bash
cd backend
python scripts/build_public_vector_pack.py
```

构建器输出分片 NPZ 和 `manifest.json`，记录 schema、RAG/chunker 版本、模型/指纹、
维度、dtype、chunk/file 数量与 hash、分片 checksum、UTC 构建时间和 Git commit。
发布前会校验 chunk ID 唯一性、文本/chunk hash、向量数量/维度/归一化、分片
checksum 和确定性随机自查询。全部通过后才原子替换旧目录；失败保留旧 artifact。

公共原文、`public.json`、`public.textbooks.json`、`knowledge/custom/public/**`、
`knowledge/public_vector_artifacts/**` 与通用 `knowledge/vector_db` 一律不入库，
它们都是部署实例的运行时数据，可能包含私有账号信息。

## 3. 云端导入

在部署机上获得经过验证的向量包后，在重启 backend **之前**执行：

```bash
cd backend
python scripts/import_public_vector_pack.py
```

导入器会重新校验 manifest、所有分片、当前公共文本和 chunks，并确认当前配置模型
的指纹/维度一致。它先构建完整 staging collection，同时复制旧 collection 中所有
非公共 runtime vectors；通过数量与查询 smoke check 后原子切换
`active_collections.json`，再清理旧 collection。相同 manifest 重复导入直接跳过。
任何校验或 staging 失败都不会切换现有 collection，服务仍可用 BM25。

导入成功后公共 Library 文件的 `rag_index.status` 更新为 `ready`，并记录 manifest
revision、模型、维度和指纹。公共教材仍只有被 workspace/会话明确选择后才进入
可见 scope。

## 4. 用户上传与资源边界

上传/解析先持久化文本与 Structured RAG chunks，状态立即为 `bm25_ready`；向量任务
进入单槽后台队列，完成后变为 `ready`，失败则保持 `bm25_ready`。整本教材不会在
请求/SSE 路径同步 embedding。删除和重建按 file/scope 清理，内容 hash 变化会重新
embedding；不同模型、维度、chunk schema、归一化策略或 RAG revision 使用不同
collection。

4 vCPU / 10GB 基线：单 Uvicorn worker、部署方自备的 CPU embedding 模型、
batch 32、2–4 个 native CPU threads、不加载 reranker/Docling、不运行时下载模型。
上线仍需在目标服务器同时运行 Paper_Agent 与 Edu_Agent，实测 RSS、swap、上传、
SSE 和连续检索峰值；仓库测试不能替代该资源验收。
