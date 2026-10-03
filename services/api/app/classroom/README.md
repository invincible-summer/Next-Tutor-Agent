# classroom — 课堂模式后端领域包

一键备课 → 九阶段后台管线产出受约束 `LessonSpec` → 编译为安全 HTML 课件 + 逐页讲稿 → 语音讲授/插问/随堂题 → 断点恢复与导出：本包承载该流程的全部服务端编排。

完整设计（管线、预算、发布事务、渲染规范、播放协议、配置表）见 [docs/architecture/classroom.md](../../../../docs/architecture/classroom.md)，本 README 只做导航。

## Owns

- 生成管线：`pipeline.py` + `worker.py`（九阶段、检查点恢复、cancel/epoch）、`llm_budget.py` / `llm_io.py`（token/deadline 预算账本）、`generation_normalize.py`、`idempotency.py`。
- 来源与外部素材：`sources.py`（教材/附件来源冻结）、`research/`（Tavily 检索唯一适配器）、`media/`（Pexels/Pixabay 候选、SSRF 校验下载、重编码署名）。
- 渲染：`render/`（v1/v2 编译器、主题 token、block 判别、KaTeX 公式、Node+Playwright Chromium 排版检查子进程）；`static/generated/` 为部署期构建的 frame runtime（gitignored）。
- 版本与编辑：`revisions.py` / `revisions_ops.py`（版本快照、快速修订）、`block_edit.py`（组件级零 LLM 替换 / 单组件重写）。
- 播放与讲授：`runs.py`（run 状态机、lease、progress）、`checkpoints.py`（reflect/question）、`assessment_bridge.py`（冻结题受理）、`chat_context.py`（插问上下文桥接）、`audio.py`（段级 TTS 合成缓存）。
- 生命周期与运维：`exports.py`（HTML ZIP / Markdown 讲稿 / 打印页）、`lifecycle.py`（归档/恢复/purge 级联）、`health.py`（管理端巡检）。
- 能力面：`service.py`（领域服务入口）、`capabilities.py`、`templates.py`、`validation.py`、`limits.py`、`errors.py`、`netcheck.py`。

## Does not own

- 存储落盘细节（目录布局、发布事务）→ `app/core/classroom_store.py`（本域唯一存储层）。
- API 路由与 schema → `app/api/v1/classroom.py`、`app/schemas/classroom.py`；提示词 → `app/prompts/classroom.py`。
- 插问的对话语义 → 聊天内核 `run_turn`（`app/agents/`）。
- TTS provider 实现与并发 → `app/voice/tts/`；MeloTTS sidecar → `services/voice/`。
- 教材解析与知识图谱 → `app/core/textbook*.py`、`app/agents/knowledge/`。
- 前端（课程中心、编辑器、播放器）→ `apps/web/src/` 的 `app/(workspace)/course/`、`components/classroom/`、`lib/classroom/`。

内部子包 `render/`、`media/`、`research/`、`static/` 不单独设 README，职责见上。

## Tests

`services/api/tests/classroom/` 下 `test_classroom_*.py` 共 31 件（api / pipeline / worker / jobs / runs / revisions / block_edit / render / render_layout / composition / audio / exports / checkpoints / lifecycle / health / images / research / sources / storage / prompts / …）；fake LLM 与固定件由 `tests/support/classroom_fake_llm.py` 等提供。真实模型手工验收脚本为 `scripts/acceptance/classroom/live.py`。

## Key entry points

- `service.py` — 领域服务入口（课程/版本/run 编排）
- `pipeline.py` / `worker.py` — 生成管线与进程内 job worker
- `runs.py` — 播放 run 状态机与 lease
- `revisions.py` / `block_edit.py` — 版本快照与组件级编辑
- `audio.py` — 段级音频供给与缓存
