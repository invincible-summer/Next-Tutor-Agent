# Diagrams & Illustration（教学图库与共享配图引擎）

本域维护 1,119 项原创教学 SVG 素材库，提供 V1 兼容绘图、V2 受限素材装配及 V3 素材参考创作。V3 由模型自由加工完整 SVG、组合素材并绘制缺失元素；服务端检索素材、安全重建 SVG、实际渲染、审核及冻结发布。配图作为共享能力供测评和独立 [工具助手情景配图](./tool-assistant.md) 消费，业务上下文和审核分别适配。长期决定见 [ADR-0006](../adr/0006-material-assisted-svg-authoring.md)、[ADR-0007](../adr/0007-shared-illustration-tools.md)。

## Purpose / Scope

- **共享图库**：随代码发布的公有素材目录（`services/api/assets/diagram_library/`，1,119 项素材 / 17 学科 / 34 素材族），以及运行期新增的公有（管理员维护）与个人创作素材。资产清单与逐项能力见 [../reference/diagram-assets.md](../reference/diagram-assets.md)。
- **通用情景配图**：复用 V1–V3 绘图、素材读取、编译和安全渲染，使用显式情景需求与审核合同；支持用户选材和基础图片修改，不创建 TaskSnapshot，不依赖答案/量规。会话和多轮 API 由 [tool-assistant.md](./tool-assistant.md) 拥有。
- **题图 v2 装配**（`app/illustration/`）：`QuestionMaterialContract` → `VisualBriefV2` 声明 → 本地检索 `CandidateBundleV2` → `SceneDraftV2` 受限构图 → 确定性装配与静态检查 → Chromium 实测与 PNG → 一次合并 PNG/题目/答案量规审核 → 冻结交付。
- **题图 V3 创作**（`app/illustration/v3*.py`）：轻量绘图需求 → 模型提出素材名称/同义词 → 本地模糊检索完整 SVG → 模型取舍、改绘、拼接或自绘 → SVG 规范化与实际 PNG → 一次合并审核 → 冻结交付。普通出题、聊天 `generate_quiz`/`fit_quiz` 和 CAT 均可使用。
- **CAT 文字先行协议**：CAT 先交付自足文字题，后为同一题目身份按实例选择 V1、V2 或 V3 生成 `supplemental` 补充图；V1 是旧版模型主导的组件绘图链，V2 是受限素材库装配链，V3 是素材参考创作链。用户提示词会进入各链的构图上下文；出题/批改的受理与判分协议属 [assessment.md](assessment.md)。
- **验收基线**：装置、读数、几何和跨学科构图的真实模型/PNG 验收记录见 [../validation/diagram-library.md](../validation/diagram-library.md)；运行命令见 [../development/testing.md](../development/testing.md)。

## Owned code

- `services/api/app/diagrams/`
  - 渲染器：`mathematics.py`、`physics.py`、`instruments.py`、`life_earth.py`、`systems.py`、`templates.py` 与 `extended_*.py` 系列（math/physics/chemistry/biology/earth/humanities/creative/statistics/systems/engineering/experiments/inventory/deferred）。
  - `curriculum_expansion.py` — 最近一轮 40 个新构图的登记模块（物理 10、化学 8、地理 8、生物 4、天文 2、数学 2、统计 2、工程 2、环境 1、农业 1），并入对应学科 family。
  - v2 语义层：`semantics.py`（31 个组件 + 10 个装配配方的领域几何、动态端口/区域）、`adapters.py`（全部登记素材统一适配为实际参数几何）、`catalog.py` / `registry.py` / `schema.py` / `taxonomy.py` / `drawing.py` / `materials.py` / `material_templates.py` / `guidance.py` / `provenance.py` / `pipeline.py`。
- `services/api/app/illustration/`：`contracts.py`（闭合 schema 全集）、`requirements.py`、`retrieval.py`、`composition.py`、`layout.py`（`compile_scene` 实测编译）、`preview.py`（Chromium PNG/测量）、`review.py`、`publishing.py`（兼容发布门）、`authoring.py`（普通出题阶段分离）、`v3.py` / `v3_contracts.py` / `v3_retrieval.py`（自由创作）、`validators.py`、`orchestrator.py`（job/run 状态机）、`persistence.py`、`events.py`（公开投影）。
- `services/api/app/core/`：`quiz_illustration.py`（SVG 规范化与白名单重建）、`quiz_illustration_policy.py`（`resolve_illustration_policy`：总闸/账户偏好/本次意图三态合成）、`quiz_illustration_enrichment.py`（兼容链路补图）。
- API 路由：`services/api/app/api/v1/diagram_library.py`（共享目录）、`diagram_materials.py`（公私创作）、`illustration_jobs.py`（题图任务）、`assessment_illustration.py`（CAT 补图启动）。
- 通用入口：`illustration/scenario_engine.py::generate_scene` 复用 `diagrams/pipeline.py::compile_authorized_scene`、V2 `composition/compile_scene` 与 V3 `compose/compile_svg`，业务层 `scenario.py` 保存会话与图片版本；`scenario_contracts.py` 无题目 ID 和私有答案，`references.py` 读取可见指定版本。`prompts/scenario_illustration.py` 提供独立场景需求/创作/审核合同，任务用途不会改变测评既有默认路径。
- 构建脚本：`scripts/diagrams/build_catalog.py`（稳定 ID/名称/别名/能力声明）、`scripts/diagrams/build_packages.py --check`（素材包可重建与一致性校验）。

