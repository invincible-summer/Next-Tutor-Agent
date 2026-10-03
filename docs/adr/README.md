# Architecture Decision Records

本目录记录对 Next Tutor Agent 有长期影响的架构决定：为什么在当时做出该选择、决定内容与后果。

## 规则

- 每份 ADR 编号递增（`NNNN-<slug>.md`），状态为 `accepted` 后**不修改结论**。
- 决策被取代时，新建一份 ADR 并在旧 ADR 头部标注 `Superseded by ADR-NNNN`，不覆盖历史。
- ADR 只记录真正的架构决定（影响边界、数据、依赖方向、分发形态的选择），不记录功能实现细节——那些属于 [`architecture/`](../architecture/README.md) 文档。

## 索引

| 编号 | 标题 | 一句话决定 |
| --- | --- | --- |
| [ADR-0001](./0001-source-only-repository.md) | Source-only 仓库 | 公开仓库只分发源码/测试/合成 fixtures，不分发教材及派生数据 |
| [ADR-0002](./0002-runtime-data-root.md) | 运行数据单根 | 所有运行数据统一进入 `NEXT_TUTOR_DATA_DIR` |
| [ADR-0003](./0003-bm25-baseline.md) | BM25 检索基线 | BM25 是基础检索能力，向量检索为可选增强 |
| [ADR-0004](./0004-single-worker-persistence.md) | Single-worker 持久层 | JSON/JSONL 文件持久层采用单 worker 不变量 |
| [ADR-0005](./0005-synthetic-pages-demo.md) | Synthetic Pages demo | GitHub Pages 演示仅使用项目自制的 synthetic fixtures |
