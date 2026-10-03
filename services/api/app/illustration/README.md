# illustration — 题图 v2 装配管线

题目配图装配：从 `diagrams` 素材库按 `VisualBriefV2` 声明检索候选、受限构图（`SceneDraftV2`）、确定性编译与 Chromium 实测、独立视觉 + 联合题图双审核通过后冻结交付——模型只声明需求与构图，几何与渲染全部服务端完成。

域设计（闭合协议、发布门、任务状态机、配置）见 [docs/architecture/diagrams-illustration.md](../../../../docs/architecture/diagrams-illustration.md)，本 README 只做导航。

## Owns

- 协议契约：`contracts.py` — 闭合 schema 全集（`QuestionMaterialContract` / `VisualBriefV2` / `CandidateBundleV2` / `SceneDraftV2` / `ScenePatchV2` / `DiagramSourceV2`），模型无素材搜索工具、不读库文件。
- 声明与检索：`requirements.py`（需求声明调用）、`retrieval.py`（本地词法检索 + 能力/视图硬约束，每类 ≤6 候选）。
- 构图与编译：`composition.py`（受限构图）、`layout.py`（`compile_scene` 按解析参数生成端口/液面/刻度并实测边界与字体支撑）、`preview.py`（Chromium PNG 渲染与测量）。
- 审核：`review.py`（独立视觉审核 + 联合题图审核）、`validators.py`（静态检查）。
- 任务与存储：`orchestrator.py`（job/run 状态机 `queued → running → ready|not_required|failed`，出题/CAT 补图/重试共用）、`persistence.py`（数据根 `illustrations/<owner>/`，artifacts 不可变）、`events.py`（公开进度投影）。

## Does not own

- 素材几何、目录与 review ledger → `app/diagrams/`。
- 出题侧是否配图的三态策略合成 → `app/core/quiz_illustration_policy.py`；兼容链路补图 → `quiz_illustration_enrichment.py`；SVG 白名单重建 → `quiz_illustration.py`。
- API 路由 → `app/api/v1/illustration_jobs.py`、`assessment_illustration.py`。
- 题目注册与判分（CAT 文字先行协议的受理侧）→ `app/agents/assessment/`。
- Chromium/Playwright 运行环境 → 本地部署依赖（缺浏览器即明确失败）。

## Design

发布门：`machine/visual/joint=passed` 才冻结交付 v2 图（`QUIZ_ILLUSTRATION_VISUAL_REVIEW=active`）；待求读数与答案不进入构图模型的事实投影；历史冻结图不重绘，改题图必须新 revision。无修复成功流程最多消耗声明、构图、视觉、联合审核四次调用。

## Tests

`services/api/tests/`：`test_illustration_v2.py`（合成材料 + fake LLM 经真实 Chromium 的全链回归）、`test_illustration_jobs.py`（真实 JWT/ASGI 路由：所有权、单次运行、重试、不可变产物、删除竞争）、`test_quiz_illustration.py` / `test_quiz_illustration_enrichment.py`（出题侧策略与兼容链路）。聚焦运行：`cd services/api && python -m tests tests.test_illustration_v2`。

## Key entry points

- `orchestrator.py` — 装配状态机入口（出题/CAT/重试共用）
- `layout.py::compile_scene` — 场景确定性编译
- `preview.py` — 真实 PNG 渲染与实测
- `contracts.py` — 全部对外协议契约