## Public contracts

**V2 闭合协议（`illustration/contracts.py`，模型无素材搜索工具、不读库文件）**

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

- 共享目录：`GET /diagram-assets?q=&subject=&family=&education_level=&asset_kind=&page=0&per=12`（分页从 0，单页 ≤48）、`GET /diagram-assets/taxonomy`、`GET /diagram-assets/{asset_id}`（已登记组件附带 v2 元数据）、`POST /diagram-assets/{asset_id}/preview`（`params` + `profile=textbook|monochrome`；未知 404、非法参数 422）。目录仅列审核 `passed` 素材；预览不调模型、不写库。`q` 为本地词法检索：图库浏览（`gallery=True`，素材库页/配图选择器）启用容错——精确/包含/词法命中，或相似度 ≥0.55（≥4 字查询）、短查询（2–3 字对相近长度名称 ≥0.5，如 烧被→烧杯）；agent 严格档（`gallery=False`，V2/V3 组装检索）保持精确/包含/词法与 0.8 相似度门。
- 素材创作：`GET /diagram-materials?scope=private|public`（分页；`q` 为 token AND：子串命中标题/说明/别名，或命中标题/别名的字符子序列）、`GET /diagram-materials/catalog`（把审核通过的内置素材与公有自建素材合并为单一公有目录，支持 `q`、`subject`、`family`、`education_level`、`asset_kind` 和分页）、`GET /diagram-materials/templates`、`POST /diagram-materials/preview`（安全规范化 + 真实渲染）、`POST /diagram-materials/generate`（LLM 可编辑草稿，不自动发布）、`POST /diagram-materials`、`GET /diagram-materials/{asset_id}`（含 revisions 历史）、`PUT /diagram-materials/{asset_id}`（需最新 `base_revision`，冲突 409）、`DELETE /diagram-materials/{asset_id}`、`GET /diagram-materials/{asset_id}/preview.png`。scope=public 写操作仅管理员；私有素材他人不可见。素材库页面（`/diagram-library`）将内置与公有自建素材放在同一张可搜索网格中，个人素材单独提供编辑入口；公有素材新增仍经管理员入口的「范围」选择，非管理员无公有写入选项。
- 题图任务：`POST /quiz/illustration-jobs`（只接受已有题目 ID/revision）、`GET /illustration-jobs/{job_id}`（本人非 shadow 任务）、`POST /illustration-jobs/{job_id}/retry`、`GET /questions/{question_id}/illustration?question_revision=`（只读冻结题图，不启动模型）、`POST /assessment/questions/{question_id}/illustration`（按当前实例的 V1/V2/V3 模式读历史或启动 CAT 补图）。V2/V3 任务合同和 V1 组件需求上下文都携带本次 `generation_hint`；状态机 `queued → running → ready|not_required|failed`（执行位置双模式：默认进程内后台任务，`TEMPORAL_ADDRESS` 设置后由 media 队列 durable workflow 在 worker 进程执行，job DTO/轮询不变，ADR-0013）；进度只投影准备/检索/构图/审核阶段与百分比；`required` 与开关冲突 409 `illustration_disabled`；他人/已删除 404；ready 重试 409 `illustration_frozen`。

**SVG 规范（`QuestionIllustration`）**

- 兼容 schema 1/sanitizer 1、2 可读；组件图 schema 2/sanitizer 3；v2 schema 3/sanitizer 3。
- 组件图上限 128 KiB、1200 节点、4000 路径段、深度 10（旧图沿用 24 KiB）；defusedxml 白名单重建，禁止脚本、HTML、事件、外链、动画与 DTD/实体；前端以 SVG data URI 的 `img` 展示（不内联 DOM），支持放大与黑白印刷切换。

## Unified material interface

所有素材使用 `material.json.interface@1.0.0`，参数字段和编译规则不随学科、情景或素材 ID 改变。现有原生渲染器的参数合同经 `diagrams/interface.py` 迁移成结构化声明，随每个素材包独立保存；运行新增素材从同一版本的 SVG 声明式控件生成接口。新增科学能力仍需真实渲染器及事实核对，不因登记字段取得资格。

