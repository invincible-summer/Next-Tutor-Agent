# scripts/ops — 企业部署运维工具

面向企业持久化形态（ADR-0017，`DATABASE_URL` 已配置）的运维/演练脚本。
全部可离线、可重复执行；输出一律写仓库外目录。

| 脚本 | 用途 | 输入 | 输出 |
|------|------|------|------|
| `dr_drill.py` | 灾备演练：seed 事实集 + 内容寻址快照、`flush-cache` 证明缓存可弃、`verify` 比对恢复后零漂移 | `TEST_DATABASE_URL`（必需）、`TEST_CACHE_URL`（可选） | `--out` 目录下 `snapshot.json` |

## 灾备演练组合

`dr_drill.py` 只负责事实与校验；dump / drop / restore 用操作员命令（与
runbook 同形）串在两侧，release workflow 的 enterprise lane 按此组合执行：

```bash
python3 scripts/ops/dr_drill.py seed --out /tmp/dr
docker run --rm --network host -e PGPASSWORD=... postgres:18 \
  pg_dump -h 127.0.0.1 -U tutor -d tutor_test --format=custom > /tmp/dr/db.pgdump
# DROP DATABASE / CREATE DATABASE（psql，见 runbook）
docker run --rm --network host -i -e PGPASSWORD=... postgres:18 \
  pg_restore -h 127.0.0.1 -U tutor -d tutor_test --no-owner < /tmp/dr/db.pgdump
python3 scripts/ops/dr_drill.py verify --snapshot /tmp/dr/snapshot.json
```

对应文档：[灾备 runbook](../../docs/validation/disaster-recovery.md)（已完成 /
CI 已验证 / 待持有者执行 的三方拆分）、[企业基础设施](../../docs/development/enterprise-infra.md)。
