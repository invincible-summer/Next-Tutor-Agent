# 贡献指南

感谢关注 Next-Tutor-Agent。本仓库目前以「源码可见」方式发布（见根 README 的授权声明）；提交 PR 前请先阅读 [`docs/README.md`](../docs/README.md) 的文档导航与 [`AGENTS.md`](../AGENTS.md) 的仓库规则。

## 环境

- Linux / WSL，Python 3.11，Node.js 22 LTS，pnpm 11，OpenAI 兼容模型服务。
- 后端依赖：`python -m pip install -r services/api/requirements.txt -r services/api/requirements-test.txt`
- 前端依赖：`cd apps/web && pnpm install`
- 完整环境说明见 [`docs/development/testing.md`](../docs/development/testing.md)。

## 日常检查（focused）

改动后先跑聚焦回归，再考虑全量：

```bash
cd services/api && python -m tests tests.test_<module>   # 单模块后端回归
cd apps/web && pnpm check                                # 前端 typecheck + ESLint + 单元检查
```

小的修复和更新不需要运行全量测试（遵循 AGENTS.md 顶部的仓库约定）；涉及行为、存储、API 或 agent 管线改动时必须跑全量。

## 全量检查（full check）

行为/存储/API 变更、以及任何要合入 main 的 PR：

```bash
python scripts/repo/check_repository_hygiene.py   # 版权/运行数据/历史边界
python scripts/repo/check_documentation.py        # 文档链接与结构
cd services/api && python -m tests               # 后端全量
cd apps/web && pnpm check && pnpm build          # 前端全量 + 生产构建
```

浏览器回归、课堂/题图专项与向量回归的触发条件见 [`docs/development/testing.md`](../docs/development/testing.md)。

## 架构文档与 ADR 的更新条件

- 修改架构、存储布局、API 契约或 agent 管线时，同一变更内更新对应 `docs/architecture/<module>.md`（它们描述当前事实）。
- 做出「后来者不应悄悄推翻」的决定（存储模型、检索基线、部署形态、内容政策）时，新增一份 ADR 到 `docs/adr/`（编号递增，Status/Context/Decision/Consequences 四节）；ADR 一经接受不再回写修改，用新 ADR 取代。
- 移动代码时同步修正受影响的模块 README 链接；README 只做导航，不要扩写成设计文档。

## plan 的生命周期

工作计划（`plan*.md`）属于本地工作文件，永远不提交、不进入仓库文档体系；tracked 的代码/测试/配置不得引用 plan 章节。阶段性验收记录写入 `docs/validation/`，只描述当前证据。

## 版权与 fixture 规则

- 用于 RAG/E2E 的文本与数据 fixture 必须是项目自写的 synthetic 数据；禁止提交教材 PDF/EPUB、解析文本、切片、教材知识图谱、embedding/向量索引、用户笔记/会话/画像/trace、真实账号导出或未经许可的第三方素材（细则见 [`docs/compliance/content-policy.md`](../docs/compliance/content-policy.md)）。
- 程序化生成的素材/目录类资产必须可由仓库内生成器确定性重建，并保持 provenance 台账一致。

## 提交约定

- imperative + scoped，例如 `m5: add textbook taxonomy`、`chat: fix formula layout`；避免模糊的 checkpoint 信息。
- PR 说明需包含：行为变化、兼容性与权限边界、已运行的测试、关联 issue、文档更新。
- UI 变更附截图或录屏（浅色/深色）。