| 字段 | 含义 |
|------|------|
| `type` / 范围 / 默认值 | 值类型及合法域；默认值仅为预览或合法的呈现默认，不是题目事实 |
| `role` | `quantity/state/data/range/function/text/appearance/schematic/display`，区分事实、文字、示意及显示控制 |
| `fact_types` / `unit` | 可绑定合同事实的类型及单位；`predicate` 非空时必须匹配参数名，事实属于实际实体 |
| `default_rule` | `fact/optional/schematic/off/canonical/derived`，统一控制何时可省略绑定 |
| `derived_from` | 可自动显示的标定依赖：每组参数须全部已绑定，任选满足的一组 |
| `non_quantitative_allowed` | 是否允许显式示意参数；数据、函数和数值区间不得借此补造条件 |
| `qualitative_state` | 参数可表达的定性条件；公开题面只说明条件存在时不凭空补出数值 |
| `layout` | 通用左上原点、统一缩放、允许旋转、真实端口关系、实测外侧标注 |

公开事实的数值必须由实际题面引用支持，必要图同样检查。未冻结合同中，题面只说明某条件存在而未给数量时，只有全部匹配接口都明确声明相同的 `qualitative_state` 并许可非定量示意，才能保留该定性条件、移除模型补造的数值；真实题面数量、命题蓝图事实和冻结合同不作这种投影。明确指定素材时，需求响应 schema 进一步约束实际名称、能力及支持视角，避免模型声明素材没有的能力。

非按比例绘制的示意图遇到容纳关系边界失败时，编译器可依据实际区域边界缩小未绑定事实且明确为 `schematic` 的呈现尺寸，并重新实例化验证，记录调整和最终参数。该过程不修改定量条件、读数、液量或内部固有部件。补丁也只允许调整值域内的未绑定非定量参数；后续关系、遮挡及 PNG 合并审核仍须通过。

编译器只读取这些字段，不再依据某个素材 ID 特判绑定、轴标签或刻度。构图输出 schema 按本轮真实素材约束可用参数键、可绑定事实 ID 和示意参数，静态素材不接受虚构控制字段。模型优先给 `fact_bindings`，后端解析冻结事实；类型、单位、谓词、实体及值域同时检查，模型重复提供的数值必须相等。`schematic` 明确声明后才可调示意尺寸，`appearance` 可改变外观；`derived` 必须有真实依赖，`off` 可以隐藏未要求的标记。文字控件绑定题面文字，不自动把内部标签拆成独立实体。位置、组合、连线、层次及标签避让继续使用统一 SceneDraft。画布宽高由 profile 固定，与素材 viewBox 分开。`required_relations` 只记录不同实体之间的装配；区域/端口均按本轮登记名称限制；包含与浸没引用真实 region，不能用 port 冒充。同实体的结构条件记录为 `internal_relations`，发布前将旧式自关系等价归入该字段，冻结题不能这样改合同。内部关系不创建连接、不取得机器审核证明，合并 PNG 审核须逐项返回其 `verified_relations` ID，否则拒绝。内部要素标注可用素材坐标 `target.local_point`，编译器按变换后位置选择临近外侧；可设置 `leader=true` 为内部点或真实端口绘制定位引线；它不能作为物理连接端口。单独的示意素材按实测绘制边界等比放大居中，保留标签空间。

**新增 SVG 参数化**：`parameterization` 使用同一 schema，含至多 12 个参数、64 条绑定。每个参数提供 `type/role/description/default`，数值提供有限上下界；每条绑定只含 `parameter/element_id/attribute/factor/offset`。一个参数可联动多个图元。支持矩形、圆、椭圆、直线和文字的有限坐标/尺寸属性，文字替换与十六进制颜色；不支持可执行表达式、路径表达式、任意 transform 或外链。默认参数映射须与原 SVG 属性一致（坐标容许 0.05px 舍入差），否则拒绝；保存时预览默认实例及数值端点；出题时重新按事实实例化并安全重建，最终 PNG 必须合并审核。没有参数声明的旧 SVG 兼容为空接口。

素材编辑器提供参数规范 JSON、模板样例和独立预览值；预览不改规范默认值。AI 草稿返回 SVG + 相同的参数规范，可以继续人工修改。校验失败时将具体字段、绑定错误及上一版草稿交回模型修正，最多重试一次；不自动发布。接口的 `svg_bindings` 明确列出当前版本可改的实际图元。内接三角形模板用同一半径联动全部边，流程模板用可绑定的两段文字说明用法。上传控件只取得实际的 SVG 调整能力，不自动取得仪器刻度、物理端口或领域装配能力。

主提示词只有通用规则。素材子提示保留必要科学含义与限制，不重复参数绑定流程；按阶段及相关/选中素材加载，预算不随全库规模增长。命题阶段不送素材预览图；明确指定可见素材时加载其完整 SVG 和对应接口，其他素材的专属规则不混入。配方接口同时声明真实子实体、参数归属和结构关系。构图只加载本轮检索素材的完整 SVG，修订只加载已选素材源码。模型自行安排位置、比例、层次、连线及标签，接口用于约束科学事实与内部可调文字。审图收到最终完整 SVG、实际选中接口、解析参数和同一成图局部放大，并独立核对 PNG；局部放大最多三幅，涵盖定量材料及真实连接端口，来自原 SVG 的截图裁切，不重画读数或关系。机器接口合法不替代科学审核。冻结源保存接口内容哈希、解析参数、素材/指南版本和最终图件，历史图不重绘。

