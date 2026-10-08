# Tool Assistant（工具助手与情景配图）

工具助手是与测评中心、资料中心平行的教学工具栏目。首个子工具「情景配图」支持自然语言生成、素材选择和多轮优化，V1/V2/V3/V4 共用教学配图底层能力；V4 直接在情景配图工作台中运行。架构边界见 [ADR-0007](../adr/0007-shared-illustration-tools.md)，绘图协议和素材能力归 [diagrams-illustration.md](./diagrams-illustration.md)。第二个子工具「模拟实验室」（确定性双引擎化学实验台）独立成文，见 [chem-lab.md](./chem-lab.md) 与 [ADR-0019](../adr/0019-deterministic-dual-engine-lab.md)。

## Purpose / Scope

- `/tools` 是工具目录，`/tools/illustration?session=<id>` 是独立配图工作台。工作台不渲染二级页头：顶栏按 `lib/nav.ts` 的 `NAV_SUBTITLES` 显示复合标题「工具助手 · 情景配图」，返回入口放在会话侧栏顶部；窄屏（<1024px）三栏退化为单列（对话 → 成果 → 会话）。
- 每轮描述情景或修改要求，选择绘图模式，并可选此前成功图片作为修改基础。会话、轮次和成功版本由后端保存，刷新可恢复。
- V1 使用已有模型绘图能力；V2 按素材参数和关系装配；V3 以完整 SVG 素材为参考，允许改造、组合和补画；V4 由服务端配置的第三方生图模型输出 raster 图片，并可在题目编辑器中保存为试卷素材。
- V2/V3 提供素材弹窗：内置素材与审核通过的公有创作素材合并为一个可搜索公有目录，另提供个人素材入口；支持名称/说明/别名搜索、学科和素材族过滤。未选素材时自动声明需求并检索。
- 主聊天教学调度与课堂课件自动配图仍由各模块拥有；本工具提供独立聊天配图入口和可复用服务，不通过题目注册或学习评价账本。

## Owned code

- 前端路由归 `apps/web/src/app/(workspace)/tools/`，工作台和素材弹窗为 `components/pages/tools/IllustrationWorkspace.tsx`、`MaterialPicker.tsx`；工具入口、工作台、素材库和组卷编译器共用 `ToolMarks.tsx` 中的原创几何 SVG 标记，客户端为 `lib/api-illustration-tools.ts`。
- 后端 `app/illustration/scenario_contracts.py` 拥有输入合同，`scenario.py` 拥有会话及版本 service，`scenario_engine.py::generate_scene` 为无题目身份的生成入口，`references.py` 校验选定素材；路由为 `app/api/v1/tool_illustration.py`。
- 全站入口由 `lib/nav.ts` / `lib/i18n.ts` 提供；站内学习助手通过目录中的 `tools` 与 `tools_illustration` 导航目标打开。
- `app/diagrams/` 拥有素材目录、参数和公私素材读取；测评拥有出题策略与业务发布规则。

## Public contracts

全部端点使用 `/api/v1/tools/illustration` 前缀，需登录，身份由 `resolve_student_id()` 解析：

| 方法 | 路径 | 含义 |
| --- | --- | --- |
| GET / POST | `/sessions` | 列出本人会话 / 创建会话 |
| GET / DELETE | `/sessions/{session_id}` | 恢复会话和历史 / 删除会话及其任务成果 |
| POST | `/sessions/{session_id}/turns` | 提交一轮配图并返回异步任务 |
| GET | `/jobs/{job_id}` | 观察本人任务进度及结果 |
| POST | `/jobs/{job_id}/retry` | 重试可重试失败，保持原需求、模式和基础版本 |

轮次输入为 `message`、`mode=v1|v2|v3|v4`、`selected_materials=[{asset_id,version}]`、`base_revision`、可选 `source_revision` 和 `request_id`。`base_revision` 是提交时会话当前版本的并发校验；`source_revision` 选择修改基础，省略时使用当前成功版本。内置素材引用为目录 ID，自建素材引用为 `material.<material_id>`。客户端不得提交 SVG 正文、审核证据或自报属主。

任务状态为 `queued → running → ready|failed`，公开阶段为 `preparing/retrieving/composing/rendering/reviewing/ready/failed`。V1/V2/V3 成功结果使用规范化 SVG 图片载荷，V4 成功结果使用 owner-scoped raster artifact 及图片元数据。公开失败只含可用错误码和重试信息，模型私有审核正文不进入页面。

