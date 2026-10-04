# enterprise-infra — 企业持久化运维手册

企业持久化 lane（ADR-0010）的操作面：环境变量、schema 迁移、文件层→PostgreSQL 数据迁移、本地基础设施、CI 集成车道。架构与数据模型见 [../architecture/backend-runtime.md](../architecture/backend-runtime.md) 与 [../architecture/identity.md](../architecture/identity.md)。

## 模式开关：`DATABASE_URL`

一切以 `DATABASE_URL` 是否配置为准（`app/persistence/db.py::enterprise_mode()`）：

- **未配置（默认，文件模式）**：行为与历史完全一致；persistence lane 惰性（构造 engine 即 RuntimeError）；`WEB_CONCURRENCY>1` 启动 fail-fast（ADR-0004）；会话端点 409 `enterprise_auth_required`。
- **已配置（企业模式）**：身份注册/登录双写 PostgreSQL（ADR-0011 影子模式）、轮换会话可用、多 worker 放行、启动时探测 DB 连通性（critical 失败拒启）。

```bash
DATABASE_URL=postgresql://user:pass@host:5432/tutor   # 应用读取；postgresql:// 自动升 asyncpg
EDU_MIGRATION_DATABASE_URL=...                        # 仅 alembic 用的独立目标（可选）
```

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `DATABASE_URL` | — | PostgreSQL 连接串；配置即企业模式 |
| `REDIS_URL` | — | 共享缓存原语（限流/lease/短 TTL）；未设或不可达回退进程内实现，请求绝不因 Redis 失败 |
| `OBJECT_STORE_ROOT` | 数据根 `object_store/` | 对象存储本地实现根（namespace + opaque key）；远程适配为接口位 |
| `AZURE_STORAGE_*` | — | 远程对象存储占位（未实现，配置不生效） |
| `AUTH_ACCESS_TOKEN_SECONDS` | `900` | RS256 access token 寿命 |
| `AUTH_REFRESH_SESSION_DAYS` | `30` | refresh 会话族寿命 |
| `OTEL_TRACES_ENABLED` | `0` | OTel 装配总开关（需安装 `requirements-observability.txt`） |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OTLP gRPC 端点（如 `http://localhost:4317`） |
| `OTEL_SERVICE_NAME` | `next-tutor-api` | 服务名 |

依赖分 lane：基础 `requirements.txt`（SQLAlchemy/asyncpg/alembic/redis/cryptography 均在基础 lane）；OTel 为可选 `requirements-observability.txt`；全部受 `constraints.txt` exact pin（契约测试 `tests/core/test_requirements_contract.py`）。

## Schema 迁移（Alembic）

models 是 schema 单事实源（`app/persistence/models/`）；**应用启动绝不执行 DDL**，升库是显式运维动作：

```bash
cd services/api
alembic upgrade head        # 空库 → 最新
alembic check               # models 与库零漂移守卫（CI 契约测试同款）
alembic downgrade base      # 仅演练/回退（生产慎用）
```

新增迁移：改 models → `alembic revision --autogenerate -m "..."` → 人工审阅版本文件 → 契约测试（空库可升 + 零漂移）随平台分片运行。

## 存量数据迁移（runtime → enterprise）

`scripts/migrations/runtime_to_enterprise/`（README 见该目录），状态文件在数据根 `migrations/runtime_to_enterprise/state.json`：

```bash
python3 scripts/migrations/runtime_to_enterprise/scan.py     # 只读清点（email 默认哈希输出）
python3 scripts/migrations/runtime_to_enterprise/import.py   # 幂等导入（tnt/mem/crd 由 user_id 哈希派生，不碰文件侧）
python3 scripts/migrations/runtime_to_enterprise/verify.py   # 双侧 count/hash/引用校验（失败 exit 1）
python3 scripts/migrations/runtime_to_enterprise/cutover.py --confirm enterprise-cutover   # 显式切换（source hash 变更/冲突/verify 失败均拒绝）
python3 scripts/migrations/runtime_to_enterprise/report.py   # 迁移报告
```

cutover 不删除任何旧文件数据；回滚 = 停用 `DATABASE_URL`。

## 本地基础设施

`deploy/local/docker-compose.yml`（PostgreSQL 18 / Redis / MinIO；`temporal`、`observability` 分 profile）：用法与环境变量对应见 [../../deploy/local/README.md](../../deploy/local/README.md)。应用本身仍由 `./start.sh` 在宿主机运行。

## 验收与 CI

- **双实例并发验收**（ADR-0010 验收锚点）：`services/api/tests/persistence/integration.py::TwoInstanceTenantConcurrencyTest` —— 两个独立 engine 并发读写同一 tenant、唯一约束仲裁冲突写入、并发 refresh 恰一胜一败且败方撤族。
- 该模块不带 `test_` 前缀：普通 CI 分片与本地全量不发现它；`TEST_DATABASE_URL`/`TEST_REDIS_URL` 未设时全部 skip。
- CI `backend-enterprise` job（`.github/workflows/ci.yml`）：persistence/migrations/依赖/CI 配置路径变更或 push 到 main 时触发，起 postgres:18 + redis:8 service containers 跑上述模块，并纳入 `CI result` 聚合（路径门控由 `enterprise-paths` job 决定，跳过时显式校验其 skipped）。
- 本地手动运行：

```bash
TEST_DATABASE_URL=postgresql://tutor:tutor@localhost:5432/tutor_test \
TEST_REDIS_URL=redis://localhost:6379/0 \
  python3 -m tests tests.persistence.integration
```

## 测试索引

- 单元 lane（沙箱 sqlite）：`tests/persistence/test_db_and_models.py`（engine/models/repository）、`test_object_store_and_cache.py`（object store + 内存缓存原语）、`test_migrations.py`（Alembic 契约）、`test_runtime_import.py`（迁移 CLI 冒烟）
- 企业认证：`tests/identity/test_enterprise_auth.py`（双写、token 双轨、轮换/撤族、会话管理、文件模式 409）
- 可观测性：`tests/observability/test_observability.py`（request-id、脱敏、OTel 降级）
- CI 分片归属：`scripts/repo/plan_backend_shards.py` 的 `persistence` / `observability` shard
