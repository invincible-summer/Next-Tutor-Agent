# deploy/self-hosted — 单机 / self-host 部署模板

systemd units、nginx 同源反代示例、MeloTTS sidecar 安装脚本与进程清理工具：单机/self-host profile 的可复制起点。

部署事实源（部署形态、环境要求、生产检查清单）是 [docs/operations/deployment.md](../../docs/operations/deployment.md)；本目录只是它引用的模板文件，README 只做导航。

## Owns

| 文件 | 职责 |
|------|------|
| `edu-backend.service` | 后端 systemd unit：uvicorn `--host 127.0.0.1 --port 8123 --workers 1 --proxy-headers`，`ProtectSystem=strict` / `NoNewPrivileges` 加固，`EnvironmentFile` 加载 `.env`（企业持久化模式下可多 worker，见下） |
| `edu-frontend.service` | 前端 systemd unit：`next start`（依赖 backend unit） |
| `edu-voice-sidecar.service` | 可选 MeloTTS sidecar unit（独立 venv，见 `install_voice.sh`） |
| `nginx.conf.example` | 同源站点模板：TLS（Mozilla intermediate）/HSTS、`/api/*` 反代（**SSE 端点 `proxy_buffering off`**、禁缓存/gzip）、WebSocket upgrade map、上传体积上限 |
| `install_voice.sh` | sidecar 安装：CPU-only PyTorch、固定 revision MeloTTS 源码、模型缓存预取与中文 warmup（产物全部 gitignored） |
| `edu-agent-nginx-reload` | certbot 续期 deploy hook（仅本站 lineage 时 reload nginx） |
| `process_cleanup.py` | `./start.sh stop` 使用的进程树清理工具（按 PID + 启动时间校验，只终止仓库自有进程）；`test_process_cleanup.py` 为其单测 |

## Does not own

- 部署文档与生产检查清单 → [../../docs/operations/deployment.md](../../docs/operations/deployment.md)（权威）。
- MeloTTS sidecar 服务代码 → [../../services/voice/](../../services/voice/README.md)。
- 本地一键启动 → 仓库根 `start.sh` 与 `scripts/dev/start.sh`。
- 本地基础设施（PostgreSQL/Valkey/OTel compose） → [../local/](../local/README.md)。
- `.env.example` → 仓库根（生产密钥 `.env` 永不入库）。

## Design

模板与实际部署分离：本目录文件是可复制的起点（复制到 `/etc/systemd/system/`、`/etc/nginx/` 后按机器调整）；服务器专属细节（域名、证书、备份策略、私有运维手册）由部署方自行维护，不入库。固定约定：专用账号 `edu-agent`（home `/var/lib/edu-agent`，源码 `/opt/edu-agent`），运行数据在 `NEXT_TUTOR_DATA_DIR` 单根（systemd 形态 `/var/lib/edu-agent/data`），`ReadWritePaths` 只放行数据根、前端 `.next` 与 `/var/lib/edu-agent`。

 Worker 策略：文件持久层模式下必须单 worker（ADR-0004）；配置 `DATABASE_URL`（企业持久化模式）后多 worker 放行——九域事实源已全部落 PostgreSQL（ADR-0017 cutover 完成），完整多实例前提矩阵（Valkey `CACHE_URL`、Temporal `TEMPORAL_ADDRESS`、ObjectStore/共享卷、派生索引共享卷、游客策略）见 [../../docs/development/enterprise-infra.md](../../docs/development/enterprise-infra.md)「多实例部署」。self-host 也可选择接入 [../local/](../local/README.md) 同款 PostgreSQL/Valkey 形态。
