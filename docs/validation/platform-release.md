# Platform release validation

复核日期：2026-10-06。产品版本：3.1.0。移动客户端证据与设备矩阵见 [mobile-validation](mobile-validation.md)，企业持久化与多实例前提见 [enterprise-infra](../development/enterprise-infra.md)，灾备基线见 [disaster-recovery](disaster-recovery.md)，运行限制沿革见 [ADR-0014](../adr/0014-single-instance-until-domain-cutover.md) → [ADR-0017](../adr/0017-domain-document-repositories.md)。

## 当前判定

3.1.0 是闭源商用依赖收敛与企业化收口后的 GitHub source release：许可面清零 AGPL/SSPL/双许可障碍（ADR-0015/0016/0018），九域业务事实全部落 PostgreSQL（ADR-0017），租户隔离、原子注册、Web refresh cookie 与多实例放行完成，发布走全量 release workflow 门禁。**Android/iOS 真机与签名发布、云凭据 ObjectStore 实连、生产灾备演练仍未执行**——它们是如实列出的 open gate，不以 mock、YAML 解析或文档勾选替代。

## 架构复核

| 边界 | 当前实现与结论 |
| --- | --- |
| 客户端维护 | Web 与 Expo 移动 source tree 共用根 workspace、lockfile、contracts/api-client/domain/design-tokens/i18n；Native UI 独立，按当前窗口动态布局。企业会话 access token 仅内存 + HttpOnly refresh cookie（`Path=/api/v1/auth`），文件模式保持 30 天 localStorage 兼容。 |
| 许可与分发 | 源码树无 AGPL/SSPL/商业双许可依赖：PyMuPDF→pypdf/pdfplumber/pypdfium2（ADR-0015）、Redis→Valkey（ADR-0016）、MinIO 占位删除；本地 OCR（Tesseract，Apache-2.0）保留。许可策略 zero-gap 门禁 + 每次 release 的 SBOM/NOTICES/SHA256SUMS 产物；移动端内置第三方许可页并受 drift 门禁约束。 |
| 身份基础设施 | PostgreSQL account/credential/tenant/membership 在**单事务**内原子注册，shadow 文件尽力写、失败由 importer 愈合；refresh 轮换双轨（body + cookie），登出撤销家族并清 cookie；并发轮换唯一胜者 + 家族撤销在真实 PG 集成车道验证。 |
| 企业业务存储 | 九域（chat/notes/assessment/orchestration/assistant/evidence/classroom/illustration/library+textbooks）事实落 JSONB 文档表，默认路由 sql，File 实现保留为回滚窗口；对象字节走 ObjectStore 协议（本地默认，S3 兼容 ADR-0018）；BM25/KG/embedding 派生索引保留文件态可重建。 |
| 租户隔离 | 高价值读取按 `tenant_id + resource_id` 过滤，A/B 租户 IDOR 矩阵覆盖 Library/Chat/Notes/Assessment/Evidence/KG/Classroom/Illustration/Assistant/Exports。 |
| 运行协调 | 文件模式仍 fail-fast 单 worker；配置 `DATABASE_URL`（企业模式）后多 worker/多实例放行，跨实例前提（Valkey、Temporal、ObjectStore/共享卷、游客策略）以启动告警 + 文档矩阵约束（ADR-0017 收口）。CI 双实例真实 PG/Valkey 车道持续验证。 |
| Durable jobs | 五队列（documents/classroom/evaluation/media/maintenance）全部 Temporal 化；账号删除走 `account.purge` workflow；worker 恢复语义由真实 server 集成测试作证，生产强杀演练见灾备 runbook open gate。 |
| 并发写入 | 事实层并发由 PostgreSQL 行锁/唯一约束仲裁（双实例车道断言）；评价 journal 与课堂版本在域内保持原锁纪律。 |
| CI 与治理 | 主分支 CI 含 hygiene/docs/notices/license 门禁 + 全后端 shards + 企业/Temporal 集成车道；新增 `release.yml`（tag/手动、无 path skip）：全 shards、PG+Valkey+Temporal、moto ObjectStore、**DR 演练（dump→drop→restore→verify 零漂移）**、全量浏览器回归、mobile 全检 + 许可 drift、contracts/token drift、gitleaks 全历史 secret 扫描、pip-audit/pnpm audit SCA、SBOM/SHA256SUMS 指纹。`v*` tag ruleset 限制创建/更新/删除。单人仓库仍无 required review。 |

## 已执行证据

- 后端聚焦回归：identity/persistence（注册原子性、cookie 轮换、S3 适配器、租户隔离）、bootstrap 门禁、requirements contract 全绿；发布 commit 的完整 shards + 集成车道以 release CI 为准。
- 灾备演练脚本 `scripts/ops/dr_drill.py`：seed/verify 在 scratch 库验证通过并具备篡改检出（漂移 exit 1）；PG dump/restore 全链路在 release workflow 企业 lane 实跑。
- SCA：katex 0.16.47→0.18.11（全树强制，清 GHSA-238p-pmpm-9mq7）、urllib3 2.7.0→2.8.0（清 PYSEC-2026-4175/4176/4177）；生产树 pip-audit 0 漏洞，pnpm audit --prod 在显式接受 3 条无补丁通告后通过（见下）。
- Web：类型、lint、单元通过；全量 Playwright 以 release CI 为准。Mobile：18 suites / 411 tests、tsc、许可数据 drift 通过；设备 binary 证据见 [mobile-validation](mobile-validation.md)。
- 第三方：版本快照、原文许可与 source SBOM 见 [THIRD-PARTY-NOTICES](../../THIRD-PARTY-NOTICES.md) 与 [licenses](../../licenses/README.md)；发布产物（artifact 级 SBOM、SHA256SUMS）由 release lane 生成并随 Release 附带。source SBOM 不代表最终 APK/IPA、容器或可选模型的完整供应链证明。

## 生产发布仍需的证据

SCA 现状如实声明：`node-forge <=1.4.0`（[GHSA-86w9-cpqp-85rv](https://github.com/advisories/GHSA-86w9-cpqp-85rv)）、`braces <=3.0.3`（[GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm)）、`sprintf-js`（[GHSA-hp3w-g68c-fv3c](https://github.com/advisories/GHSA-hp3w-g68c-fv3c)）上游尚无修补版本，且均位于 expo CLI 工具链或自动安装的 jest peer（@testing-library/react-native），不进入应用 bundle；发布 lane 以显式 GHSA ID 接受这三条并在文档留档，**新出现的通告仍会阻断发布**。binary 分发前需按最终安装图重新评估。

1. Android/iOS 签名 release binary、Maestro 核心/全量、多窗口/大字体/VoiceOver/TalkBack/Reduce Motion 与录音/后台播放/锁屏（见 [mobile-validation](mobile-validation.md)）。
2. 云凭据 ObjectStore（真实 S3/R2/自建端点）上传下载与桶版本控制实连记录。
3. 生产 PG 备份/恢复定期演练、Temporal worker kill -9 中断演练（CI 已覆盖同形命令与 durable 语义，生产环境仍需持有者执行并记录，见 [disaster-recovery](disaster-recovery.md)）。
4. 组织 tenant 的 MASVS/MASTG 与 ASVS5 安全审查、SAST 证据；与受保护 tag 绑定的 signed artifact、binary SBOM、商店隐私表与分阶段 rollout/rollback 演练。
