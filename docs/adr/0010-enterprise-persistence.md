# ADR-0010: 企业持久化栈（PostgreSQL / Object Storage / Redis）

- 状态：accepted
- 日期：2026-10
- 取代：ADR-0004 的「多 worker 不受支持」结论（文件模式部分仍然有效，见下）

## Context

ADR-0004 冻结了 JSON/JSONL 文件持久层的 single-worker 不变量：没有跨进程锁，多 worker 会互相覆盖写、失去 job/内存态一致性。这排除了水平扩展与高可用部署。企业部署（多实例 API、会话/租户数据、跨实例协调）需要真正的数据库与共享原语，同时不能强迫现有 self-host 单机部署改变形态。

## Decision

以 `DATABASE_URL` 是否配置为唯一开关，引入企业持久化模式：

- **PostgreSQL 为权威持久层**（`app/persistence/`）：SQLAlchemy 2.0 async + asyncpg；models 是 schema 单事实源；Alembic 管理迁移（`services/api/migrations/`，显式 `alembic upgrade`，应用启动绝不执行 DDL）。域服务只依赖 repository Protocol 与纯 dataclass records，不 import SQLAlchemy model。
- **Object Storage**：`app/persistence/object_store/`，namespace + opaque key；数据根内本地文件系统实现为默认，Azure/S3 适配留接口位（未配置远程即本地）。
- **Redis 只存五类短状态**（限流、single-flight/lease、短 TTL 缓存等 `CachePrimitives` 原语）：**不是事实源**；未配置或不可达时回退进程内实现——失去共享层只降级协调（短暂超发限流/重复工作），绝不让请求失败。
- **多 worker 放行条件**：配置 `DATABASE_URL`（企业模式）后 `WEB_CONCURRENCY>1` 合法，写一致性与跨实例仲裁由 PostgreSQL 行级约束承担；文件模式维持 ADR-0004 的 single-worker fail-fast 不变。
- **存量数据迁移**：`scripts/migrations/runtime_to_enterprise/`（scan → import → verify → cutover → report）幂等导入、source hash 校验、cutover 显式确认且不删除旧文件数据。

## Consequences

- 验收锚点：两个独立 engine（模拟双实例）并发读写同一 tenant 由数据库仲裁（唯一约束恰一胜、条件 UPDATE 恰一轮换），CI `backend-enterprise` job 在真 PostgreSQL/Redis 上执行（`tests/persistence/integration.py`）。
- ADR-0004 在文件模式范围内继续成立（原子写、job 认领、single-worker）；仅其「多 worker 不受支持」结论被本 ADR 取代。
- Redis/Sentry 类共享层 outage 属降级事件而非事故：可用性优先于协调精确性。
- self-host profile（`deploy/self-hosted/`）默认仍是文件模式单 worker；迁移完成后可自行选择接入 `deploy/local/` 的 PostgreSQL/Redis 形态。
- 生产基线随之收紧：`AUTH_MODE=1`、强 `AUTH_JWT_SECRET`、受限 `CORS_ORIGINS`（见 deployment.md）。
