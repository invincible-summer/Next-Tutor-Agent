# Platform release validation

复核日期：2026-10-05。产品版本：3.0.0。移动客户端证据与设备矩阵见 [mobile-validation](mobile-validation.md)，数据迁移边界见 [enterprise-infra](../development/enterprise-infra.md)，运行限制见 [ADR-0014](../adr/0014-single-instance-until-domain-cutover.md)。

## 当前判定

3.0.0 是包含原生移动客户端源码的 GitHub source release。当前证据支持源码、共享协议、JS/资源打包和离线回归验收；**企业生产改造及 Android/iOS 商店发布的完整验收未通过**。不能将 PostgreSQL 身份基础设施的双实例测试推断为所有业务支持多实例，也不能将 Hermes export 推断为签名 native binary 或真机回归。

## 架构复核

| 边界 | 当前实现与结论 |
| --- | --- |
| 客户端维护 | Web 与一个 Expo 移动 source tree 共用根 workspace、lockfile、contracts/api-client/domain/design-tokens/i18n；Native UI 独立，按当前窗口动态布局，无平板业务分支。 |
| 移动首页与导航 | 原生学习空间首页、五区导航、宽窗口 rail/panes；助手默认右边缘带边框半隐藏；跨模块只使用允许的 typed targets，草稿先展示编辑表单，再由用户保存。 |
| 数据事实 | 客户端业务缓存与草稿在内存；refresh 凭据在 SecureStore，服务器保留正式业务事实。移动端未内置 LLM/RAG/OCR/TTS 或云 provider 密钥。 |
| 学习闭环 | 聊天/教材/测评 V1/V2/V3/情景配图/素材库/图谱/笔记/计划/洞察记忆/课堂均提供移动 UI 与共享 API adapter；实际系统选择器、录音和两端状态一致仍需设备执行。 |
| 富文本与画布 | Native Markdown、离线分组 KaTeX WebView；图谱与 SVG 素材用 native SVG。课堂仅画布使用服务器 renderer 的受限 WebView，HTML/hash/CSP/nonce 检查已做离线验证。原生视觉、辅助阅读、500 消息性能及 WebView 渗透测试尚无设备证据。 |
| 身份基础设施 | PostgreSQL account/tenant/membership/refresh session 已接入。shadow profile 仍写文件，读取只覆盖 profile，数据库 role/token_version/credential 保持权威；注册的多个 repository transaction 尚非整笔原子注册。 |
| 企业业务存储 | 业务仍有 JSON/JSONL/Markdown、文件对象和进程缓存；全域 PostgreSQL/Object Storage 切库与组织租户查询隔离未完成。当前不能对外承诺任意 tenant 的企业隔离。 |
| 运行协调 | API 仍要求一个实例与 `WEB_CONCURRENCY=1`。文件锁新增同一共享文件系统的 OS advisory lock，Worker 按 data root/task queue 独占入口；这不提供跨主机数据库事务或全部领域缓存一致性。 |
| Durable jobs | 教材、课堂、Quiz/Scenario Illustration、评价、维护复用领域代码与 Temporal workflow/activity。恢复读取暂时不可用时不伪造任务终止；单 queue 消费约束仍需遵守。实际部署的强杀、跨环境恢复、预算与对象 orphan 演练仍是独立发布门。 |
| 并发写入 | 课堂版本分配在一次 metadata mutation 内；评价 journal 在同锁内检查持久文件变化，空重写保留 generation。各领域跨文件写入不构成统一 ACID 事务。 |
| CI 与治理 | Mobile 配置/类型/单元/Doctor/CNG/export 加入必需 CI aggregate；主分支要求 strict `CI result`、conversation resolution、禁止 force push/deletion。`v*` tag active ruleset 限制创建、更新与删除（仓库管理员有 bypass）。当前单人仓库没有 required review，不能称为完整企业评审规则。 |

## 已执行证据

- 完整后端：3040 tests 通过。之后的并发修补分别运行相关 focused regressions；发布 commit 的 CI 仍必须再次通过。
- Web：完整 125 项 Chromium E2E 通过，包含 light/dark 与较窄窗口；类型、lint、单元及生产 build 通过。后续依赖更新后的结果以发布 CI 为准。
- Shared packages：类型检查通过；API client 130 项测试通过，包含 refresh/session、二进制、SSE、上传、课堂幂等 QA 与各业务 adapter。
- Mobile：18 suites / 411 tests 通过；Android/iOS Hermes export、clean CNG 与 Expo SDK 校验按 [mobile-validation](mobile-validation.md) 记录。Maestro 资产已解析，未作为设备测试结果。
- 第三方：版本快照、原文许可与 source SBOM 见 [THIRD-PARTY-NOTICES](../../THIRD-PARTY-NOTICES.md) 与 [licenses](../../licenses/README.md)。source SBOM 不代表最终 APK/IPA、容器或可选模型的完整供应链证明。

## 生产发布仍需的证据

本轮官方 npm registry SCA 在修补 Next.js、sharp、uuid 和 decode-uri-component 后，仍报告 `node-forge <=1.4.0` 的 [RSA 验证问题](https://github.com/advisories/GHSA-86w9-cpqp-85rv) 与 `braces <=3.0.3` 的 [深层模式拒绝服务问题](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm)，当前上游未提供修补版本。保留真实扫描状态；**不宣称依赖安全扫描全部通过**。部署或发布 binary 前需基于最终安装图、可达调用与上游修补重新评估，不把开发/打包用途推断为零风险。

在 Python 3.11 隔离环境按 `services/api/requirements*.txt` 运行 pip-audit，本次结果为 60 个解析包、0 个已报告漏洞；这不覆盖可选 voice 环境、容器系统包或最终 native binary。PyJWT 已升至 2.15.1，Pydantic/PyMuPDF 采用兼容补丁版本。

1. Android/iOS 签名 release binary、Maestro 核心/全量、手机/平板/折叠/iPad 窗口、系统大字体、VoiceOver/TalkBack、Reduce Motion 与录音/后台播放/锁屏。
2. 全业务企业存储迁移、旧 runtime 导入核对、账户删除/retention 等价、对象 orphan、备份恢复及缓存层（Valkey）丢失演练。
3. 组织 tenant 的高价值 API/worker 越权回归、MASVS/MASTG 与 ASVS5 安全审查、SCA/secret/SAST 证据。
4. 与同一受保护 commit/tag 绑定的 signed artifact、binary SBOM、商店隐私表、分阶段 rollout、签名 OTA 与 rollback 演练。

上述证据缺失不会被测试 mock、YAML 解析或文档勾选替代；保持当前单实例约束，直到对应业务切换与验收完成。
