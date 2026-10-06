# app/persistence — 企业持久化层

PostgreSQL（SQLAlchemy 2.0 async）/ 对象存储 / RESP 缓存（Valkey，ADR-0016）的唯一入口，由
`DATABASE_URL` / `CACHE_URL` 等环境变量激活；未配置时整包惰性（文件模式
行为与历史完全一致，不建 engine、不发起连接）。

```text
persistence/
├── db.py             # async engine/session/transaction factory（URL 归一化 postgresql→asyncpg）
├── models/           # SQLAlchemy 模型（identity.py：§11.2 八表）
├── repositories/     # Protocol + records（纯 dataclass）+ SqlAlchemy 实现
├── object_store/     # ObjectStore 接口 + local 适配（数据根内）；azure/s3 接口位
├── cache/            # RESP（Valkey）限流/lease/短缓存原语；未配置回退进程内实现
└── health.py         # bootstrap /ready 探活（DB critical、cache non-critical）
```

关键纪律：**domain service 不 import SQLAlchemy model**——业务代码只依赖
`repositories/protocols.py` 的协议与 `records.py` 的纯数据对象；模型映射
封在 `repositories/identity.py`。JSON 列用 `JSON().with_variant(JSONB,
"postgresql")` 保持 sqlite 单测通道可跑。

schema 变更只经 Alembic（`services/api/migrations/`，`alembic upgrade
head` 是显式运维动作）；应用启动**从不**自动建表/改表。运行数据迁移见
`scripts/migrations/runtime_to_enterprise/`。

架构文档：[docs/architecture/backend-runtime.md](../../../../docs/architecture/backend-runtime.md)