装配渲染器版本 `2.4.3` 保留温度计刻度前层、不透明底板及液柱平直端面；原生温度计统一绘制 -20～80°C 的 5°C 分度，装配层不补画第二套刻度。漏斗导管采用收窄斜口，真实端口与接收杯内壁接触。内部区域的外侧标注以整实例实测绘制边界为外包络，实际 region 决定对齐位置，避免所有候选都落在主体内部。点名、顶点和圆心标记新增 `placement=near_point`：以真实 `local_point`/port 为目标，在其附近排字并绘制定位点，不生成辅助线；Chromium 对最终主体的实际笔画作避让检查，允许标记落在圆等空心构造内，仍禁止遮挡敏感区域、科学线条或已有文字。`move_annotation` 修复可以调整同实例目标点、placement 和 leader，不能换实体或增添文字。

配方的 `entity_map` 必须使用全部注册子部件键，运行时独立校验，不依赖模型遵守 schema。组合父节点不是实际图元：标注只有在引用事实能唯一指向一个已授权子部件、且该部件拥有所请求区域时才自动转到子节点；有歧义时回传构图校正，不猜测点位或实体。

CAT 文字题完全没有材料投影时，需求调用按专用 `{material,brief}` schema 首次提取最小绘图投影；已有实体、事实、关系或未知量时仍只声明 brief。新投影逐项引用公开题干/选项，禁止 blueprint、改角色或引入答案。新投影沿用接口可证明的定性/呈现规范化：水存在不能变成虚构的液量、显示控制不作科学事实；已有数字、中文数量、比例条件及服务端 `to_scale` 约束不能被这种规范化移除，既有冻结合同不重解释。纯上下位置保留在布局意图，不能伪装成支撑或左右排序。普通数字列表也提供相关数据素材；图表绑定整体数组而非虚构逐项实体。函数的 `x²`、`x^2` 与 `x**2` 仅作等价语法规范化，仍经受限 AST。已明示且有原文支持的数值可格式化成其已声明符号的等式标签，隐藏/待读/派生值不适用。

标定统一声明 `minimum/maximum`（或 `maximum_parameter`）、`division_count/numbered_every/unit`；审图按已解析子实例参数计算最小分度和印数间隔，不将两者混为一谈。包含或浸没失败返回主体与容器的实际边界、所需内边距，供局部修订定位。`ScenePatchV2.set_param` 的 `value` 只可调整接口允许且未绑定科学事实的示意参数；真实事实的重绑定仍必须保持原值，不允许修订读数、量程或液位条件。

在非按比例场景中，编译器可将显式声明为 `non_quantitative`、未绑定事实且 `role=schematic` 的尺寸缩小以满足包含/浸没。先按真实区域边界提出范围内尺寸，再实例化确认实际区域成立；真实液位、读数、数据与已绑定尺寸不调整，整套关系、碰撞及 PNG 合并审核继续执行。有效参数和调整记录存入图件源，输入草稿不被自动修改。已确定的内接构造不适用这种尺寸拟合。

统一接口适用于全部登记素材及后续符合规范的新增素材，并不表示任意题目都存在可表达其条件的素材；缺少近点排版坐标时使用普通外侧标注，不虚构科学点位；缺能力、缺事实、科学关系不符或审图失败都必须拒绝交付。验收须包含未添加情景提示的素材与新建参数化素材，不以少数模板成功推定全库的科学正确性。

## V3：素材参考与自由创作

V3 不使用 V2 的实体映射、能力/视角硬过滤、端口、参数绑定或场景补丁。素材只提供可加工的参考；模型可以保留、替换或删掉任意参考图元，自行补画素材库没有的元素，也可以完全自绘。实现按通用协议处理，不按题型或素材 ID 分支。

| 私有合同 | 职责 |
| --- | --- |
| `VisualSpecV3` / `DrawingInputV3` | 自然语言需求和最多 48 项绘图输入；`explicit` 已知值、`depict_only` 图中呈现供读取、`symbol_only` 只显示符号 |
| `QuestionVisualContractV3` | 绑定题目身份/revision、绘图需求、公开题面及只供审核的 gold；哈希由服务端计算 |
| `VisualRequirementsV3` / `MaterialNeedV3` | 模型以名称、同义词、用途提出最多 12 类参考需求 |
| `CandidateBundleV3` | 名称/别名/特征/用途/说明模糊检索，每类最多 3 项、总计最多 12 项和 128 KiB 完整 SVG；按需求轮流提供首个候选，缺素材仍可自绘 |
| `SvgDraftV3` | 模型返回完整 SVG、alt/caption 及实际使用的授权素材 ID/版本；允许一次追加需求 |
| `DiagramSourceV3` | 私有原稿、所用素材/指南版本、原始参考哈希、成图哈希、实测边界与合并审核证据 |

