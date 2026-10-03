# Diagrams & Illustration（教学图库与题图装配）

本域维护 1,119 项原创教学 SVG 素材库并运行题图 v2 装配管线：模型只声明需求、构图与审核，检索、几何、编译、PNG 渲染与最终 SVG 全部由服务端确定性完成。

## Purpose / Scope

- **共享图库**：随代码发布的公有素材目录（`services/api/assets/diagram_library/`，1,119 项素材 / 17 学科 / 34 素材族），以及运行期新增的公有（管理员维护）与个人创作素材。资产清单与逐项能力见 [../reference/diagram-assets.md](../reference/diagram-assets.md)。
- **题图 v2 装配**（`app/illustration/`）：`QuestionMaterialContract` → `VisualBriefV2` 声明 → 本地检索 `CandidateBundleV2` → `SceneDraftV2` 受限构图 → 确定性装配与静态检查 → Chromium 实测与 PNG → 独立视觉审核 + 联合题图审核 → 冻结交付。
- **CAT 文字先行协议**：CAT 先交付自足文字题，后为同一题目身份生成 `supplemental` 补充图；出题/批改的受理与判分协议属 [assessment.md](assessment.md)。
- **验收基线**：14 类题图情境与两组数值的真实模型/PNG 验收记录见 [../validation/diagram-library.md](../validation/diagram-library.md)；运行命令见 [../development/testing.md](../development/testing.md)。

## Owned code

- `services/api/app/diagrams/`（36 文件）
  - 渲染器：`mathematics.py`、`physics.py`、`instruments.py`、`life_earth.py`、`systems.py`、`templates.py` 与 `extended_*.py` 系列（math/physics/chemistry/biology/earth/humanities/creative/statistics/systems/engineering/experiments/inventory/deferred）。
  - `curriculum_expansion.py` — 最近一轮 40 个新构图的登记模块（物理 10、化学 8、地理 8、生物 4、天文 2、数学 2、统计 2、工程 2、环境 1、农业 1），并入对应学科 family。
  - v2 语义层：`semantics.py`（31 个组件 + 10 个装配配方的领域几何、动态端口/区域）、`adapters.py`（全部登记素材统一适配为实际参数几何）、`catalog.py` / `registry.py` / `schema.py` / `taxonomy.py` / `drawing.py` / `materials.py` / `material_templates.py` / `guidance.py` / `provenance.py` / `pipeline.py`。
- `services/api/app/illustration/`（12 文件）：`contracts.py`（闭合 schema 全集）、`requirements.py`、`retrieval.py`、`composition.py`、`layout.py`（`compile_scene` 实测编译）、`preview.py`（Chromium PNG/测量）、`review.py`、`validators.py`、`orchestrator.py`（job/run 状态机）、`persistence.py`、`events.py`（公开投影）。
- `services/api/app/core/`：`quiz_illustration.py`（SVG 规范化与白名单重建）、`quiz_illustration_policy.py`（`resolve_illustration_policy`：总闸/账户偏好/本次意图三态合成）、`quiz_illustration_enrichment.py`（兼容链路补图）。
- API 路由：`services/api/app/api/v1/diagram_library.py`（共享目录）、`diagram_materials.py`（公私创作）、`illustration_jobs.py`（题图任务）、`assessment_illustration.py`（CAT 补图启动）。
- 构建脚本：`services/api/scripts/build_diagram_catalog.py`（稳定 ID/名称/别名/能力声明）、`build_diagram_packages.py --check`（素材包可重建与一致性校验）。

## Public contracts

**闭合协议（`illustration/contracts.py`，模型无素材搜索工具、不读库文件）**

| 合同 | 职责 |
|------|------|
| `QuestionMaterialContract` | 服务端绑定题目身份、公开题面、实体/事实/关系与私有答案/量规 |
| `VisualBriefV2` | 一次结构化调用声明用途、视图、素材需求、事实引用（≤12 类需求、≤24 实例） |
| `CandidateBundleV2` | 本地检索返回的授权 ID/版本、能力、参数语义、端口与区域（每类 ≤6 候选） |
| `SceneDraftV2` | 模型提交的实例、实体映射、事实绑定、关系、标签与分层 |
| `ScenePatchV2` | 绑定 `base_scene_hash` 的受限修复（移动/缩放/合法旋转/换候选/重排标签路由），冻结事实不变 |
| `DiagramSourceV2` | 私有场景、实例事实、实测布局与审核证据 |

