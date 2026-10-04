# scripts/migrations — 运行数据迁移工具

文件运行时（file runtime）→ 企业持久化（PostgreSQL）的迁移工具域。按
`runtime_to_enterprise/` 子目录组织；后续按域扩展 importer 时新增子命令
目录，不往本目录塞 unrelated 脚本。

## runtime_to_enterprise/

| 文件 | 职责 |
|------|------|
| `scan.py` | 只读枚举旧 runtime 可迁移实体（email 默认哈希展示） |
| `import.py` | 幂等导入（identity：accounts.json → users/tenants/memberships/credentials），记录 source hash；**绝不修改或删除文件侧** |
| `verify.py` | 双侧 count/hash/引用一致性校验，不一致 exit 1 |
| `cutover.py` | 显式 `--confirm enterprise-cutover` 记录切换；保留回退窗口，不做删除 |
| `report.py` | 状态文件 + 双侧计数汇总（cutover 前的审查产物） |
| `_common.py` | 共享：数据根解析、状态文件、确定性派生 id |

统一约定：派生 id（tenant/membership/credential）由 user_id 哈希得出，
重跑收敛不重复；状态落在 `<数据根>/migrations/runtime_to_enterprise/
state.json`（运行数据，不入库）。

## 五件事声明

- 是否联网：否（只访问本地文件根与 DATABASE_URL 指定的数据库）。
- 是否产生真实 LLM 费用：否。
- 输入是否必须 synthetic：否（部署期工具，输入是真实运行数据；因此
  输出/日志只含计数、id 与 hash，不打印邮箱明文/密码哈希）。
- 输出是否允许入库：否（state.json 属运行数据根，禁止提交）。
- 推荐调用位置：部署期人工（cutover 需 operator 显式确认）。