普通命题可设计 `essential` 绘图数据，必要图必须在注册前完成；冻结 CAT 只能从公开题干/选项提取有逐字来源的数据，不能反推隐藏读数、改变 gold 或变成 essential。必要读图数据可供模型计算几何，但 `depict_only` 禁止直接印为读数、答案等式或写入 alt/caption；正常标定刻度允许。含待读/仅符号输入时，服务端统一从公开题干生成中性 alt 并清空 caption，再安全重建 title/desc，避免中文数量、数组或自由描述披露待读数据；图中泄题仍拒绝。已有绘图数据不能被需求或修图阶段更改。

创作结果沿用 `QuestionIllustration.schema_version=3` / `sanitizer_version=3`，不改变公开图片协议。服务端用闭合 SVG 语法重建（128 KiB、1200 图元、4000 线段，属性值至多 2048 字符，文字总量至多 600 字符），拒绝脚本、HTML、外链、事件、动画及外部资源；Chromium 渲染并实测越界。结构可解析、参考素材通过审核均不等于成图科学正确。单次合并审核读取实际 PNG、最终 SVG、题面、绘图数据、答案、解析、冻结量规和命题时的教材证据，独立解题并核对全部必要绘图输入及实际分度及独立重建的 `observed_values`（不能以已核对 ID 代替实际读数）；必要图提供同一成图的四幅局部放大。

修图仅回传闭合问题码、授权绘图输入/画布目标与服务端固定规则；审核自由文字、推导、gold、教材内容不回流创作模型。SVG 属性错误给出图元序号及属性限制，越界给出实际边界；重复原稿不能当作完成修复。审不过则在本模式内明确失败，不能假装成功或自动换版。注册阶段保留确实受审的冻结量规，绑定正式题目身份并核对成图/源码哈希。

## State & storage

- 内置素材（随仓库发布）：`services/api/assets/diagram_library/catalog.json` + `materials/<asset_id>/{asset.svg, material.json, usage_guide.json}`（1,130 个包 = 1,119 素材 + 10 配方 + 1 创作基底）；旧图版本不匹配则明确拒绝，目录与渲染器一同发布。
- 运行新增素材：数据根 `diagram_assets/<public|owner>/materials/<id>/versions/<revision>/`（同一版本目录内独立保存 `asset.svg`、`material.json`、`usage_guide.json` 和 `preview.png`；每次保存形成不可变版本，索引在完整版本写入后原子发布）。
- 素材子提示词与 SVG 按素材隔离存储。`usage_guide.json` 按出题、需求、构图、审图四阶段保存简短说明并独立版本化；主提示词只维护通用合同规则。出题/需求阶段仅提供当前语境相关的至多 12 项素材说明，构图阶段仅提供本轮候选的对应阶段说明，审图阶段仅提供实际选中素材的说明；不会将全库子提示词传给模型。私有素材读取指定不可变版本的说明，冻结题图保存素材版本、指南版本与最终图件内容哈希。
- V2/V3 运行状态：数据根 `illustrations/<owner>/{jobs,runs,artifacts,previews}`——jobs 快照、runs 追加阶段事件、artifacts 不可变冻结、PNG 预览；JSON 走文件锁与 `core/atomic.py` 原子写。`illustrations/.epochs/<owner>.json` 是跨进程 owner epoch tombstone（durable 模式 API 与 worker 共读；purge 只增不清，标记损坏 fail-closed，写落盘后复验并补偿删除——见 ADR-0013）。
- 双模式存储（ADR-0017）：`DOMAIN_DOCUMENT_BACKENDS=assistant=sql` 时，六类 JSON 文档（jobs/runs/artifacts/sessions/scenario_jobs/scenario_revisions）走 `assistant_documents` 表（`app/illustration/sql_store.py`，与站点助手共用该表但 kind 不相交）；PNG 预览字节与 epoch 标记文件仍在数据根——标记是跨进程 purge 闸，须在行删除后继续拦截迟到写。run 事件 append 在 SQL 侧为单事务行锁 read-modify-write；immutable 写、epoch 复验与补偿删除语义与文件模式一致。
- 工具助手的情景会话、轮次和成功版本复用 `illustrations/<owner>/` 根，独立于题目任务身份。多轮生成只新增成果版本，历史图不覆盖；账户删除同时取消两类任务并失效 owner epoch。详细合同见 [tool-assistant.md](./tool-assistant.md)。
- 兼容 v1 缓存：`students/<owner>.question_illustrations.json`。
- 两个运行根均由 `core/paths.py` 绑定并登记测试沙箱、账户删除（owner epoch 失效 + 后台任务取消）与孤儿清理（`core/orphan_cleanup.py` 类别 `illustrations`/`diagram_assets`；`.epochs` 标记目录被扫描跳过）。遗留 `queued/running` 记录标 `failed/run_interrupted`、显式重试开启新运行——文件模式由读路径内联标记；durable 模式（media 队列）由各 job workflow 的 settle activity 兜底 + worker 启动对账结算，读路径不再依赖本进程任务表（ADR-0013）。

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