- `visual_role ∈ none|supplemental|essential`：`essential`（必要条件在图中，如仪器读数）必须在题目注册、作答前完成完整材料并通过审核；CAT 冻结文字题不能被后到补图转成 `essential`，也不能改题干/选项/答案/解析/量规/revision。
- 事实以 `fact_id` 绑定、服务端解析驱动几何；`depict_only`/`symbol_only` 数值与 `hidden` 事实不进构图；答案与量规只供联合审核；图库 `sample_params` 不是题目事实。
- 画布使用固定 profile（`question_landscape` / `question_square` / `coordinate_plane` / `comparison_split` / `tabletop`），尺寸不由模型任意扩张；响应 schema 为闭合字段，模型 SVG、外链与任意片段不进入构图。

**API（前缀 `/api/v1`，身份一律 `resolve_student_id()`）**

- 共享目录：`GET /diagram-assets?q=&subject=&family=&education_level=&asset_kind=&page=0&per=12`（分页从 0，单页 ≤48）、`GET /diagram-assets/taxonomy`、`GET /diagram-assets/{asset_id}`（已登记组件附带 v2 元数据）、`POST /diagram-assets/{asset_id}/preview`（`params` + `profile=textbook|monochrome`；未知 404、非法参数 422）。目录仅列审核 `passed` 素材；预览不调模型、不写库。
- 素材创作：`GET /diagram-materials?scope=private|public`（分页/搜索）、`GET /diagram-materials/templates`、`POST /diagram-materials/preview`（安全规范化 + 真实渲染）、`POST /diagram-materials/generate`（LLM 可编辑草稿，不自动发布）、`POST /diagram-materials`、`GET /diagram-materials/{asset_id}`（含 revisions 历史）、`PUT /diagram-materials/{asset_id}`（需最新 `base_revision`，冲突 409）、`DELETE /diagram-materials/{asset_id}`、`GET /diagram-materials/{asset_id}/preview.png`。scope=public 写操作仅管理员；私有素材他人不可见。
- 题图任务：`POST /quiz/illustration-jobs`（只接受已有题目 ID/revision）、`GET /illustration-jobs/{job_id}`（本人非 shadow 任务）、`POST /illustration-jobs/{job_id}/retry`、`GET /questions/{question_id}/illustration?question_revision=`（只读冻结题图，不启动模型）、`POST /assessment/questions/{question_id}/illustration`（按当前模式读历史或启动 CAT 补图）。状态机 `queued → running → ready|not_required|failed`；进度只投影准备/检索/构图/审核阶段与百分比；`required` 与开关冲突 409 `illustration_disabled`；他人/已删除 404；ready 重试 409 `illustration_frozen`。

**SVG 规范（`QuestionIllustration`）**

- 兼容 schema 1/sanitizer 1、2 可读；组件图 schema 2/sanitizer 3；v2 schema 3/sanitizer 3。
- 组件图上限 128 KiB、1200 节点、4000 路径段、深度 10（旧图沿用 24 KiB）；defusedxml 白名单重建，禁止脚本、HTML、事件、外链、动画与 DTD/实体；前端以 SVG data URI 的 `img` 展示（不内联 DOM），支持放大与黑白印刷切换。

## State & storage

- 内置素材（随仓库发布）：`services/api/assets/diagram_library/catalog.json` + `materials/<asset_id>/{asset.svg, material.json, usage_guide.json}`（1,130 个包 = 1,119 素材 + 10 配方 + 1 创作基底）；旧图版本不匹配则明确拒绝，目录与渲染器一同发布。
- 运行新增素材：数据根 `diagram_assets/<public|owner>/materials/<id>/versions/<revision>/`（SVG、元信息、短提示与 PNG 分离；每次保存形成不可变版本，索引在完整版本写入后原子发布）。
- v2 运行状态：数据根 `illustrations/<owner>/{jobs,runs,artifacts,previews}`——jobs 快照、runs 追加阶段事件、artifacts 不可变冻结、PNG 预览；JSON 走文件锁与 `core/atomic.py` 原子写。
- 兼容 v1 缓存：`students/<owner>.question_illustrations.json`。
- 两个运行根均由 `core/paths.py` 绑定并登记测试沙箱、账户删除（owner epoch 失效 + 后台任务取消）与孤儿清理（`core/orphan_cleanup.py` 类别 `illustrations`/`diagram_assets`）；重启后无存活任务的遗留 `queued/running` 记录标 `failed/run_interrupted`，显式重试开启新运行。

## Main flows

