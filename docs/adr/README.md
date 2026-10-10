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
| [ADR-0006](./0006-material-assisted-svg-authoring.md) | 素材参考 SVG 创作 | V3 允许加工、组合及自绘，实际 PNG 合并审核后兼容冻结发布 |
| [ADR-0007](./0007-shared-illustration-tools.md) | 独立配图工具 | 配图引擎供测评与工具助手复用，情景配图支持选材与多轮版本 |
| [ADR-0008](./0008-expo-react-native-mobile.md) | Expo/RN 单移动代码库 | 移动端唯一代码库走 Expo SDK 57 / RN 0.86，共享包起步，禁止复制 API |
| [ADR-0009](./0009-root-monorepo-shared-packages.md) | 根 monorepo 共享包 | 根 pnpm workspace + 五个共享客户端包，契约生成单事实源 |
| [ADR-0010](./0010-enterprise-persistence.md) | 企业持久化栈 | PostgreSQL/Object/Redis 企业模式，`DATABASE_URL` 门控，原多 worker 门控由 ADR-0014 收紧 |
| [ADR-0011](./0011-tenant-rotating-sessions.md) | 租户与轮换会话 | tenant/membership + RS256 短 access/轮换 refresh 会话族，复用即撤族 |
| [ADR-0012](./0012-cloud-speech-server-mediated.md) | 云语音服务端中转 | 服务端 STT/合成 REST 端点，Azure 凭证只在服务器；`/voice/ws` 兼容保留，MeloTTS 定位 self-hosted/dev 可选 |
| [ADR-0013](./0013-durable-workflows.md) | Durable workflows | Temporal 承担后台长任务执行所有权，`TEMPORAL_ADDRESS` 门控双模式，域持久化仍是唯一事实源（取代 ADR-0004 的进程内任务持有部分） |
| [ADR-0014](./0014-single-instance-until-domain-cutover.md) | 完整领域迁移前单实例 | DATABASE_URL 只接入身份基础设施，业务文件事实源仍须单 API 实例/worker（已由 ADR-0017 收口取代企业模式禁令；文件模式仍有效） |
| [ADR-0015](./0015-permissive-pdf-backend.md) | 许可宽松的 PDF 后端 | pypdf/pdfplumber/pypdfium2/ReportLab 替代 PyMuPDF（AGPL），core/pdf 门面 + PDFIUM_LOCK，页边界契约不变 |
| [ADR-0017](./0017-domain-document-repositories.md) | 域文档仓储 JSONB cutover | 九域同构 `<domain>_documents` 表 + DocumentRepository 协议与逐域路由，epoch CAS，字节走 ObjectStore，派生索引留文件态 |
| [ADR-0016](./0016-valkey-cache-and-minio-removal.md) | Valkey 缓存与 MinIO 移除 | 缓存服务端换 Valkey 9.1.2（BSD-3）+ CACHE_URL，删 AGPL MinIO 占位，缓存即弃态不迁移 |
| [ADR-0018](./0018-s3-object-store-adapter.md) | S3 兼容 ObjectStore 适配器 | boto3（Apache-2.0）S3 兼容字节层：sha256 元数据完整性、SSE-S3、multipart、standard retry；local 保持默认，误配 loud 失败 |
| [ADR-0019](./0019-deterministic-dual-engine-lab.md) | 确定性双引擎模拟实验台 | Python 权威引擎 + TS 镜像同构双实现，manifest 锚定内容包，命令队列 base_revision=tip+i，ACK state_hash 对账；AI 仅经 providers Protocol 预留 |
| [ADR-0020](./0020-independent-electrical-lab.md) | 独立的本地电路实验室 | 浏览器端确定性电路求解器、本地版本化实验和沉浸式工作台，不接入 AI、聊天或化学实验室同步体系 |
| [ADR-0021](./0021-threejs-math-workbench.md) | Three.js 数学作图工作台 | 2D SVG + 固定版本 Three WebGLRenderer 3D，domain 纯 TS 数学内核 + Worker，本地文档无 AI/后端 |
| [ADR-0022](./0022-math-workbench-three-modes.md) | 作图器三模式独立画布与独立存档 | 移除立体几何构造模式；三模式各持独立文档/撤销栈/存档槽位（storage v2），视图命令不进撤销 |
| [ADR-0023](./0023-math-workbench-mode-switch-prompt.md) | 作图器模式切换保存提示与右栏按模式分域 | switch-mode 意图经 dirty 三选一（直接切换不销毁数据）；右栏页签/数学工具/几何构造与属性面板按模式分域升级 |