2. **v2 装配**：服务端材料合同 → 模型声明 `VisualBriefV2`（无搜索工具）→ 本地按名称/别名/语义能力检索候选（能力与视图硬约束，学段筛选；候选卡包含参数的条件含义、单位、定性许可、端口、敏感区域与配方关系，提供本轮检索素材完整 SVG 代码（含版本/hash，不截断、不送全库源码），模型直接据图元/坐标/文字设计组合，样例数值不是本题事实）→ 模型提交 `SceneDraftV2`（可在实体/事实不变时请求一次重新检索）→ `compile_scene` 按解析参数生成端口/液面/刻度/边界，用 Chromium 实测边界与字体检查支撑、浸没、遮挡、出界、路由与可读性 → `preview.py` 渲染真实 PNG → 一次合并审核（实际 PNG/题面/答案/量规；刻度题用同一成图局部放大）→ `machine/combined=passed` 才冻结交付。可修复问题走 `ScenePatchV2` 后重走编译/PNG/审核；无修复成功流程消耗声明、构图、合并审核三次调用。
3. **普通出题 vs CAT**：普通出题在题目注册前完成题图（`essential` 缺图或未过审不能成为可作答任务；私有 artifact 先准备，再在锁内检查材料不可变性并整组注册，journal 题目引用是交付点）；CAT 文字题先冻结独立可答，补图只作 `supplemental`，且不重新生成文字题。
4. **任务生命周期**：相同题目身份重复启动复用已有任务，并发只创建一个运行；ready 产物不可覆盖，改材料须新 revision；只有进行中 CAT 的当前/最后题可发起补图或重试。旧 `failed` 任务遇到提示词、目录或渲染器版本更新时，两条启动 API 在重新检查当前题权限后自动建立新运行；旧失败记录保留。同版本失败由显式重试启动。策略 off 的新请求直接返回 `not_required`，不创建失败任务。
5. **素材创作**：上传/模板/LLM 草稿进 Modal 编辑器 → 预览 → 显式保存为不可变版本（乐观锁）→ 启用后进入该账户出题候选；上传 SVG 按其声明式参数接口调整文字和几何；不自动取得科学刻度或物理端口，实际题图仍须实际 PNG 合并审核。

## Dependencies

- **Node/Playwright Chromium**（本地）：布局实测与实际题图 PNG 渲染；缺浏览器即明确失败，不把结构可解析当视觉审核通过。
- **LLM 图片输入**：`LLM_SUPPORTS_IMAGES=1` 声明真实能力；未声明或模型无视觉时 V2/V3 返回失败。
- **出题路径**：`generate_quiz`/`fit_quiz` 与 M4 CAT/约束出题（[assessment.md](assessment.md)）经 `quiz_illustration_policy` 决定是否生成；题目注册与作答上下文由 M2 评价域承载。
- **prompt 注册表**：出题材料合同 `quiz_illustration_authoring@1.16.0`，V2 需求 `@2.21.0`、构图 `@2.22.0`、冻结提取 `@3.1.0`、补丁 `@3.0.0`、合并审核 `@3.2.0`；V3 命题 `@1.0.0`、需求 `@1.1.0`、创作 `@1.3.0`、合并审核 `@1.3.0`（`prompts/illustration_v3.py`），V1 需求/构图/语义审核分别为 `@1.6.0` / `@1.9.0` / `@1.7.0`（`prompts/quiz_illustration.py`）；素材创作草稿 `diagram_material`。需求视角须属于候选素材支持视角，画布 profile 与观察视角分别约束。

## Invariants / security boundaries