1. **图库渲染**：渲染器按真实结构画线（液面、刻度、接头、火焰按物理事实表达）；`adapters.py` 把全部登记素材适配为实际参数几何；`semantics.py` 的 31 组件/10 配方提供动态端口、区域与配方展开（省略显式 relations 不跳过配方内部支撑检查）。渲染层次与实现位置的对应：

   | 层次 | 内容 | 实现位置 |
   |------|------|---------|
   | 基础图形与数学 | 几何体、分数、坐标、函数、向量、统计 | `mathematics.py`、`extended_math.py` |
   | 仪器与物体 | 容器、实验配件、测量、力学对象 | `instruments.py`、`physics.py`、`extended_physics.py` |
   | 生物与地理 | 细胞、器官、地形与地球 | `life_earth.py`、`extended_biology.py`、`extended_earth.py` |
   | 化学与通用 | 分子键型、逻辑门、生活对象 | `systems.py`、`extended_systems.py` |
   | 兼容完整构图 | 实验、力学、电路、几何和统计模板 | `templates.py`、`extended_experiments.py`、`extended_deferred.py` |
   | v2 装配语义 | 实例参数、动态端口/区域、部件分层与配方 | `semantics.py` + `app/illustration/` |
   | 扩充构图 | 40 个新构图（文丘里管、凌星光变曲线、惠斯通电桥等） | `curriculum_expansion.py` |

2. **v2 装配**：服务端材料合同 → 模型声明 `VisualBriefV2`（无搜索工具）→ 本地按名称/别名/语义能力检索候选（能力与视图硬约束，学段筛选；候选卡包含参数的条件含义、单位、定性许可、端口、敏感区域与配方关系，外观拼图由本地 Chromium 生成且标注为样例）→ 模型提交 `SceneDraftV2`（可在实体/事实不变时请求一次重新检索）→ `compile_scene` 按解析参数生成端口/液面/刻度/边界，用 Chromium 实测边界与字体检查支撑、浸没、遮挡、出界、路由与可读性 → `preview.py` 渲染真实 PNG → 独立视觉审核 + 联合题图审核（题面/答案/量规 vs 图中条件；刻度题用同一成图局部放大）→ `machine/visual/joint=passed` 才冻结交付。可修复问题走 `ScenePatchV2` 后重走编译/PNG/审核；无修复成功流程消耗声明、构图、视觉与联合审核四次调用。
3. **普通出题 vs CAT**：普通出题在题目注册前完成题图（`essential` 缺图或未过审不能成为可作答任务；私有 artifact 先准备，再在锁内检查材料不可变性并整组注册，journal 题目引用是交付点）；CAT 文字题先冻结独立可答，补图只作 `supplemental`，且不重新生成文字题。
4. **任务生命周期**：相同题目身份重复启动复用已有任务，并发只创建一个运行；ready 产物不可覆盖，改材料须新 revision；只有进行中 CAT 的当前/最后题可发起补图或重试。
5. **素材创作**：上传/模板/LLM 草稿进 Modal 编辑器 → 预览 → 显式保存为不可变版本（乐观锁）→ 启用后进入该账户出题候选；当前上传 SVG 只具备静态整体能力，无定量参数或物理端口，实际题图仍须两项审核。

## Dependencies

- **Node/Playwright Chromium**（本地）：布局实测、候选拼图与 PNG 渲染；缺浏览器即明确失败，不把结构可解析当视觉审核通过。
- **LLM 图片输入**：`LLM_SUPPORTS_IMAGES=1` 声明真实能力；未声明或模型无视觉时 v2 返回失败。
- **出题路径**：`generate_quiz`/`fit_quiz` 与 M4 CAT/约束出题（[assessment.md](assessment.md)）经 `quiz_illustration_policy` 决定是否生成；题目注册与作答上下文由 M2 评价域承载。
- **prompt 注册表**：需求声明 `quiz_illustration_requirements@2.2.0`，构图/视觉审核/联合审核 `@2.1.0`（`prompts/quiz_illustration.py`）；素材创作草稿 `diagram_material`。

## Invariants / security boundaries

- 模型没有素材搜索工具，不读取库文件；候选 ID/版本/能力/参数全部由服务端决定；模型自报 hash 或「已通过」字段不能跳过任何审核阶段。
- 待求读数和答案不进入构图模型的事实值投影，也不写入 alt/caption/标签。
- 发布门：`QUIZ_ILLUSTRATION_VISUAL_REVIEW=active` 且 `machine/visual/joint=passed` 才可交付 v2 图；off/shadow 或旧账户审图偏好不能放行未经审核的图。
- 历史冻结图不因目录、提示词或开关变化重绘；改冻结题图必须新 revision。
- 学生接口只返回任务身份、视觉角色、进度、规范化图片与闭合失败码/重试标志；原始事实、gold、场景、候选轨迹、内部失败阶段与审核文字留在账户私有记录；shadow 任务不可经公开任务接口读取。
- 公有库写操作仅管理员（`require_admin`）；个人素材仅属主可见；预览不调用模型、不修改库或学生记录。
- 函数图使用受限 AST 解释器，不执行用户代码；单位/数据类型/区间必须与参数合同匹配。
- 内置素材是项目原创矢量（源码资产）；新增定量或科学装配能力必须登记单位、条件参数、动态端口、区域与关系规则，不能只靠标签或相似外形取得资格。

