# scripts — 仓库级脚本导航

仓库根 `scripts/` 是按子域组织的运维/开发/验收脚本入口；本 README 只做目录导航，不描述单个脚本的用法（见各脚本 docstring 与对应文档）。

文档体系总入口：[docs/README.md](../docs/README.md)。

## 子目录

| 目录 | 职责 |
|------|------|
| `demo/` | GitHub Pages 静态演示：`export_pages_demo.py`（synthetic fixtures 只读导出）、`serve_pages_demo.py`（本地伺服预览）及导出器自测 |
| `dev/` | 本地开发辅助：`start.sh`（完整运行时一键启动、端口回退、sidecar 拉起）、`generate_assistant_types.py` / `generate_classroom_types.py`（契约类型生成）、`create_speech.js`、`migrate_learning_evidence.py` |
| `illustration/` | 图库/题图真实模型验收：`acceptance.py`、`material_acceptance.py`（产生真实 LLM 费用，输出必须写仓库外目录） |
| `repo/` | 仓库卫生守卫：`check_repository_hygiene.py`（CI 首个 blocking job，检查 tracked 文件与全量历史）、`check_pages_artifact.py`、`repo-policy.toml`（大文件例外政策） |

## 命名与归域规则

- 脚本按域归入子目录，不散落在仓库根；新域新建子目录，而不是往现有目录塞 unrelated 脚本。
- 每个子域子目录配套 README，说明五件事：是否联网；是否产生真实 LLM 费用；输入是否必须 synthetic；输出是否允许入库；推荐调用位置（本地手工 / CI / 部署期）。
- 涉及运行数据的脚本必须走 `services/api/app/core/paths.py` 的数据根解析，不得硬编码存储路径。

## Does not own

- 后端专属脚本（真实模型验收、图库目录生成等）→ `services/api/scripts/`。
- 前端构建与浏览器回归脚本 → `apps/web/scripts/`（如 `build-classroom-assets.mjs`、`test-classroom-*.mjs`、`check-diagram-library.mjs`）。
- 部署模板与安装脚本 → [`deploy/`](../deploy/README.md)（部署文档以 [docs/operations/deployment.md](../docs/operations/deployment.md) 为权威）。
