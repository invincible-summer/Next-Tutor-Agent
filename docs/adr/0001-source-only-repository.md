# ADR-0001: 公开仓库只分发 source/test/synthetic fixtures

- 状态：accepted
- 日期：2026-10

## Context

本项目的核心输入是真实教材（PDF/EPUB）。教材本体受版权保护，其 OCR 全文、chunks、知识图谱运行数据、embedding/vector index 都是教材的派生产物，随附分发同样受版权约束。此外用户数据（笔记、会话、画像、trace）天然属于隐私，绝不可入库。仓库同时托管面向公众的 GitHub Pages 演示，演示内容也需要有清晰的来源授权。

## Decision

公开仓库只分发：

- 项目源码与测试；
- deployment template；
- 项目自制的 synthetic fixtures（唯一 demo 内容源是 `fixtures/demo/`，以及 E2E 用的 synthetic 文本 fixture）；
- 项目原创/程序化生成的 SVG 及其 provenance；
- 可确定性重建的生成型 catalog/reference；
- 第三方许可证文本。

禁止入库：教材 PDF/EPUB、OCR/解析全文、chunks、教材图谱运行数据、embedding/vector index、用户笔记/会话/画像/trace、真实 demo 账户导出、真实模型权重、未明确许可来源的图像素材。

该边界由 `scripts/repo/check_repository_hygiene.py` 在 CI 第一道阻断 job 上对 tracked 文件与全历史强制执行。

## Consequences

- 干净 clone 即可运行全部 keyless 测试；任何需要真实教材的数据都是部署本地运行时状态。
- 公共教材命名空间（`public`）的内容是部署本地的，不在仓库间迁移。
- Pages 演示只能展示 synthetic 内容（见 ADR-0005），这约束了演示的真实感，换来零版权风险。
- 任何人 fork 仓库不会获得任何有版权争议的资产。
