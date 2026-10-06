# ADR-0014: 完整领域 cutover 前保持单 API 实例

- 状态：superseded by [ADR-0017](./0017-domain-document-repositories.md)（九域 cutover 完成后，企业模式的多 worker/多实例禁令解除：`create_app` 对 `WEB_CONCURRENCY>1` 在 `DATABASE_URL` 已配置时放行并对缺项前提告警；多实例完整矩阵见 docs/development/enterprise-infra.md「多实例部署」）；文件模式（无 `DATABASE_URL`）内本 ADR 结论继续有效
- 日期：2026-10-05
- 取代：ADR-0010 仅凭 DATABASE_URL 放行多 worker 的条件；不更改其数据库、对象存储和缓存的所有权设计。

## Context

身份与轮换会话的数据库仲裁已经落地。聊天、课堂、笔记、学习证据和其他领域仍使用 JSON/JSONL 文件与进程内业务缓存；游客状态也存在进程内。两个数据库 engine 的并发测试只能证明该 repository 的仲裁，不能证明全产品多实例安全。

## Decision

当前 API 必须单实例、单 uvicorn worker。create_app 对 WEB_CONCURRENCY 不等于整数 1 fail-fast，无 DATABASE_URL 例外。部署清单也固定单 API 实例；仅靠环境变量不能检测手动启动的第二实例，运维必须遵守此边界。

解除保护需独立决定和验收：所有可变领域事实源进入 tenant-aware repositories、对象存储接口落实、游客与租约跨实例协调，以及 API/worker 并发写入和恢复测试通过。Temporal 的执行所有权移交不等于领域存储迁移；使用现有文件调度器时各 task queue 保持单 worker 消费者，由共享数据根的 OS advisory lock 在 worker 入口拒绝重复消费者。文件读改写临界区也增加同 key 的可重入 OS 锁；这不代替领域缓存失效和数据库 cutover。

## Consequences

数据库会话与基础设施仍可独立使用，生产规模受领域文件存储约束。源码发布与基础设施集成测试不能被描述为全产品企业 cutover 或横向扩展验收通过。
