# ADR-0003: BM25 是基础检索能力，向量检索为可选增强

- 状态：accepted
- 日期：2026-10（自 P9 检索运行时改造确立）

## Context

RAG 检索存在两条技术路线：纯词法（BM25）与向量语义检索。向量检索需要 embedding 模型：要么分发模型权重（体积与许可都不可行），要么依赖外部 API（破坏 keyless 可测试性与离线部署能力）。教材检索的高精度场景（术语、公式、题号）本身对词法匹配友好。

## Decision

- BM25 是**必在的基线检索能力**：干净 clone、零外部依赖（keyless CI）下必须可用；
- 向量检索（本地自备模型接口或 Embedding API）是**可选增强**，配置后才启用，与 BM25 混合（RRF 融合）；
- 仓库不分发任何 embedding 模型权重或向量 index（与 ADR-0001 一致）；
- 公开教材向量包的构建/导入是部署侧的运维操作（`docs/operations/semantic-rag.md`），不是仓库内容。

## Consequences

- CI 的 backend/vector 分层：普通提交的测试全部 keyless（BM25 轨），向量回归由每周 regression workflow 用 `requirements-vector.txt` 环境单独跑。
- 检索质量的下限由 BM25 保证；语义增强的缺失只降低召回上限，不破坏功能。
- 混合检索的调参空间（ranker 权重、tier 分级）集中在检索运行时内，见 [architecture/knowledge-rag.md](../architecture/knowledge-rag.md)。