同会话进行中任务返回 409；基础版本和重复提交由服务端校验。相同幂等 ID 复用原轮，冲突请求不重复生成。他人或已删除资源不可见。

## State & storage

- 会话、任务和成功版本位于统一运行根 `illustrations/<owner>/{sessions,scenario_jobs,scenario_revisions,previews}`，使用文件锁与 `core/atomic.py`；与既有题图的 `jobs/runs/artifacts` 分开，不在浏览器持久化服务端成果，不进入版本库。企业模式下六类 JSON 文档路由至 `assistant_documents` 表（ADR-0017，`DOMAIN_DOCUMENT_BACKENDS=assistant=sql`），预览 PNG 与 owner epoch 标记仍留在数据根；会话删除按 kind 删行并落 `deleted` 墓碑文档，语义与文件模式一致。
- 只有成功生成才增加图片版本。每个成果冻结模式、需求和素材版本来源，历史成果不被重试覆盖；失败和进行中轮次保留既有图片。
- 多轮输入结合基础版本所属的历史需求及其已有图稿，并保留当前轮要求；用户修改事实或对象会形成新合同及版本。
- 进程重启后的无存活任务标记 `run_interrupted`，用户可显式重试；不静默重放模型调用。文件模式由读路径内联标记；durable 模式（`TEMPORAL_ADDRESS`，media 队列）由 job workflow 的 settle activity 兜底结算 + worker 启动对账（ADR-0013），轮询合同不变。
- 复用已登记的 illustrations 账户数据类别。账户删除提高 owner epoch（磁盘化 tombstone，跨进程生效）并取消场景和题图任务（durable 模式 best-effort cancel 该 owner 在途 workflow），迟到结果无法重建账户数据；单会话删除同样先取消在途任务再落墓碑阻止迟到写入。

## Main flows

1. 用户从工具助手进入情景配图，新建或恢复会话。
2. 选择模式、基础图片和可选素材，提交描述或修改要求；服务端校验版本、权限与幂等，并受理异步任务。
3. 手选时读取可见且有效的指定版本；自动时由模型提出需求，服务端检索并提供有界候选。V2 仍受能力和视角条件限制；V3 可以自由处理素材和自行补画。
4. 创作结合会话需求和基础图，经过规范化、实际 Chromium 渲染和情景审核。成功冻结为新版本，失败提供闭合状态与重试入口。
5. 前端观察任务进度，完成后刷新会话并展示成果；用户继续发送修改请求，或选择历史成果预览、作为新一轮基础、下载 SVG。

## Dependencies

- 共用配图引擎、SVG 规范化和真实渲染：[diagrams-illustration.md](./diagrams-illustration.md)。
- Next.js 工作区、`apiFetch`、Input/Modal/Pager 原语、主题和 i18n：[frontend.md](./frontend.md)。
- 账户身份、运行数据单根及删除清理：[identity.md](./identity.md)、[backend-runtime.md](./backend-runtime.md)。
- 站内助手只提供目录和导航：[site-assistant.md](./site-assistant.md)。

## Invariants / security boundaries

- 不伪造测评题目，不读取或写入评价 journal，不把情景审核结果用于题目发布。
- 选材只传引用，由服务端重新鉴权；私有素材不跨账号可见。历史 SVG 已冻结后不依赖素材当前启用状态重新生成。
- 手选时不偷偷补入其他候选；V2 无法满足能力时明确失败，V3 可以自行绘制缺失元素。
- 不把 raw SVG 原稿、内部审核证据或隐藏推理作为聊天回答；只展示安全规范化图像、用户消息和公开状态。
- 有界调用、时间、修复和素材输入预算；同会话进行中任务互斥，切会话或换账户不得串入迟到结果。
- 演示站保持只读，不提交生成请求或尝试读取不存在的运行快照。

## Configuration

绘图继续复用 `QUIZ_SVG_ENABLED` 部署总闸与 `QUIZ_ILLUSTRATION_MAX_CALLS/MAX_REPAIRS/DEADLINE_SECONDS` 预算配置，各模式成功发布前需要支持图片输入的模型和 Chromium 实际审图。用户的 `quiz_svg_enabled` 出题偏好不控制主动调用的情景配图。模式每轮明确选择，不改变测评账户默认或已有 CAT 实例选择。

## Observability

