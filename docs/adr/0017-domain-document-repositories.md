# ADR-0017: 域文档仓储（JSONB）与逐域 SQL cutover

- 状态：accepted
- 日期：2026-10-06
- 关联：ADR-0010（企业持久化栈）、ADR-0014（单实例约束，本决策逐步解除其前提）、ADR-0003（BM25 基线保护）。

## Context

ADR-0010 只把身份/会话接入了 PostgreSQL；九个业务域的事实仍在文件层，因而 ADR-0014 强制单 API 实例。各域现状高度一致：`(owner 段 → 每资源 JSON / 每用户索引 JSON) + JSONL journal`，所有权要么在路径段、要么在 payload 内（chat session）；锁为 per-path `file_lock`。逐域自建 SQL schema 会产生九套列级映射与九套迁移风险；而各域 payload 本就是自描述 JSON 文档。

## Decision

**通用 JSONB 文档表**：`app/persistence/models/documents.py` 为九域（chat / library / textbooks / notes / assessment / evidence / classroom / orchestration / assistant+illustration）生成同构表 `<domain>_documents`，列为 `tenant_id + owner_id + kind + doc_id + payload(JSONB) + epoch + created_at/updated_at`，唯一键 `(tenant_id, owner_id, kind, doc_id)`。空串 `tenant_id` 是 pre-tenant 遗留作用域（WS5c 之前），不用 NULL——保证唯一约束在 PG 与 sqlite 都有意义。

**仓储协议**：`app/persistence/documents/` 提供 `DocumentRepository` 协议（put/get/delete/list/count/mutate/purge_owner）、`DocumentRecord` 纯数据与 `SqlDocumentRepository` 通用实现：

- `put(expected_epoch=…)` 是 compare-and-swap，epoch 失配抛 `DocumentConflictError`（illustration 域的乐观并发语义）；
- `mutate(fn)` 在单个事务 + 行锁内做 read-modify-write（journal 追加、索引更新的原子单元；PG 用 `SELECT … FOR UPDATE`，sqlite 单写者串行）；
- `purge_owner` 供账号删除：`core/account_data.purge_account` 在企业模式以独立短命引擎 + 自有事件循环清空九域行（purge 在 worker 线程执行，共享引擎连接绑定主循环，不可跨 loop 复用）；失败记 warning，purge 幂等可重试。

**逐域路由**：`DOMAIN_DOCUMENT_BACKENDS`（默认空 = file）+ `DATABASE_URL` 共同决定 `sql_enabled(domain)`；支持 `all` / `none` / `chat=file,notes=sql` / `default=sql`。九域全部 cutover 验证后默认值翻转为 `sql`，env 保留逐域回退文件实现的通道。

**边界**：对象字节（上传原件、渲染产物、音频）不进 JSONB——走 ObjectStore 协议（ADR-0018）；BM25/KG/embedding 等派生索引保持文件态（可重建缓存，ADR-0003 基线不动）；领域 payload 的 schema 与版本化归各域所有，本层只管身份、隔离键、时间戳与并发纪元。

**迁移路径（每域）**：repository protocol → SQL repository → importer（`scripts/migrations/runtime_to_enterprise/` 逐域扩展）→ verify → shadow read → cutover（路由翻 sql）→ 去生产文件写。文件实现保留为回退路径。

## Consequences

- 每域 cutover 完成即解除该域对单实例的依赖；九域全部完成后 `WEB_CONCURRENCY=1` 解除（多实例前提：PG 事实源 + ObjectStore + 派生索引共享卷，见部署文档）。
- JSONB 文档不做列级约束：域内字段校验在域服务层完成；跨文档事务通过 `mutate` 单行语义或后续显式复合操作实现，不做分布式事务。
- 迁移 0002 建九表；`alembic check` 保证模型↔迁移零漂移。sqlite 单测 lane 与 PG 集成 lane（CI enterprise job）双绿是每域 cutover 的门禁。