## Configuration

- `QUIZ_SVG_ENABLED`（默认 `1`）：题图运维总闸（紧急止血）；关闭后 `required` 意图返回 `illustration_disabled`，已冻结历史题图仍可读。
- `QUIZ_ILLUSTRATION_PIPELINE`（`v1|shadow|v2`，默认 `shadow`）：`v1` 兼容组件链路；`shadow` 兼容链路对外交付、CAT 另起账户私有 v2 对照任务（不公开）；`v2` 普通出题走 v2 发布门、CAT 走任务返回协议。
- `QUIZ_ILLUSTRATION_VISUAL_REVIEW`（`off|shadow|active`，默认 `active`）：真实 PNG 视觉审核与联合审核发布门。
- `QUIZ_ILLUSTRATION_MAX_CALLS=5`、`QUIZ_ILLUSTRATION_DEADLINE_SECONDS=45`（冻结 CAT 补图 30 秒）、`QUIZ_ILLUSTRATION_MAX_REPAIRS=2`：v2 预算（重新检索与修复同池；修复上限不保证预算足够）。普通出题另受外层 `ASSESSMENT_GENERATION_*` 约束（见 [assessment.md](assessment.md)）。
- `LLM_SUPPORTS_IMAGES`（默认 `0`）：部署声明模型图片输入能力。
- `QUIZ_DIAGRAM_MODE`：仅保留兼容配置含义，不能开启模型直接输出 SVG。
- 账户 `profile.prefs.quiz_svg_enabled`（缺省 true）与本次 `illustration_request=auto|none|required` 意图共同控制新生成；`quiz_illustration_review_enabled` 只控制兼容 v1 语义审查，不能取消 v2 发布门。
- 模型工具参数不能自行开启生成或伪造强制要求：Chat provider 绑定可信账户与当前用户意图；CAT 的本次意图保存于实例并在 next/恢复时复用。

## Observability

- 任务/run 追加事件流记录各阶段；`catalog_version()` 与 `V2_RENDERER_VERSION` 随产物落盘，可追溯装配环境。
- 构建期校验：`python3 services/api/scripts/build_diagram_packages.py --check`（可重建与一致性）；`apps/web/scripts/check-diagram-library.mjs` 渲染全部目录素材输出人工审查拼图与边界报告（自动检查不替代截图审阅）。
- 真实模型验收：`python3 scripts/illustration/acceptance.py --live-llm --output <repo 外目录>` 逐轮输出模型 JSON、SVG、实际 PNG 与 report；验收记录见 [../validation/diagram-library.md](../validation/diagram-library.md)。
- 资产级清单（名称/别名/能力/参数）见 [../reference/diagram-assets.md](../reference/diagram-assets.md)；测评侧合同见 [assessment.md](assessment.md)；图库与题图协议已并入本文档。

## Tests / acceptance

`services/api/tests/`（聚焦运行：`cd services/api && python -m tests tests.test_illustration_v2`）：

- `test_diagram_library.py` — 共享目录、参数、规范化与兼容构图。
- `test_diagram_adapters.py` / `test_diagram_expansion.py` / `test_diagram_guidance.py` — 全库适配器、扩充登记与阶段提示。
- `test_diagram_materials.py` — 公私创作、版本、乐观锁与权限。
- `test_illustration_v2.py` — 合成材料 + fake LLM 经真实 Chromium 检查科学关系、事实绑定、补丁与审核门（不使用真实模型凭证）。
- `test_illustration_jobs.py` — 经真实 JWT/ASGI 路由检查所有权、公开投影、单次运行、重试、不可变产物与删除竞争。
- `test_quiz_illustration.py` / `test_quiz_illustration_enrichment.py` — 出题侧材料策略与兼容补图链路。
- 验收基线与真实模型记录见 [../validation/diagram-library.md](../validation/diagram-library.md)；fake LLM 通过不等于真实验收。

## Related ADRs

- ADR-0001 source-only（内置图库是原创源码资产随仓库发布；运行产生的题图/个人素材是部署本地运行数据）。
- ADR-0002 运行数据单根（`illustrations/`、`diagram_assets/` 均为统一数据根下的绑定存储根）。
