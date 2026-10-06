# deploy — 部署模板与本地基础设施

| 目录 | 职责 |
|------|------|
| [`local/`](local/README.md) | Docker Compose 基础设施（PostgreSQL 18 / Valkey / Temporal 占位 / OTel collector），只提供依赖，应用仍由 `./start.sh` 启动 |
| [`self-hosted/`](self-hosted/README.md) | 单机/self-host profile 模板：systemd units、nginx 同源反代示例、MeloTTS sidecar 安装脚本、进程清理工具 |

部署事实源（部署形态、环境要求、生产检查清单）是 [docs/operations/deployment.md](../docs/operations/deployment.md)；本目录只放它引用的模板文件，README 只做导航。
