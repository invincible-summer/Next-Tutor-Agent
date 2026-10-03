# scripts/retrieval — 公用向量包构建与导入

面向 `public` 命名空间公用教材语料的向量检索包（BM25 之外的 optional vector lane）。属于部署期/运维工具，不是日常开发命令。

| 脚本 | 作用 |
|------|------|
| `build_public_vector_pack.py` | 从公用教材 chunks 构建可搬运的向量包（分片 manifest + 向量 + 校验和），默认输出到运行数据根 `public_vectors/` |
| `import_public_vector_pack.py` | 校验并导入一个向量包到本环境 Chroma 存储 |

## 契约

- **联网**：构建使用本地 embedding 客户端（`app/core/embedding.py` 的 LocalEmbeddingClient，模型由部署方提供），不访问外部服务；导入只读写本机运行数据根。
- **真实 LLM 费用**：无（embedding 走本地模型，不经 chat LLM）。
- **输入**：必须是已入库的 synthetic/授权公用教材派生 chunks——仓库本身不携带任何教材或派生数据。
- **输出**：**不允许入库**。向量包是部署本地运行时状态（runtime data root），受 `NEXT_TUTOR_DATA_DIR` 约束，由 hygiene guard 排除在 tracked 文件之外。
- **推荐调用位置**：部署期/运维手工执行（可选 vector 环境，需 `requirements-vector.txt` 依赖）；CI 不调用（BM25-only 环境跳过，见 `tests/agents/knowledge/test_local_rag.py` 的 load_tests 门）。
- **对应文档**：[docs/operations/semantic-rag.md](../../docs/operations/semantic-rag.md)、[docs/architecture/knowledge-rag.md](../../docs/architecture/knowledge-rag.md)。
