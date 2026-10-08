# Chem Lab（模拟实验室 · 化学实验台）

模拟实验室是工具助手下的第二个子工具：一个可交互、可回溯的虚拟实验台。首个主题为化学，实验现象由**确定性模拟引擎**生成而非模型即兴输出，因此每个会话都可以回放、分叉、生成结果卡。工程决策见 [ADR-0019](../adr/0019-deterministic-dual-engine-lab.md)，工具栏目整体边界见 [tool-assistant.md](./tool-assistant.md)。

## Purpose / Scope

- `/tools/lab` 是实验目录页，`/tools/lab/chemistry?session=<id>` 是化学工作台。顶栏按 `lib/nav.ts` 的 `NAV_SUBTITLES` 显示「工具助手 · 模拟实验室」；`?experiment=` 进入准备视图（目标、安全信息、预测问题），`&at=<revision>` 进入只读历史点横幅。
- 学生在 SVG 实验台上取放器材、倾倒、加热、加试剂、用仪器测量读数；引导栏给出目标与阶梯提示，观察记录、证据链、时间线与分支对比全部来自引擎事件流。
- 两种模式：`guided`（按步骤目标推进，观察可见性受内容包 `observation_visibility` 限制）与 `explore`（自由探索，全量观察可见）。
- 实验台是旁路工具：不经 `/chat/stream` 伪造学习事件，不写学生模型/知识图谱；首期不提供实验 AI 工具。

## Owned code

- 后端域模块 `services/api/app/chem_lab/`：
  - `catalog.py` 内容目录（manifest sha256 锚定每个内容文件，改动必须重跑 `scripts/chem_lab/validate_pack.py` 刷新 manifest）；`pack_schema.py` Pydantic 内容模式；`dsl.py` 规则 DSL 静态检查（闭集、上限、无循环）。
  - `engine/` 确定性引擎：`reducer.py`（命令→状态迁移，**任何命令含被拒绝者 revision 均 +1**）、`model.py`（状态与 `state_hash`）、`reaction.py`/`phase.py`/`safety.py`（反应、相态、安全边界）、`projection.py`（RenderFrame 投影）、`guidance.py`（阶梯指导与目标状态）、`replay.py`（回放/分叉）。
  - `service.py` 会话服务（每 owner 一把文件锁串行化读-改-写；幂等、base_revision 验收、ACK 存储与重放）；`persistence.py` 存储布局；`providers.py` AI 扩展点（见下）。
  - 路由 `app/api/v1/tool_chem_lab.py`，前缀 `/api/v1/tools/lab/chemistry`。
- TS 镜像引擎 `packages/domain/src/chem-lab/`（与 Python 引擎同构：model/reducer/reaction/phase/safety/projection/guidance/replay/units），经 `packages/domain/src/index.ts` 导出。
- 前端 `apps/web/src/components/pages/tools/chem-lab/`：`chem-lab-worker.ts` + `chem-lab-engine.ts`（Web Worker 内跑 TS 镜像做本地预测，Worker 失败回退主线程）；`useChemLabSession.ts`（有序命令队列与同步状态机）；`chem-lab-geometry.ts`/`chem-lab-renderer.ts`/`assets.tsx`（RenderFrame→原创 SVG 器材）；`LabStage.tsx` 等交互组件；`ChemLabWorkspace.tsx` 组合根。客户端封装 `apps/web/src/lib/api-chem-lab.ts` + `packages/api-client/src/tools/chem-lab.ts`；契约生成物 `packages/contracts/src/generated/chem_lab.ts`。
- 内容包 `app/chem_lab/content/`：`experiments/`（6 个实验定义）、`packs/`（发布包）、`species/`、`rules/`、`equipment/`、`concepts/`、`vectors/`（回放向量）、`manifest.json`。全部为项目自研合成内容，受 [../compliance/content-policy.md](../compliance/content-policy.md) 约束。
- 作者工具 `scripts/chem_lab/`：`validate_pack.py`（schema/引用/DSL/i18n/hash 校验 + manifest 刷新）、`replay_pack.py`（单包回放）、`render_preview.py`（预览渲染）。
- 站内助手：`app/agents/site_assistant/product_catalog.json` 登记 `tools_lab`、`tools_lab_chemistry` 两个 route_id；前端 `lib/assistant/routes.ts`、`page-context.ts` 同步映射。

## Public contracts

全部端点需登录，身份由 `resolve_student_id()` 解析，前缀 `/api/v1/tools/lab/chemistry`：

| 方法 | 路径 | 含义 |
| --- | --- | --- |
| GET | `/catalog` | 实验目录摘要 + 能力声明 |
| GET | `/experiments/{id}` | 实验公开投影（准备视图；预测选项**剥离 correct 标记**） |
| GET | `/experiments/{id}/engine-pack` | TS 镜像运行时包（与 Python 引擎输入一致，同样剥离答案） |
| GET / POST | `/sessions` | 本人会话列表 / 创建会话（experiment_id + pack_version + mode + language） |
| GET / DELETE | `/sessions/{sid}` | 会话快照（含 render_frame、engine_state、guidance、同步元数据）/ 删除（墓碑化） |
| POST | `/sessions/{sid}/commands` | 提交命令（command_id 幂等 + base_revision 验收）→ ACK |
| GET | `/sessions/{sid}/events` | 事件流分页（after_seq 增量拉取） |
| POST | `/sessions/{sid}/checkpoints` | 命名检查点 |
| POST | `/sessions/{sid}/fork` `/reset` | 从检查点/任意 revision 分叉；重置回起始态 |
| POST | `/sessions/{sid}/finish` | 结束并生成结果卡（目标达成、预测核对、观察与概念） |

