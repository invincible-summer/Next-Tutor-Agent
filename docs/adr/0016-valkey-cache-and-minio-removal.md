# ADR-0016: Valkey 替代 Redis server，移除 MinIO 占位

- 状态：accepted
- 日期：2026-10-06
- 关联：ADR-0010（企业持久化栈）、ADR-0013（durable workflows）；许可治理见 `docs/compliance/dependency-and-sbom.md`。

## Context

两条许可边界与「专有闭源、零付费依赖」的产品政策冲突：

1. **Redis 8 server**：自 7.4 起源码以 RSALv2/SSPLv1/AGPLv3 三选一发布，Redis 8 延续该模式。self-hosted 场景下 RSAL/SSPL 对闭源产品分发的相容性存在实质不确定性，AGPL 则明确不可接受。缓存层不应以许可不确定性换取功能。
2. **MinIO**：AGPL-3.0。它此前仅作为 `deploy/local/docker-compose.yml` 中的可选占位 service 存在，代码从未依赖。

同时，缓存层语义自始就不是业务事实源：Valkey/Redis 只保存限流窗口、lease、短 TTL 会话态，全部可丢弃、可重建（`app/persistence/cache` 的 fail-open 内存回退契约）。Python 客户端 `redis-py` 为 MIT，且 Valkey 保持 RESP 协议兼容，客户端无需更换。

## Decision

- **服务端**：`deploy/local/docker-compose.yml` 与 CI enterprise lane 的服务容器由 `redis:8` 换为 `valkey/valkey:9.1.2`（BSD-3-Clause，锁定 tag，禁 latest）。URL scheme 维持 `redis://`（RESP），环境变量改名为 `CACHE_URL`；`REDIS_URL` 保留一个版本的兼容别名（读取时打一次 deprecation 日志）。
- **不迁移缓存数据**：缓存即弃态，切换时直接弃置旧 volume（`redis-data`→`cache-data`）；业务事实完整性不依赖缓存内容，flushall/断连/整卷丢弃都必须无损失重建。
- **客户端命名对齐**：`app/persistence/cache/redis.py`→`resp.py`，`RedisCachePrimitives`→`RespCachePrimitives`（RESP 协议原语，与具体服务端解耦）；`base.py` 的工厂、health probe、集成测试同步。MIT 的 `redis` 客户端库保留。
- **删除 MinIO**：compose service、volume、README/部署文档、THIRD-PARTY-NOTICES、external-components 清单中的全部引用移除。远程对象存储能力由后续 S3 兼容 adapter（ADR-0018）按需引入，不再保留 AGPL 占位。

## Consequences

- 分发边界内不再含 AGPL/RSAL/SSPL 服务端组件；许可证清单/SBOM/部署文档同步再生成，Valkey COPYING 原文存档于 `licenses/texts/`。
- 运维需知：Valkey 与 Redis 7.x 命令面在本项目用量（INCR/EXPIRE/EVAL lease/GETSET/DEL）完全兼容；升级 Valkey 主版本时仍需按其 release notes 复核。
- `CACHE_URL`/`TEST_CACHE_URL` 成为唯一推荐配置；`REDIS_URL` 别名将在下一版本移除。
- 客户端进程内 fail-open 回退、双进程 lease 协调、compare-and-delete Lua 释放等既有契约不变（集成测试以真实 Valkey 服务容器验证）。
