# Next Tutor Agent 文档体系

本目录是仓库所有长期文档的唯一入口。每份文档有明确的**生命周期归属**；同一事实只允许存在一个权威来源。

## 目录结构与职责

| 目录 | 职责 | 生命周期 |
| --- | --- | --- |
| [`architecture/`](./architecture/README.md) | 系统当前如何工作（系统级入口 + 各模块 architecture 文档） | 长期，随代码持续更新（现在时） |
| [`adr/`](./adr/README.md) | 为什么做出关键架构决定（Architecture Decision Records） | 提交后结论不可改写；被取代时新建 superseding ADR |
| [`development/`](./development/testing.md) | 开发与测试方法（环境、分层、命令） | 长期，随流程更新 |
| [`operations/`](./operations/deployment.md) | 部署与运行（生产部署、Pages 演示、语义 RAG 运维） | 长期，随部署形态更新 |
| [`compliance/`](./compliance/content-policy.md) | 版权、第三方与分发边界（内容政策、语音许可证） | 长期，规则稳定 |
| [`reference/`](./reference/diagram-assets.md) | 生成型/清单型参考（由脚本生成，不可手改） | 随生成器重建 |
| [`validation/`](./validation/diagram-library.md) | 当前验收基线；独立的[版本回顾](./validation/diagram-library-history.md)记录历史验收与优化建议 | 当前基线随验收更新；回顾按版本归档，不代表当前能力 |
| [`i18n/`](./i18n/README.en.md) | 本地化文档（英文 README 等） | 随主文档同步 |
| [`assets/`](./assets/learning-journey.svg) | 文档用静态素材 | — |

## 文档生命周期规则

- **architecture = 现在时**：描述当前系统。允许持续编辑，但禁止加入 TODO checklist、实施阶段计划、已完成任务流水账或按日期追加的修复记录。
- **ADR = 过去/现在时的决定**：解释关键取舍的上下文、决定与后果。accepted 后不修改结论；决策改变时新建 superseding ADR。
- **plan = 将来时，永不入库**：当前任务的临时计划写在仓库根的 `plan.md`（已被 `.gitignore` 排除）。tracked 代码、测试与配置**禁止引用** `plan.md` 的章节号；需要引用稳定设计时只引用 `docs/architecture/...` 或 ADR 编号。完成一个 plan 时：永久约束写入 architecture、关键取舍写成 ADR、可验证要求写成测试、必要的人工验收证据更新到 validation，然后删除本地 plan。
- **tests = 可执行契约**：行为语义以测试为准，文档不复制测试细节。
- **validation = 当前证据**：当前验收文档只保留仍有效的基线，不堆叠历史轮次；历史回顾单独成文。
- **版本回顾 = 独立历史文档**：在已有 `validation/` 目录保存历史版本结果、失败调查与详细优化建议；不混入 architecture 或当前验收文档，也不替代本地任务计划。

## 从哪里开始

- 想了解整个系统：[`architecture/README.md`](./architecture/README.md)
- 想跑起来开发/测试：[`development/testing.md`](./development/testing.md)
- 想部署：[`operations/deployment.md`](./operations/deployment.md)
- 想确认能否提交某类内容：[`compliance/content-policy.md`](./compliance/content-policy.md)
- 想知道某个决定为什么如此：[`adr/README.md`](./adr/README.md)
