# enterprise-infra — 企业持久化与 durable workflow 运维手册

企业持久化 lane（ADR-0010）与 durable workflow lane（ADR-0013）的操作面：环境变量、schema 迁移、文件层→PostgreSQL 数据迁移、Temporal worker 运行、本地基础设施、CI 集成车道。架构与数据模型见 [../architecture/backend-runtime.md](../architecture/backend-runtime.md) 与 [../architecture/identity.md](../architecture/identity.md)。


**API 部署约束：当前必须单 API 实例、单 uvicorn worker，`WEB_CONCURRENCY=1`。**
配置 `DATABASE_URL` 只启用已落地的身份/会话和持久化基础设施；聊天、课堂、笔记、
学习证据等领域仍使用文件事实源和进程内锁，不能因此横向扩容。文件业务全部 cutover、
游客协调跨进程和并发恢复验收完成前，数据库双实例测试不代表全产品多实例安全。

## 模式开关：`DATABASE_URL`

一切以 `DATABASE_URL` 是否配置为准（`app/persistence/db.py::enterprise_mode()`）：

- **未配置（默认，文件模式）**：行为与历史完全一致；persistence lane 惰性（构造 engine 即 RuntimeError）；`WEB_CONCURRENCY>1` 启动 fail-fast（ADR-0004）；会话端点 409 `enterprise_auth_required`。
- **已配置（企业模式）**：身份注册/登录双写 PostgreSQL（ADR-0011 影子模式）、轮换会话可用、仍须单 API 实例和单 worker、启动时探测 DB 连通性（critical 失败拒启）。

```bash
DATABASE_URL=postgresql://user:pass@host:5432/tutor   # 应用读取；postgresql:// 自动升 asyncpg
EDU_MIGRATION_DATABASE_URL=...                        # 仅 alembic 用的独立目标（可选）
```

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `DATABASE_URL` | — | PostgreSQL 连接串；配置即企业模式 |
| `CACHE_URL` | — | 共享缓存原语（限流/lease/短 TTL，RESP 连接 Valkey，ADR-0016；旧 `REDIS_URL` 保留一版兼容）；未设或不可达回退进程内实现，请求绝不因缓存失败 |
| `OBJECT_STORE_ROOT` | 数据根 `object_store/` | 对象存储本地实现根（namespace + opaque key）；远程适配为接口位 |
| `AZURE_STORAGE_*` | — | 远程对象存储占位（未实现，配置不生效） |
| `AUTH_ACCESS_TOKEN_SECONDS` | `900` | RS256 access token 寿命 |
| `AUTH_REFRESH_SESSION_DAYS` | `30` | refresh 会话族寿命 |
| `OTEL_TRACES_ENABLED` | `0` | OTel 装配总开关（需安装 `requirements-observability.txt`） |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OTLP gRPC 端点（如 `http://localhost:4317`） |
| `OTEL_SERVICE_NAME` | `next-tutor-api` | 服务名 |
| `TEMPORAL_ADDRESS` | — | Temporal frontend `host:port`；未设=各域保持进程内任务执行（零行为变化），已设=API 不再启动对应 in-process worker，需另跑 worker 进程 |
| `TEMPORAL_NAMESPACE` | `default` | Temporal namespace |

依赖分 lane：基础 `requirements.txt`（SQLAlchemy/asyncpg/alembic/redis 客户端/cryptography/temporalio 均在基础 lane）；OTel 为可选 `requirements-observability.txt`；全部受 `constraints.txt` exact pin（契约测试 `tests/core/test_requirements_contract.py`）。

## Durable workflow lane（ADR-0013）

`TEMPORAL_ADDRESS` 是唯一开关（`app/workflows/config.py::temporal_configured()`），与 `DATABASE_URL` 同一降级范式：

- **未配置（默认）**：整层惰性，各域后台任务保持进程内执行；`worker.py` 拒绝启动（exit 2）。
- **已配置**：API 进程只经 Temporal Client 提交/取消/查询 workflow；执行都在 worker 进程。

