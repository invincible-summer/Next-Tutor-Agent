# 内容政策（仓库可分发内容边界）

本文件是"什么内容允许进入本仓库"的唯一权威规则。执行机制是 `scripts/repo/check_repository_hygiene.py`（CI 第一道阻断 job，检查 tracked 文件与全历史）；本文件回答"为什么"与边界判断。相关决策背景见 [ADR-0001](../adr/0001-source-only-repository.md)。

## 允许入库

- 项目源码与测试；
- deployment template（`deploy/`）；
- 项目自制的 synthetic fixtures：
  - Pages 演示唯一内容源 `fixtures/demo/`（虚构书名、`fx_*` 合成 id，README 声明 synthetic 来源）；
  - E2E 用的合成教材文本 fixture（如 `apps/web/tests/e2e/fixtures/` 下的 `synthetic-zx17-grounding.txt`，fixtures README 声明 project-authored synthetic）；
- 项目原创/程序化生成的 SVG 及其 provenance（`services/api/assets/diagram_library/`，含逐素材 `material.json`/`usage_guide.json` 与审核台账）；
- 可确定性重建的生成型 catalog/reference（如 [../reference/diagram-assets.md](../reference/diagram-assets.md)，由 `scripts/diagrams/build_catalog.py` 生成，`--check` 保证与源一致）；
- 第三方许可证文本（`licenses/`、`THIRD-PARTY-NOTICES.md`）。

## 禁止入库

- 教材 PDF/EPUB 原件；
- OCR/解析出的教材全文；
- chunks（切片）；
- 教材知识图谱运行数据；
- embedding/vector index（公共向量包是部署本地产物，见 [../operations/semantic-rag.md](../operations/semantic-rag.md)）；
- 用户笔记、会话、画像、trace 等任何私有用户数据；
- 真实 demo 账户导出；
- 真实模型权重（含 embedding 模型、TTS 模型——模型缓存一律 gitignored 部署侧资源）；
- 未明确许可来源的图像/素材。

## test fixture 规则

所有用于 RAG/E2E 的文本 fixture 必须是 **synthetic**（项目自写），禁止使用真实教材片段。fixture 文件应使用可读的 `synthetic-*` 命名或在文件头声明来源为 project-authored synthetic。

## SVG 与图库 provenance

- 每个 `diagram_library/materials/<id>/` 素材目录保存 `asset.svg`、`material.json`（含 provenance：origin/creation_method/source_hash）与 `usage_guide.json`；
- 审核台账 `review.json` 逐项绑定来源哈希：**不能只批量修改"已审核"字段**；绘制源或生成器变更后需重渲染复核并重绑哈希，过程记录在 [../validation/diagram-library.md](../validation/diagram-library.md)；
- 引用外部知识事实（如 OpenStax 页面）只登记 URL 与用途（`fact_only`），不复制其图形。

## 运行数据

一切运行数据（`students/`、`chat_history/`、`traces/`、`uploads/`、`notes/`、`knowledge/`、`users/`、`classroom/`、`assistant/`、`illustrations/`、`diagram_assets/` 等）都在 `NEXT_TUTOR_DATA_DIR` 数据根之下，被 `.gitignore` 覆盖（[ADR-0002](../adr/0002-runtime-data-root.md)）。公共教材命名空间（`public`）的内容同样属于部署本地运行时状态，默认为空，由部署方自行导入。

## 第三方许可

- 第三方许可汇总见 [`THIRD-PARTY-NOTICES.md`](../../THIRD-PARTY-NOTICES.md)，原文存于 [`licenses/`](../../licenses/)；
- 语音栈的来源与审计规程见 [voice-licenses.md](./voice-licenses.md)；
- 本仓库目前**未授予开源复用权**（无项目 LICENSE）；`THIRD-PARTY-NOTICES.md` 与 `licenses/` 独立于项目自身许可状态。