任务阶段和失败码可由 GET job 观察，客户端显示准备、检索、创作、渲染及审核进度。运行数据和私有审核材料只保存在本人运行根；失败不伪装成已完成图像。

## Tests / acceptance

验收包含：三种模式、自动/手选素材、私有素材隔离、多轮基础版本、提交幂等、并发冲突、失败重试、重启恢复及删除竞争。浏览器验证导航、聊天出图、版本切换、素材搜索和刷新恢复，并检查浅深主题及较窄桌面。

原有出题、聊天出题及 CAT 配图回归仍使用 `tests.illustration` 与测评浏览器用例；协议测试采用 synthetic 输入、fake provider 和临时运行根，不替代真实模型质量验收。

## V4 生图与组卷编译器（2026-10）

- V4 不再是独立工具栏。`/tools/illustration?mode=v4` 进入统一情景配图工作台，旧 `/tools/image` 深链重定向到这里。V4 支持 `direct`、`auto`（服务端检索审核过的 SVG 素材）和 `selected`（教师选择素材）三种参考路径，结果以 raster artifact 展示、下载并可继续修改。
- 生图网关由 `app/api/v1/tool_image.py` 提供 `GET /tools/image/capability`、兼容的 `GET /tools/image/providers` 和 `POST /tools/image/generate`。客户端只提交意图、参考素材 ID 和画幅；provider、protocol、base URL、key、model 都由 `IMAGE_API_*` 环境变量决定。`IMAGE_API_PROTOCOL` 支持 `openai_compatible`、`dashscope`、`seedream`，model 原样转发，因此新增第三方 OpenAI-compatible 模型无需改前端。旧的 GPT/Qwen/Seed 变量保留迁移兼容。
- 参考素材由服务端按当前用户重新鉴权并渲染，供应商临时 URL 会尽量物化为 data URL；未配置或供应商失败返回明确错误码，不生成演示假图。设置页只展示 active backend 的状态、协议、模型和参考图能力，不允许用户在客户端切换模型或填写密钥。
- `/tools/worksheets` 是按用户隔离的服务端组卷草稿 API，运行数据存于 `worksheets/<owner>`，使用 `core.atomic`、文件锁、ETag 和账户删除清理。新建或保存试卷必须绑定当前用户拥有的真实学习区（`workspace_id`），服务端重新鉴权工作区归属；空值、失效或他人工作区直接返回 `worksheet_learning_area_required|invalid`。设置阶段可搜索并分页选择知识图谱点、填写出卷目标/考试说明；教材检索开关只有在选中知识点且该学习区确实绑定教材时可用，未选知识点时服务端不会触发检索。明确开启检索却没有范围、证据或服务能力时返回可恢复错误，不静默生成无依据试卷。API 支持单题追加、按题型/数量/难度/分值批量生成、逐题编辑/删除/重写、保存 V4 配图以及导出学生版或教师版 Markdown/HTML。命题指导和出卷目标只作为生成约束，服务端会清理模型误写的背景标签，不把提示词拼进题干。
- Web 与 Mobile 共用 `@next-tutor/api-client`。组卷工作台采用最左题目导航、中间出题对话、最右当前单题预览；长题目编辑和设置在 Modal/Sheet 中进行，批量生成、整卷预览、打印均通过按钮打开，日常动作是一题一题追加。正式抬头、考试信息、姓名/班级栏和题号分值进入整卷预览；学生版隐藏答案与解析，教师版保留。预览、打印和 HTML 导出统一使用 KaTeX：Web 复用聊天 Markdown 渲染器，服务端 HTML 内嵌版本化 KaTeX 资产并在独立 A4 打印页面等待字体、图片和公式完成后打印，支持 `$…$`、`$$…$$`、`\(…\)`、`\[…\]`。题目不强制背景段落，答案和解析始终是独立可编辑字段。

### V4 provider adapter contract

- `openai_compatible` 无参考图调用 `/images/generations`，有参考图调用 `/images/edits`；请求体使用服务端配置的任意 model，并按画幅发送 size。
- `dashscope` 使用异步图像生成接口和任务轮询；`seedream` 使用方舟图像生成接口。协议只负责字段适配，模型名和新版本不在代码中枚举。
- 网关限制超时、参考图数量和响应大小；下载临时 URL 失败时保留可诊断的外链结果，组卷附件只接受经网关物化的 `data:image/*`。
