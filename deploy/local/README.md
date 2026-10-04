# deploy/local — 本地基础设施（Docker Compose）

只提供依赖服务，不跑应用本身：应用仍由仓库根 `./start.sh dev`（或直接 uvicorn）在宿主机启动，便于调试。后端如何接入这些服务见根 `.env.example` 的企业持久化/可观测性段落。

## Owns

| 文件 | 职责 |
|------|------|
| `docker-compose.yml` | PostgreSQL 18 / Redis 8 / MinIO（127.0.0.1 绑定；`temporal`、`otel-collector` 分 profile） |
| `otel-collector.yaml` | 本地 OTLP collector 配置（接收 4317/4318，debug 导出） |

## 使用

```bash
cd deploy/local
docker compose up -d postgres redis              # 企业持久化（DATABASE_URL/REDIS_URL）
docker compose --profile observability up -d     # + 本地 OTel sink（OTEL_* env）
docker compose --profile temporal up -d          # + durable workflow 占位（Stage C 未实现）
```

对应环境变量（本地回环默认口令，见 compose 注释）：

- `DATABASE_URL=postgresql://tutor:tutor@localhost:5432/tutor`（建表走 `alembic upgrade head`，应用启动不做 DDL）
- `REDIS_URL=redis://localhost:6379/0`（可选；未设/不可达时回退进程内原语）
- `OTEL_TRACES_ENABLED=1` + `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317`
- MinIO（9000/9001）：远程对象存储适配落地前的 wiring 演练位

## Does not own

- 应用进程与构建 → 仓库根 `./start.sh`、`scripts/dev/start.sh`。
- 单机/self-host systemd/nginx 模板 → [../self-hosted/](../self-hosted/README.md)。
- 部署文档与生产检查清单 → [docs/operations/deployment.md](../../docs/operations/deployment.md)（权威）。
- CI 中的真 PostgreSQL/Redis 集成测试 → `.github/workflows/ci.yml` 的 `backend-enterprise` job（与本地同一迁移集）。
