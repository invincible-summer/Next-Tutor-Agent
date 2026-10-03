# diagrams — 教学图库（程序化原创 SVG）

1,119 项原创教学 SVG 素材的参数化 drawing 模块与目录登记：所有几何由服务端代码按真实结构确定性绘制（液面、刻度、接头按物理事实表达），模型与外部图形不参与绘制。

域设计详见 [docs/architecture/diagrams-illustration.md](../../../../docs/architecture/diagrams-illustration.md)，本 README 只做导航；逐项资产清单见 `docs/reference/diagram-assets.md`（生成物，勿手改）。

## Owns

- 参数化渲染器（drawing 模块）：`mathematics.py`、`physics.py`、`instruments.py`、`life_earth.py`、`systems.py`、`templates.py` 与 `extended_*.py` 系列（math / physics / chemistry / biology / earth / humanities / creative / statistics / systems / engineering / experiments / inventory / deferred），共 17 学科 34 素材族；`curriculum_expansion.py` 为最近一轮扩充构图的登记模块。
- v2 装配语义层：`semantics.py`（31 个组件 + 10 个装配配方的领域几何、动态端口/区域）、`adapters.py`（全部登记素材统一适配为实际参数几何）。
- 目录与登记：`catalog.py`（检索与 source_hash 校验）、`registry.py`、`schema.py`、`taxonomy.py`、`materials.py`（运行新增素材卡片）、`material_templates.py`、`guidance.py`、`pipeline.py`。
- 溯源与审核台账（review ledger）：`provenance.py` 维护 `services/api/assets/diagram_library/review.json` —— 逐素材 provenance / `source_hash`（绑定渲染器源码哈希）与事实核对引用；`catalog.py` 启动校验目录条目与台账 hash 一致，不一致即拒绝。
- 素材资产：`services/api/assets/diagram_library/`（`catalog.json` + `materials/<asset_id>/`，随仓库发布的源码资产，ADR-0001）。

## Does not own

- 题图 v2 装配管线（检索/构图/编译/审核/任务状态机）→ `app/illustration/`。
- 出题侧配图策略与兼容补图 → `app/core/quiz_illustration*.py`。
- 公开 API → `app/api/v1/diagram_library.py`、`diagram_materials.py`。
- 运行新增素材的存储根 `diagram_assets/`（`core/paths.py` 绑定，`core/orphan_cleanup.py` 扫描）。

## Design

`catalog.json` 与 `review.json` 均为生成物，由目录生成脚本（见 scripts/diagrams/）从渲染器源码重建，不要手改；素材包可重建性经 `scripts/diagrams/build_packages.py --check` 校验。新增定量/科学装配能力必须登记单位、条件参数、动态端口、区域与关系规则，不能只靠标签或相似外形取得资格。

## Tests

`services/api/tests/`：`test_diagram_library.py`（目录/参数/规范化）、`test_diagram_adapters.py`（全库适配器）、`test_diagram_expansion.py`（扩充登记）、`test_diagram_guidance.py`（阶段提示）、`test_diagram_materials.py`（公私创作与版本）。题图侧回归（`test_illustration_v2.py` 等）属 `app/illustration/` 域。

## Key entry points

- `catalog.py` — 素材目录检索、`digest` 与 source_hash 一致性校验
- `semantics.py` — v2 组件/配方语义、`asset_card`、`catalog_version`
- `adapters.py` — 登记素材 → 实际参数几何的统一适配
- `provenance.py` — review ledger 与事实引用