- 模型没有素材搜索工具，不读取库文件；候选 ID/版本/能力/参数全部由服务端决定；模型自报 hash 或「已通过」字段不能跳过任何审核阶段。
- V2 待求读数和答案不进入构图事实值投影；V3 必要读图数据按 `display` 供创作模型绘图，答案/解析/量规与教材证据仅供独立合并审核。各版本均禁止在 alt/caption/待求标签揭示答案。
- V2 合并审核的修复反馈只回传服务端白名单错误码、已授权实例/子部件/关系/标注目标及有限补丁操作；私有审核描述、任意 code/target 文本和修订建议不回流构图模型。机器几何反馈独立保留确定性边界信息。
- 发布门：`QUIZ_ILLUSTRATION_VISUAL_REVIEW=active` 且 `machine/combined=passed` 才可交付 V2/V3 图；历史 V2 的 `machine/visual/joint=passed` 仍可读；off/shadow 或旧账户审图偏好不能放行未经审核的图。
- 历史冻结图不因目录、提示词或开关变化重绘；改冻结题图必须新 revision。
- 学生接口只返回任务身份、视觉角色、进度、规范化图片与闭合失败码/重试标志；原始事实、gold、场景、候选轨迹、内部失败阶段与审核文字留在账户私有记录；shadow 任务不可经公开任务接口读取。
- 公有库写操作仅管理员（`require_admin`）；个人素材仅属主可见；预览不调用模型、不修改库或学生记录。
- 函数图使用受限 AST 解释器，不执行用户代码；单位/数据类型/区间必须与参数合同匹配。
- 构图候选事实按参数语义和单位筛选：函数表达式仅供函数参数，不因 JSON 同为字符串而成为轴标签或标题的候选；编译阶段仍独立验证事实绑定与实体归属。
- 内置素材是项目原创矢量（源码资产）；新增定量或科学装配能力必须登记单位、条件参数、动态端口、区域与关系规则，不能只靠标签或相似外形取得资格。
- 完整构图只声明整体实体，内部标识不伪造独立对象或端口。配方检索依据已登记子组件的共同出现识别被关系短语隔开的名称，仍受需求数量、实体、能力和视角校验。固定短标识可登记为允许标记，重复标注复用实际图中的标识，但仍检查目标端口/区域有效。明确中文数量可校验，科学名词或定义不能凭空支持数值事实。
- 红移对照在同一线性波长轴上按比例伸展；`shift` 是最右谱线的示意像素位移，不能作为 nm 或红移值。单位圆只以 `angle` 控制角度，半径 1 为固定已知标记。科学模型的事实核对来源随素材溯源保存，例如 [NASA 光谱说明](https://science.nasa.gov/mission/webb/science-overview/science-explainers/spectroscopy-101-beyond-temperature-and-composition/)。
- 气柱驻波采用同一位移包络的上下幅值边界，避免将正负瞬时波形误当成两种模态；闭端位移为零，开端为腹点，内部节点随允许模态确定性变化。

## Configuration

- `QUIZ_SVG_ENABLED`（默认 `1`）：题图运维总闸（紧急止血）；关闭后 `required` 意图返回 `illustration_disabled`，已冻结历史题图仍可读。
- `QUIZ_ILLUSTRATION_PIPELINE`（`v1|shadow|v2|v3`，默认 `shadow`）：保留普通出题旧默认，账户显式选择 V3 时所有出题入口使用 V3；旧 V1/V2 账户偏好的原作用范围保持兼容。CAT 由账户默认 `profile.prefs.quiz_illustration_mode`（缺省 `v1`）或本次 `illustration_mode` 选择 V1/V2/V3，实例持久化版本；重试保持原版本。
- `QUIZ_ILLUSTRATION_VISUAL_REVIEW`（`off|shadow|active`，默认 `active`）：真实 PNG 合并审核发布门。
- `QUIZ_ILLUSTRATION_MAX_CALLS=10`、`QUIZ_ILLUSTRATION_DEADLINE_SECONDS=120`、`QUIZ_ILLUSTRATION_MAX_REPAIRS=2`：V2/V3 每任务独立配图预算；协议校正另计、最多 2 次，图件修复最多 2 次，追加检索最多 1 次，均受调用和时间总额限制。正常成功为需求、构图、合并审核 3 次调用；修复前保留重绘及其审核调用。普通出题的文字预算仍为 `ASSESSMENT_GENERATION_*`（默认 6 次/90 秒），图片调用不消耗文字预算；同批图片阶段总时限 120 秒、并发 2。可绘制条件缺失时允许剩余文字预算内重新命题一次，共用原图片截止时间。无跨版本自动降级。warnings 不阻断，科学错误、泄题、必要条件缺失及审核不确定仍拒绝。
- V1 补图预算固定 90 秒 / 8 次调用 / 最多 2 次场景修复，生成单调用 30 秒、审核单调用 20 秒、生成阶段保留 10 秒审核余量。需求可有一次同池校正；正常含语义审核流程仍为 3 次调用。需求获得真实素材名称和功能词表；漏字段及校验错误回传受限修复。整图越界可整体平移/等比缩放，材料参数不改。数量控制由素材接口的 role、unit 及公开题干来源授权，读数与量程角色不能互借，选项不能授权数值；未知读数时选无读数条件的素材。启用账户语义审核时必须输入实际 Chromium PNG，无图片能力、无法解析或 passed 与失败问题码矛盾均不缓存成功；有限的纯呈现 warnings 不阻断交付；关闭该偏好仍执行确定性编译与公开条件检查。审核只把闭合问题码回传构图模型，私有答案/审核描述不回流。

V1 编译器版本 `1.2.0` 与构图提示、检查及预览共用 `instantiate_asset` 的实际 drawing/ports/regions，避免绘制原生图却检查另一视图的区域。布局使用通用 `layout_relations`：`inside|immersed_in|supported_by|suspended_from` 引用现有节点的注册 `region` 或 `anchor`，并附逐字公开题干 `source_quote`。多节点缺公开肯定关系声明时要求模型校正；否定子句不能授权正向关系，浸没覆盖对应包含关系，普通背景介词不产生包含条件。`legacy_layout.py` 只按真实区域边界/接触点平移节点，先处理目标依赖再处理上层，不依赖模型声明顺序，不改参数、比例或对象身份；未知区域、不可满足关系及冲突仍拒绝。wire 只连接 `kind=wire` 的真实端子，从最近外侧逃逸并绕开全部主体、敏感区域及其他端子网络的导线；仅显式共享端子网络允许合流。相邻面对端子可直接连接，普通定位锚点不能冒充物理端口；检查会拒绝不同网络交叉或重合产生的额外连接。该协议及几何求解不按素材 ID 或题型分支。

实际素材的 `parts` 按 background/body/connection/front/labels 分层，同层先绘制关系目标，再绘制其内含或受支撑部件；容器填色不再依赖模型节点顺序遮挡内容。布局依赖环直接拒绝，不以节点重合冒充关系成立。

- `LLM_SUPPORTS_IMAGES`（默认 `0`）：部署声明模型图片输入能力。
- `QUIZ_DIAGRAM_MODE`：仅保留兼容配置含义，不能开启模型直接输出 SVG。
- 账户 `profile.prefs.quiz_svg_enabled`（缺省 true）与本次 `illustration_request=auto|none|required` 意图共同控制新生成；`quiz_illustration_review_enabled` 只控制兼容 V1 语义审查，不能取消 V2/V3 发布门。`quiz_illustration_mode` 只提供默认版本，出题中心可以按次覆盖。
- 模型工具参数不能自行开启生成或伪造强制要求：Chat provider 绑定可信账户与当前用户意图；CAT 的本次意图保存于实例并在 next/恢复时复用。

## Observability

- 任务/run 追加事件流记录各阶段；`catalog_version()` 与对应 V2/V3 renderer 版本 随产物落盘，可追溯装配环境。
- 构建期校验：`python3 scripts/diagrams/build_packages.py --check`（可重建与一致性）；`apps/web/scripts/check-diagram-library.mjs` 渲染全部目录素材输出人工审查拼图与边界报告（自动检查不替代截图审阅）。
- 真实模型验收：`python3 scripts/acceptance/illustration/live.py --live-llm --output <repo 外目录>` 逐轮输出模型 JSON、SVG、实际 PNG 与 report；验收记录见 [../validation/diagram-library.md](../validation/diagram-library.md)。
- 资产级清单（名称/别名/能力/参数）见 [../reference/diagram-assets.md](../reference/diagram-assets.md)；测评侧合同见 [assessment.md](assessment.md)；图库与题图协议已并入本文档。

## Tests / acceptance

`services/api/tests/`（聚焦运行：`cd services/api && python -m tests tests.illustration.test_illustration_v2`）：

- `diagrams/test_diagram_library.py` — 共享目录、参数、规范化与兼容构图。
- `diagrams/test_diagram_adapters.py` / `test_diagram_expansion.py` / `test_diagram_guidance.py` — 全库适配器、扩充登记与阶段提示。
- `diagrams/test_diagram_materials.py` — 公私创作、版本、乐观锁与权限。
- `illustration/test_illustration_v2.py` — 合成材料 + fake LLM 经真实 Chromium 检查科学关系、事实绑定、补丁与审核门（不使用真实模型凭证）。
- `illustration/test_illustration_v3.py` / `test_illustration_v3_integration.py` — 自由改绘、缺素材自绘、私有素材隔离、必要读图数据、实际 PNG、修复反馈、真实 JWT/ASGI 任务、五题预算隔离及注册冻结。
- `illustration/test_illustration_jobs.py` — 经真实 JWT/ASGI 路由检查所有权、公开投影、单次运行、重试、不可变产物与删除竞争。
- `illustration/test_quiz_illustration.py` / `test_quiz_illustration_enrichment.py` — 出题侧材料策略与兼容补图链路。
- `diagrams/test_legacy_layout.py` — 通用区域/锚点关系、否定/缺失条件、不可容纳约束、端子逃逸与笔画避让。
- 验收基线与真实模型记录见 [../validation/diagram-library.md](../validation/diagram-library.md)；fake LLM 通过不等于真实验收。

## Related ADRs

- ADR-0001 source-only（内置图库是原创源码资产随仓库发布；运行产生的题图/个人素材是部署本地运行数据）。
- ADR-0002 运行数据单根（`illustrations/`、`diagram_assets/` 均为统一数据根下的绑定存储根）。

- [ADR-0006](../adr/0006-material-assisted-svg-authoring.md)：V3 以素材为参考自由创作，公开成图协议兼容，实际 PNG 合并审核守住发布边界。
