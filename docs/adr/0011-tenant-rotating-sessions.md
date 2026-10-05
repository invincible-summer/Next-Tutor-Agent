# ADR-0011: 租户模型与轮换认证会话

- 状态：accepted
- 日期：2026-10

## Context

身份模型原先只有单层 user（文件层 `users/*.json`），认证是单个 HS256 JWT（30 天有效期、无会话概念）：无法表达组织/班级归属，无法列出或撤销单设备会话，token 泄露只能全设备作废（token_version）。企业形态需要 tenant/membership 与可轮换、可撤销、可审计的会话族。

## Decision

- **Principal 模型**：`RequestPrincipal{user_id, tenant_id, membership_id, tenant_role, platform_role, auth_session_id}`（`app/identity/principal.py`）；`resolve_student_id()` 仍是唯一可信学生标识，行为不变；route 永不信任 body 中的 owner/tenant 字段。
- **数据模型**（PostgreSQL，`app/persistence/models/identity.py`）：users / credentials / tenants / memberships / auth_sessions / refresh_tokens / identity_providers / audit_events 八表；注册自动创建 personal tenant + owner membership。
- **Token 双轨**：
  - 新轨：RS256 access token（`kid` 头，15 分钟，claims `sub/ver/sid/tenant/typ=access`）+ opaque refresh token（`rt_*`，仅存 SHA-256 hash，30 天会话族）；每次 refresh 轮换，旧 token 复用即视为盗窃并撤销整个 family（条件 UPDATE 仲裁，跨实例原子）。
  - 旧轨：legacy HS256 30 天 token 继续作为 `token` 字段签发（Web 客户端 localStorage 语义未迁移前的兼容窗口）；认证依赖先验 RS256（查会话活性）再回落 legacy；声称 `typ=access` 的 HS256 token 一律拒绝。
  - 签名密钥：本地 RSA keyring（数据根外 `auth_keys/`，0600，active kid 可轮换），KMS/Key Vault 留接口位（`SigningKeyring` Protocol）。
- **新端点**：`POST /auth/refresh`、`GET /auth/sessions`、`DELETE /auth/sessions/{id}`、`GET /auth/principal`；登录/换密/强制注销写 `audit_events`；`token_version` 全设备撤销语义保留。
- **会话活性判定带 10s 短缓存**（Redis 共享层优先，回退进程内）——撤销传播窗口以秒计，安全敏感路径（refresh）直查数据库不受缓存影响。

## Consequences

- **双写影子模式（记录在案的过渡偏差）**：企业模式下注册/登录同时写文件层（旧消费方仍以文件为事实源）与 PostgreSQL（会话/租户权威）；profile 类字段窗口期仍单写文件层。待文件层消费方全部迁移后收敛为 PG 单写——本 ADR 明确该偏差与收敛方向，避免被当作永久状态。
- 会话/凭据数据永远不进日志（redaction 基线，`app/observability/redaction.py`）。
- 文件模式（无 `DATABASE_URL`）下会话端点显式 409 `enterprise_auth_required`，登录/注册行为与历史完全一致。
