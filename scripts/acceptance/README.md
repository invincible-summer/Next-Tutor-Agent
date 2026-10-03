# scripts/acceptance — 真实模型验收（产生费用）

真实 provider 下的端到端验收：不 mock LLM，用配置的真实模型跑完整管线，产出可人工审读的工件。**全部脚本都会产生真实 LLM 费用，输出一律写仓库外目录。**

| 脚本 | 验收对象 | 输出示例 |
|------|----------|----------|
| `classroom/live.py` | 课堂课件生成全链路（brief → run → revision → 离线 HTML） | `/tmp/course-live/` |
| `illustration/live.py` | 题图/图库真实模型逐轮输出（模型 JSON、SVG、实际 PNG、report） | `/tmp/illustration-review/` |
| `illustration/materials.py` | 素材服务全流程（容器/几何/流程草稿、手动修改、冻结版本、用指定素材出题，两项审图看真实 PNG） | `/tmp/material-live-acceptance/` |

## 契约

- **联网**：是（调用配置的真实 LLM provider）。
- **真实 LLM 费用**：是；这是它们与 unittest/e2e（全部 mock）的本质区别。
- **输入**：synthetic 题目/brief/ fixtures（项目编写，不含任何真实用户数据）。
- **输出**：**不允许入库**——`--output` 必须指向仓库外目录；验收结论（而非工件）记录在 [docs/validation/diagram-library.md](../../docs/validation/diagram-library.md)。
- **推荐调用位置**：本地/部署环境人工执行；CI 永不运行（遵循「不为目录重排/例行验证产生费用」原则）。
- **对应文档**：[docs/development/testing.md](../../docs/development/testing.md)（验收章节）、[docs/architecture/diagrams-illustration.md](../../docs/architecture/diagrams-illustration.md)、课堂架构 [docs/architecture/classroom.md](../../docs/architecture/classroom.md)。
