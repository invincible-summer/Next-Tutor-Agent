# 生产部署（同机单机形态）

本文档是生产部署事实源：部署形态、环境要求、systemd/nginx 模板的使用方式与生产检查清单。模板文件在 `deploy/` 目录；服务器专属的详细运维手册（域名、证书、备份策略等）由部署方自行维护，不入库。

## 部署三形态（`NEXT_PUBLIC_BACKEND_URL` 单一真相源）

1. **开发/跨域生产**：`NEXT_PUBLIC_BACKEND_URL=http(s)://<host>:<port>` 构建 → 客户端直连（需 CORS 放行）。
2. **同源生产（推荐）**：不设该变量 → 相对路径 `/api/v1`，nginx 反代 `/api/*` 到后端（SSE 需 `proxy_buffering off`）。
3. **本地一键**：`./start.sh` 先探测后端/前端实际端口，再同步 `NEXT_PUBLIC_BACKEND_URL` 与本地 `CORS_ORIGINS`；子进程统一直连网络，不继承 shell 代理。前端默认生产模式（构建时烤入后端端口并记录于 `.next/edu-build-port`，端口漂移自动重建）。

## 环境要求

- Python 3.11（`services/api/requirements.txt` + `services/api/constraints.txt`）；BM25 基础环境**不安装**可选向量/本地模型 requirements（见 [ADR-0003](../adr/0003-bm25-baseline.md)、[semantic-rag.md](./semantic-rag.md)）。
- Node.js 22 + pnpm（版本由根 `package.json` 的 `packageManager` 固定；依赖从仓库根 `pnpm install --frozen-lockfile` 安装，唯一 lockfile 在根目录）。
- 后端 worker 策略：JSON 文件持久层模式下固定**单 uvicorn worker**（`--workers 1`，[ADR-0004](../adr/0004-single-worker-persistence.md)）；配置 `DATABASE_URL`（企业持久化模式）后多 worker 放行，一致性由 PostgreSQL 与共享缓存层承担（[ADR-0010](../adr/0010-enterprise-persistence.md)）。
- 课堂模式 renderer（可选能力）：需要 Node + Playwright Chromium，以服务账号运行、禁止 `--no-sandbox`；部署期在仓库根执行 `pnpm install --frozen-lockfile && pnpm --filter @next-tutor/web run build:classroom` 生成 `services/api/app/classroom/static/generated/`。缺失时课堂能力端点显式 `renderer_unavailable`，普通聊天不受影响。
- 语音 sidecar（可选）：`deploy/self-hosted/install_voice.sh` 安装 MeloTTS 栈（独立 venv + CPU torch + 模型预热），模型缓存 gitignored，不入库（见 [../compliance/voice-licenses.md](../compliance/voice-licenses.md)）。
- 企业持久化（可选形态）：配置 `DATABASE_URL`（PostgreSQL）+ 可选 `REDIS_URL` 后，身份/会话进入企业模式、多 worker 放行（[ADR-0010](../adr/0010-enterprise-persistence.md)）；本地基础设施 compose 在 `deploy/local/`，schema 迁移、存量数据迁移与变量清单见 [../development/enterprise-infra.md](../development/enterprise-infra.md)。

## 运行数据

所有运行数据统一在 `NEXT_TUTOR_DATA_DIR` 单根之下（[ADR-0002](../adr/0002-runtime-data-root.md)）。systemd 部署中该根指向 `/var/lib/edu-agent/data`，与源码目录 `/opt/edu-agent` 分离；`ReadWritePaths` 只放行该数据根、前端 `.next` 与 `/var/lib/edu-agent`。

## systemd 与 nginx

模板（复制到 `/etc/systemd/system/` 后按机器调整）：

- `deploy/self-hosted/edu-backend.service`：uvicorn `--host 127.0.0.1 --port 8123 --workers 1 --proxy-headers`；`ProtectSystem=strict`/`ProtectHome=true`/`NoNewPrivileges` 等加固；`EnvironmentFile` 加载 `.env`。
- `deploy/self-hosted/edu-frontend.service`：`next start`（依赖 backend unit）。
- `deploy/self-hosted/edu-voice-sidecar.service`：可选 MeloTTS sidecar（独立 venv）。
- `deploy/self-hosted/nginx.conf.example`：同源站点模板——TLS（Mozilla intermediate 档）、HSTS、`/api/*` 反代（**SSE 端点禁用 proxy_buffering/缓存/gzip**）、WebSocket upgrade map、`client_max_body_size`（前端 10m、API 按上传端点放宽）。
- `deploy/self-hosted/edu-agent-nginx-reload`：certbot 续期 deploy hook（仅本站 lineage 时 reload）。
- `deploy/self-hosted/process_cleanup.py`：只终止仓库自有进程树的停止工具（`./start.sh stop` 使用）。

专用系统账号模型：`edu-agent` 用户/组，home `/var/lib/edu-agent`，`nologin` shell，源码位于 `/opt/edu-agent`；`.env`/密钥 0600，运行数据目录 0700。

## 生产检查清单

`.env` 至少满足（模板见 `.env.example` 尾部）：

- `AUTH_MODE=1`；
- 替换强随机 `AUTH_JWT_SECRET`（使用默认值时 `AUTH_MODE=1` 拒绝启动）；
- `CORS_ORIGINS` 白名单（同源部署可不放开跨域；禁 `*`）；
- `chmod 600 .env`；
- LLM `LLM_API_KEY` 等凭证只放 `.env`，绝不入库或日志；
- 管理员 `ADMIN_EMAIL`/`ADMIN_PASSWORD` 引导（防抢注提权语义见 [../architecture/identity.md](../architecture/identity.md)）。

部署后验证：`GET /api/v1/health`、`GET /api/v1/ready`；按 [../development/testing.md](../development/testing.md) 执行同机回归（后端全量、前端检查/构建、浏览器冒烟）。

## 运维注意

- 改 agent 管线代码后必须重启 uvicorn（无热重载假设）。
- `start.sh` 默认 `SUPERVISOR_MODE=v2`；排障可 `SUPERVISOR_MODE=legacy ./start.sh` 对比。
- 各智能层开关可用于二分定位故障层（开关总表见 [../architecture/backend-runtime.md](../architecture/backend-runtime.md)）。
- OpenAI 兼容门面（第三方平台接入）默认关闭，需显式配置 `COMPAT_API_KEY`。