```bash
cd services/api
python worker.py                          # 服务全部五个 task queue
python worker.py --queues documents       # 子集运行（队列独立扩容）
```

- Task queue 划分（`app/workflows/runtime.py`）：`documents` / `classroom` / `evaluation` / `media` / `maintenance`。
- 域事实源不变（job.json/journal 等仍由域代码原子写）；workflow id 由域 job id 稳定派生（`join_workflow_id`）。
- 已迁移域的启动恢复移到 worker 启动时执行（API lifespan durable 分支不再驱动，避免双进程重复入队）；每条队列保持单 worker 实例消费（worker 入口通过数据根内的 OS advisory lock 拒绝重复消费者）（域调度器在 worker 进程内存中，与文件模式同一约束，见 ADR-0013「调度策略优先复用域调度器」）。
- 各域迁移状态表见 [../adr/0013-durable-workflows.md](../adr/0013-durable-workflows.md)——五域已全部迁移。
- 维护定时（briefing tick / trash cleanup / assistant 草稿清扫）由 worker 启动时幂等注册的 Temporal Schedule 驱动（间隔变更需删除对应 Schedule 后重启 worker 重建）；账号删除（`DELETE /account`、管理员删用户）在 durable 模式经 `account.purge` workflow 执行，路由 await 终态、响应契约与 file 模式一致。
- systemd 部署：`deploy/self-hosted/edu-worker.service`（与 backend 同账号/数据根/加固）。
- 本地 Temporal：`cd deploy/local && docker compose --profile temporal up -d`（端口 127.0.0.1:7233，库建在同一 compose 的 PostgreSQL 上）。

## Schema 迁移（Alembic）

models 是 schema 单事实源（`app/persistence/models/`）；**应用启动绝不执行 DDL**，升库是显式运维动作：

```bash
cd services/api
alembic upgrade head        # 空库 → 最新
alembic check               # models 与库零漂移守卫（CI 契约测试同款）
alembic downgrade base      # 仅演练/回退（生产慎用）
```

新增迁移：改 models → `alembic revision --autogenerate -m "..."` → 人工审阅版本文件 → 契约测试（空库可升 + 零漂移）随平台分片运行。

## 域文档表（ADR-0017）

九个业务域（chat / library / textbooks / notes / assessment / evidence / classroom / orchestration / assistant+illustration）的事实记录进同构 JSONB 表 `<domain>_documents`（migration 0002）：`tenant_id + owner_id + kind + doc_id + payload + epoch`，唯一键即四元组。访问统一走 `app/persistence/documents/`（`DocumentRepository` 协议 + `SqlDocumentRepository`）：`put(expected_epoch=…)` compare-and-swap、`mutate()` 单事务行锁 read-modify-write、`purge_owner()` 账号删除通道。对象字节不进 JSONB（走 ObjectStore）；BM25/KG/embedding 派生索引保持文件态（可重建缓存）。

**逐域路由**（cutover 期间每域独立开关）：

```bash
DOMAIN_DOCUMENT_BACKENDS=""                      # 默认：全部走文件实现（cutover 完成前）
DOMAIN_DOCUMENT_BACKENDS="chat=sql"              # 单域试点
DOMAIN_DOCUMENT_BACKENDS="all"                   # 九域全量 SQL（cutover 完成后默认）
DOMAIN_DOCUMENT_BACKENDS="default=sql,chat=file" # 全量后单域回退
```

`sql_enabled(domain)` 同时要求 `DATABASE_URL`；未配置企业库时任何 flag 都不会路由到 SQL。单测 lane：`tests/persistence/test_domain_documents.py`（sqlite，含账号 purge 清空九域行的契约）；账号删除链路（`core/account_data.purge_account`）在企业模式用独立短命引擎清空九域行。

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

### 域文档导入（ADR-0017）

