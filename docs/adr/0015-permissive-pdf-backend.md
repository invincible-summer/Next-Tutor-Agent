# ADR-0015: 许可宽松的 PDF 后端替代 PyMuPDF

- 状态：accepted
- 日期：2026-10-06
- 关联：ADR-0003（BM25 基线）、ADR-0010（企业持久化）；依赖治理见 `docs/compliance/dependency-and-sbom.md`。

## Context

PyMuPDF 官方区分 AGPL-3.0 与商业授权两条路径：闭源/专有发行要继续使用它就必须购买商业许可。本项目政策是「不购买第三方商业授权、保持专有闭源、依赖全部免许可费」，因此生产与测试路径必须迁出 PyMuPDF/MuPDF。本地 OCR（Tesseract，Apache-2.0）不涉及该问题，按产品决策保留。

PyMuPDF 在仓库中承担的职责：PDF 文本层逐页提取、页数、TOC/书签、page label、页面光栅化（扫描页远端视觉 OCR 与本地 tesseract OCR 的 PNG 输入、页面预览）、原生表格收割、内嵌位图 bbox/裁剪渲染、测试 PDF 构造。

## Decision

引入 `services/api/app/core/pdf/` 作为唯一 PDF 引擎门面，业务层禁止直接 import 引擎包：

- **pypdf**（BSD-3-Clause，pin ≥6.19 规避恶意 page label 内存耗尽 CVE）：页数、outline/TOC、page labels、基础逐页文本；`/PageLabels` 缺失时 label 返回空串，保持旧引擎「无声明不产出 [页码=N]」的行为。
- **pdfplumber**（MIT，pdfminer.six MIT）：表格行网格、内嵌位图 bbox。
- **pypdfium2**（Apache-2.0/BSD-3-Clause 双许可，OR 分支记录为 Apache-2.0；wheel 捆绑的 PDFium 及其第三方许可随发行物声明）：页面光栅化。PDFium 非线程安全——所有 pypdfium2 调用经全局 `PDFIUM_LOCK` 串行（沿用 MuPDF 时代 2026-08 uvicorn SIGSEGV 的教训），锁不跨 `await`；pypdf/pdfplumber 各自持有独立 reader，不经过该锁。
- **Pillow**：crop/resize/encode（每页最多整页渲染一次，多图裁剪）。
- **ReportLab**（BSD，仅测试 lane）：synthetic PDF fixtures（文本/空白/位图/书签/表格/CJK/page label——label 用 pypdf writer 后处理构造，不引入手写二进制 fixture）。

文本抽取主路 pypdf、整档打开失败回退 PDFium（Chrome 同源引擎的容错性）；损坏/加密以稳定错误码（`pdf_corrupt`/`pdf_encrypted`）暴露诊断，门面层保持旧「永不抛出」的结果契约（0/[]/None），`\f` 页边界拼接契约不变（BM25 citation、结构化切块、printed page fallback 依赖它）。表格反伪造门槛、插图过滤阈值（60pt/1%/90%/单卷 40 图）、page label 契约等知识管线策略全部保留在 `core/figure_harvest`。

## Consequences

- AGPL 依赖从生产与测试依赖中清零（pymupdf pin 删除，`import fitz` 清零）；许可证清单/SBOM 同步再生成。
- 渲染吞吐受全局锁约束（与迁移前相同）；需要并行渲染时走 worker 进程，不走多线程 PDFium。
- 引擎差异带来的文本层字节差异由合同测试约束（页数、`\f` 计数、章切分、OCR 目标页、渲染尺寸不漂移；字符量差异设阈值），性能由 20/100/300（可选 1000）页 synthetic 基准与 fd/RSS 预算守护，不做第二套引擎逃避回退。
