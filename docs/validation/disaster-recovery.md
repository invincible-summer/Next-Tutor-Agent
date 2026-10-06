# 灾备与恢复基线（2026-10-06，v3.1.0）

企业持久化形态（ADR-0017：九域事实落 PostgreSQL；ADR-0016：Valkey 缓存层；
ADR-0013：Temporal durable worker；ADR-0018：S3 兼容 ObjectStore）的灾备证据与
生产恢复 runbook。证据按三方如实拆分：**已完成**（机制与工程落地）、
**CI 已验证**（release workflow 每次发布实际执行）、**待持有者执行**
（需要生产环境/真实凭据，本仓库无法代跑）。

## 已完成（机制）

| 能力 | 机制 | 依据 |
|------|------|------|
| PostgreSQL 单一事实源 | 九域 cutover 完成，默认路由 sql；文件实现保留可回滚 | ADR-0017、`app/persistence/documents/` |
| 缓存层可弃 | 业务事实不落缓存；断连自动回退进程内原语 | ADR-0016、`app/persistence/cache/resp.py` |
| 后台任务 durable 化 | 五队列移交 Temporal worker；API 重启不丢任务 | ADR-0013、`services/api/worker.py` |
| 账号删除全量清除 | `core/account_data.purge_account` + 孤儿数据扫描清理端点 | `GET/POST /admin/orphan-data`、`core/orphan_cleanup.py` |
| 对象字节完整性 | S3 适配器写入 sha256 元数据，读取校验不匹配即拒绝 | ADR-0018、`app/persistence/object_store/s3.py` |
| 演练工具 | seed/flush/verify 三段式灾备演练脚本 | `scripts/ops/dr_drill.py` |

## CI 已验证（release workflow 企业 lane 实跑）

release workflow（`.github/workflows/release.yml`，tag/手动触发）的
`backend-enterprise` lane 在真实 PostgreSQL 18 + Valkey 9.1.2 服务容器上执行：

1. **PG dump → drop → restore → verify 零漂移**：`dr_drill.py seed` 写入身份
   四表 + 审计事件 + chat/notes 域文档并冻结内容寻址快照（逐表行数 + 规范化
   序列化 sha256）；`pg_dump --format=custom` 后 `DROP DATABASE WITH (FORCE)`、
   重建、`pg_restore`；`verify` 要求恢复后逐表字节一致，身份束可读，任何漂移
   exit 1。
2. **Valkey FLUSHALL 后事实完整**：`flush-cache` 子命令清空共享缓存并立即从
   PostgreSQL 重读全部种子事实——缓存是可弃态的直接证据。
3. **ObjectStore 集成**：moto S3 上的往返/篡改拒绝/多段上传/工厂配置
   （`tests.persistence.test_object_store_s3`；真实云凭据连接见待持有者项）。
4. **导入幂等重放**：runtime → enterprise 导入器（身份 + 九域文档）幂等与
   verify 漂移拒绝（`tests.persistence.test_runtime_import.py`）。
5. **Temporal durable 派发**：真实 Temporal server 上 API 角色派发 → worker
   域队列 → 终态结算（`tests.workflows.integration`）。
6. **账号清除无孤儿**：purge 与孤儿清理断言在常规 backend shards 持续运行。

## 生产恢复 runbook（持有者执行）

以下命令与 CI 演练同形；在 self-host 机器上以实际连接参数替换。

### PostgreSQL 备份与恢复

```bash
# 备份（建议 systemd timer 每日；custom format 支持并行与选择性恢复）
sudo -u postgres pg_dump -Fc -d tutor -f /var/backups/tutor-$(date +%F).pgdump

# 恢复（整库重建）
sudo -u postgres psql -d postgres -c "DROP DATABASE tutor WITH (FORCE)" \
                         -c "CREATE DATABASE tutor OWNER tutor"
pg_restore -h 127.0.0.1 -U tutor -d tutor --no-owner \
  < /var/backups/tutor-YYYY-MM-DD.pgdump

# 验证（零漂移门禁）：恢复后立即跑一次 seed/verify 演练，
# 或对既有快照执行 scripts/ops/dr_drill.py verify --snapshot <快照>
```

### Valkey（缓存层）

缓存不承载事实。实例宕机/数据丢失 = 重启或重建容器即可，无需恢复数据；
极端情况下 `FLUSHALL` 也只是性能回退。语义证据见上方 CI 项 2。

### Temporal worker

worker 崩溃/被杀后重启（`systemctl restart edu-agent-worker`）即从事件历史
恢复执行中的 workflow；维护期可先停 API 再停 worker，恢复顺序反之。
**kill -9 中断演练**需要在持有者的生产/预发环境实际执行一次并记录（见下）。

### ObjectStore（S3 兼容）

字节侧完整性靠对象元数据 sha256（ADR-0018）；持有者应开启桶版本控制或
服务端复制作为备份层。预览/上传对象不可恢复时，域文档事实仍在 PostgreSQL，
对象可通过上游教材重新生成（illustration/preview 类派生产物）。

## 待持有者执行（发布声明的 open gate）

| 项 | 原因 | 建议证据 |
|----|------|----------|
| 生产 PG 备份/恢复定期演练 | 需要真实生产数据与备份基础设施 | 每季度一次，记录备份尺寸/耗时/RTO 到本文件附注 |
| Temporal worker kill -9 中断演练 | CI 集成车道 worker 与测试同进程，无法真实杀进程 | 预发环境 `kill -9` worker 并确认 workflow 自动续跑至终态 |
| 云凭据 ObjectStore 实连 | 本仓库无云凭据；CI 用 moto | 用真实 S3/R2/MinIO 生产端点跑一次上传下载往返并记录 |
| 自建镜像容器扫描 | 本项目以源码 release 分发（SBOM+NOTICES+SHA256SUMS），**无容器发行物**，release 阶段的 container scan 标注为 N/A；持有者若自行构建镜像，建议 `trivy image` 后入库 | 扫描报告归档于部署方运维手册 |

以上各项在 GitHub Release v3.1.0 的 notes 中如实列为 open gate，不宣称完成。