```bash
python3 scripts/migrations/runtime_to_enterprise/import_documents.py --domain chat           # 幂等导入（逐域落地，walker 陆续加入）
python3 scripts/migrations/runtime_to_enterprise/import_documents.py --domain chat --verify # 双侧 count + payload 摘要比对，漂移 exit 1
```

已支持域：`chat`（会话/转写/trace 引用）、`notes`（仓库索引/正文/修订/智能体状态）、`evidence`（学习证据 journal + 学生档案）、`orchestration`（编排工作集 + 事件日志）、`assistant`（学习助手会话 + 交接草稿）、`classroom`（owner 记录/课程/生成任务/播放 run）。

导入不覆盖既有 SQL 行（cutover 后 SQL 侧写入优先）；状态记入同一 `state.json`。

## 本地基础设施

`deploy/local/docker-compose.yml`（PostgreSQL 18 / Valkey 9.1；`temporal`、`observability` 分 profile）：用法与环境变量对应见 [../../deploy/local/README.md](../../deploy/local/README.md)。应用本身仍由 `./start.sh` 在宿主机运行。

## 验收与 CI

- **双实例并发验收**（ADR-0010 验收锚点）：`services/api/tests/persistence/integration.py::TwoInstanceTenantConcurrencyTest` —— 两个独立 engine 并发读写同一 tenant、唯一约束仲裁冲突写入、并发 refresh 恰一胜一败且败方撤族。
- 该模块不带 `test_` 前缀：普通 CI 分片与本地全量不发现它；`TEST_DATABASE_URL`/`TEST_CACHE_URL` 未设时全部 skip。
- CI `backend-enterprise` job（`.github/workflows/ci.yml`）：persistence/migrations/依赖/CI 配置路径变更或 push 到 main 时触发，起 postgres:18 + valkey:9.1.2 service containers 跑上述模块，并纳入 `CI result` 聚合（路径门控由 `enterprise-paths` job 决定，跳过时显式校验其 skipped）。
- Durable workflow 集成：`tests/workflows/integration.py` 由 `TEST_TEMPORAL_ADDRESS` 门控（未设全部 skip；workflow 确定性测试用 temporalio 内置 test server，随 `workflows` 分片常规运行）。本地手动运行：

```bash
TEST_DATABASE_URL=postgresql://tutor:tutor@localhost:5432/tutor_test \
TEST_CACHE_URL=redis://localhost:6379/0 \
  python3 -m tests tests.persistence.integration

TEST_TEMPORAL_ADDRESS=127.0.0.1:7233 \
  python3 -m tests tests.workflows.integration
```

## 测试索引

- 单元 lane（沙箱 sqlite）：`tests/persistence/test_db_and_models.py`（engine/models/repository）、`test_object_store_and_cache.py`（object store + 内存缓存原语）、`test_migrations.py`（Alembic 契约）、`test_runtime_import.py`（迁移 CLI 冒烟）
- 企业认证：`tests/identity/test_enterprise_auth.py`（双写、token 双轨、轮换/撤族、会话管理、文件模式 409）
- 可观测性：`tests/observability/test_observability.py`（request-id、脱敏、OTel 降级）
- Durable workflow：`tests/workflows/*`（门控/queue/worker 入口、textbook/classroom/evaluation/media/maintenance 各域确定性 workflow 与双模式接缝测试；真服务器集成车道由 `TEST_TEMPORAL_ADDRESS` 门控，含 Schedule 注册/触发）
- CI 分片归属：`scripts/repo/plan_backend_shards.py` 的 `persistence` / `observability` / `workflows` shard

共享数据根的协调锁保存在 `coordination/locks/`，仅含 canonical key 的哈希；它不是按用户存储根，不保存正文或身份信息，不参与账号孤儿扫描。运行中禁止删除锁文件：替换 inode 会使两组写者绕过同一锁。备份恢复应在停止全部 API/worker 后执行。网络文件系统若不支持可靠 advisory locking，不可用于该文件模式；此约束不等于跨主机企业数据库 cutover。