开关：`CHEM_LAB_ENABLED`（默认开）、`CHEM_LAB_MAX_SESSIONS_PER_OWNER`（默认 60）。

## 同步协议（双引擎一致性）

- 服务端 Python 引擎是唯一权威，逐条重跑每个命令；TS 镜像在 Web Worker 中做**本地预测**以实现即时反馈与离线降级。
- 客户端命令队列有序发送，第 i 条排队命令的 `base_revision = tip + i`——这成立是因为引擎语义保证**任何命令（含 rejected）revision +1**，该语义是公开契约，不得改动。
- ACK 携带 `revision + state_hash`：与 Worker 预测 hash 相等即证明双引擎状态一致，免回拉快照；不等则标记 `diverged` 并整快照 resync；409（base_revision 冲突）丢弃本地预测重新对齐；断网进入 `offline_preview`（本地继续预测、恢复后重放队列）。
- 一致性由 CI 强制：`content/vectors/` 的回放向量同时被 Python（`tests.chem_lab.test_replay_vectors`）与 TS（`packages/domain` 回放测试）执行，state_hash、事件类型、关键读数必须一致。
- 客户端时序铁律：`ChemLabWorkspace` 中 detail 与 live-session 两个加载 effect **不得共享 generation 计数器**——共用时后声明的 effect 在同一次挂载里立刻顶高计数，detail 请求返回即被守卫误判为"已被新运行取代"，`setLoading(false)` 被跳过，准备页永久停在"正在创建会话…"（`?experiment=` 深链必现，e2e `chem-lab.spec.ts` 钉死）。同理，任何"加载态 + 异步结果"配对都必须保证 finally 复位不被丢弃。
- 所有 chem-lab API 调用在 `packages/api-client/src/tools/chem-lab.ts` 统一携带 30s 超时：服务端全是毫秒级确定性本地操作，永不返回的请求只能是传输层丢失，必须以可重试的 typed 错误呈现给 UI，绝不留永久转圈。

## 故障排查（./start.sh 后看不到实验）

- 目录为空且有"加载失败"提示：浏览器 devtools Network 看 `/api/v1/tools/lab/chemistry/catalog`——401=登录态失效（重新登录即可）；网络错误=后端地址与启动日志 `backend on :<port>` 不一致（prod 构建把端口烘焙进产物，端口变化应由 start.sh 自动重建，必要时 `REBUILD=1 ./start.sh`）。
- 点进实验后整页停在"正在创建会话…"：上方"客户端时序铁律"的加载态竞争（已修复）；若再次出现，优先核对两个 effect 的守卫与复位路径。
- "我的会话"区为空但目录正常：会话接口独立失败不再连坐清空实验目录（列表加载已解耦）。

## 存储布局

`<NEXT_TUTOR_DATA_DIR>/chem_lab/<owner>/`：会话文档 + `events/<sid>.jsonl`（追加事件流）+ `checkpoints/<sid>.json`。经 `core/paths.py::bind_storage_path` 绑定、注册进 `core/orphan_cleanup.py` 扫描类目；账号删除经 `core/account_data.purge_account` 清除。内容目录是源码（入库），会话数据永不入库。

## 安全与指导模型

- 虚拟安全边界：危险组合/操作触发 `safety_locked` 相——先阻止操作再解释恢复方式（解锁命令）；被拒绝命令携带 reason 并照常推进 revision/事件流。
- 指导等级 `on_track / try_again / hint / explain / safety / complete`，全部来自 `engine/guidance.py` 的确定性规则；每条提示带 `evidence_event_seq`、`concept_ids`、`model_scope`，页面据此前端显示"根据你的第 N 步操作"。
- 内容包携带 `safety_profile`、`model_scope`（模型适用范围说明），结论边界对学生可见。

## AI 扩展点（默认关闭）

`providers.py` 定义三个 Protocol：`ScenarioProvider`（解析实验包）、`GuidanceProvider`（证据→指导卡）、`ScenarioDraftProvider`（起草新实验包）。默认实现 `CatalogScenarioProvider`、`DeterministicGuidanceProvider` 均无模型调用且已被 service 实际使用；`ScenarioDraftProvider` 无默认实现。任何未来 AI 实现不得写 LabState/执行命令、不得输出可执行代码或 DSL、不得读取其他用户会话或 raw chain-of-thought、不得绕过校验/安全/预览/确认/幂等；AI 草稿必须经 schema 校验 → DSL 静态检查 → 多路径回放 → 双引擎 hash 对齐 → 内容审查 → 人工确认后才能入目录并生成新 pack_hash（已有会话不自动切换）。

## 新增实验

已有能力覆盖的新实验只改内容（复制 `content/experiments/` 模板 → 填写 → `validate_pack.py` → 为新路径补回放向量 → 双引擎回放对齐 → 更新 manifest）。新操作/新规则原语/新现象投影才改引擎：先扩 command/event/render union 与双实现、公开合同、回放向量，再写实验包；禁止在 React 组件中按 experiment_id 特判。
