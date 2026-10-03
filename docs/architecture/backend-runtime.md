# backend-runtime — 后端运行时与系统全景

FastAPI 单进程后端的宿主形态：模块地图、物理拓扑、智能层开关、统一存储布局、API 面概览与部署/运维边界。各领域的内部设计见 [identity.md](./identity.md)（M0）、[conversation.md](./conversation.md)（M1 对话内核）。

## Purpose / Scope（职责与边界）

- 系统全景：产品形态（学生学习空间）、M0 + M1-M10 模块地图与领域模块、浏览器→前端→后端→外部模型的物理拓扑。
- 运行时基建：FastAPI 应用工厂与 lifespan、CORS、`X-Process-Time`、原子写与文件锁、限流、trace 落盘、LLM/OCR/向量通道。
- 智能层正交开关总表与降级护栏。
- 统一存储布局总表（`NEXT_TUTOR_DATA_DIR` 单根）与 API 面分组概览。
- 部署三形态与生产基线；GitHub Pages 静态演示仅一句话指引（详见 [../operations/pages-demo.md](../operations/pages-demo.md)），生产部署见 [../operations/deployment.md](../operations/deployment.md)。
- 不负责：各业务域内部契约（见对应模块文档）；测试细节见 [../development/testing.md](../development/testing.md)。

## Owned code（拥有的代码路径）

| 路径 | 职责 |
|------|------|
| `services/api/app/main.py` | 应用工厂、lifespan（管理员引导、图谱 reaper、定时清理）、CORS、`X-Process-Time` 中间件、总路由挂载 |
| `services/api/app/core/config.py` | 全量环境变量/开关集中读取（`settings`） |
| `services/api/app/core/paths.py` | `RuntimePaths` 单一存储根所有权：`NEXT_TUTOR_DATA_DIR` 解析、`bind_storage_path` 绑定与批量重定向（测试沙箱/demo 导出器复用） |
| `services/api/app/core/atomic.py` | JSON 原子写 + 文件锁（所有 JSON 持久化必经） |
| `services/api/app/core/ratelimit.py` | 固定窗口限流（登录 10/min、OCR 20/min 等） |
| `services/api/app/core/trace.py` + `trace_dir_path` | 每轮 trace JSONL 落盘 |
| `services/api/app/core/llm_async.py`、`core/llm_policy.py`、`core/llm_runtime/` | OpenAI 兼容异步客户端（`trust_env=False` 直连）、`disable_thinking` 防饿死、Provider 能力档案与 ReasoningPolicy |
| `services/api/app/core/embedding.py`、`vector_store.py`、`rag_index.py`、`hybrid.py`、`retriever.py` | 向量轨（可选）+ BM25 混合检索（ADR-0003） |
| `services/api/app/core/ocr.py`、`pdf_ocr.py`、`ocr_policy.py`、`textbook_ocr.py` | 视觉模型 OCR（主通道未配置回退本地 tesseract）与逐页择优 |
| `services/api/app/api/v1/router.py` | `/api/v1` 总路由（挂 `require_api_access`）与各域子路由注册 |
| `services/api/app/api/v1/health.py`、`compat.py` | `GET /health`、`GET /model-info`；OpenAI 兼容门面 |
| `start.sh` + `deploy/` | 本地一键启动（端口探测/回退、进程清理、`.env` 覆盖）；nginx/systemd 模板与 voice 安装脚本 |
| `services/voice/` | MeloTTS 语音 sidecar（独立 venv，`deploy/edu-voice-sidecar.service`） |

## Public contracts（对外契约：API 端点/SSE/WS/数据结构）

### 物理拓扑

```
浏览器 ──> Next.js 前端 (:3000，Edu 端口 3001) ──REST/SSE──> FastAPI 后端 (:8000，Edu 端口 8123，/api/v1)
                                                                │
     前端所有 API 调用由 NEXT_PUBLIC_BACKEND_URL 单一决定（同源部署时不设即相对路径 /api/v1）        │
                                                                ├──> LLM（OpenAI 兼容 Chat Completions，必配）
                                                                ├──> 本地自备向量模型 / Embedding API（可选，RAG 向量轨）
                                                                └──> 多模态视觉 API（可选，拍照识题/OCR；缺省回退本地 tesseract）
语音：浏览器 ──WS──> FastAPI /voice/ws ──> MeloTTS sidecar (:8130)
```

SSE 为前端直连后端的流式通道（`POST /chat/stream`、`POST /quiz/grade`）；同源生产经 nginx 反代（需 `proxy_buffering off`）。每个响应带 `X-Process-Time` 头（>1s 终端告警）。

### API 面概览（前缀 `/api/v1`，按域分组）

- 健康/模型：`GET /health`、`GET /model-info`（只返回模型名 + 多模态 bool，绝不返回 key）
- 认证/账户/游客/管理：`/auth/*`、`/user/*`、`/guest/*`、`/admin/*`（详见 [identity.md](./identity.md)）
- 对话/工作区/侧边栏：`/chat/*`、`/workspaces/*`、`GET /sidebar`（详见 [conversation.md](./conversation.md)）
- 测评交互：`POST /quiz/grade`（SSE）、`POST /quiz/record`、`GET /quiz/{hint,recent,submission}`、`POST /quiz/dispute`；`POST /assessment/{start,answer,next,abandon}`、`GET /assessment/{report,active}`（+ grounding/illustration 子面）
- 学生投影与学习评价：`GET /student/{profile,teaching-log,learning-path,error-notebook}`；`/learner-evaluation/*`（workspaces/concepts/sessions/evidence/jobs/reviews）
- 知识：`GET /knowledge/{graph,catalog,custom,taxonomy}`、`GET /knowledge/concepts/{id}`、`DELETE /knowledge/custom/{topic_key}`（手动 build 端点已移除，图谱只来自教材）
- 记忆/评估/UX/编排：`GET /memory/{episodes,semantic,procedural}`；`GET /evaluation/{report,traces,proposals,guidance,context-budget}` 等；`GET /ux/{profile,engagement,motivation,greeting,activity}`；`/orchestration/*`（plan/today/habit/review + goal/week/task 写操作族）
- 资料库/教材库：`/library/*`（folders/upload/move/delete/download）；`/textbooks/*`（upload 带 `level` 五选一 + `scope=private|public`、rebuild_graph、组卷 volumes）
- 回收站：`GET/DELETE /trash`、`GET /trash/{id}`、`POST /trash/{id}/restore`、`GET/PUT /trash/policy`
- 笔记（M-Notes）：`/notes/*`（vault/search/graph/folders/revisions/templates/agent/review/export + SSE generate/chat/stream）
- 课堂：`/classroom/*`（生成管线、revisions、runs、音频、导出）
- 站点助手：`/assistant/*`（catalog/search/preview/actions/handoff/notifications/undo）
- 图示与题图：`/diagram-assets/*`、`/diagram-materials/*`、`/illustration-jobs/*`
- 语音：`GET /voice/status`、`POST /voice/ticket`、`WS /voice/ws?ticket=`
- Trace：`GET /trace/{run_id}`、`GET /trace/{run_id}/html`
- 使用文档：`GET /docs/content?lang=zh|en`（公开读）、`PUT /docs/content`（admin）
- OpenAI 兼容门面：`GET /models`、`POST /chat/completions`（`COMPAT_API_KEY` Bearer/x-api-key；未配置=503，无效=401，限流 30/min；接入方 baseUrl 填 `https://<域名>/api/v1`；帧序 role→delta.reasoning/delta.content→stop→`[DONE]`；`max_tokens<=2` 探测快道不启动 Agent）

## State & storage（状态与存储布局，含 runtime data 路径）

**全部账号/运行数据位于单一数据根 `NEXT_TUTOR_DATA_DIR` 之下**（默认 `.runtime/data`，`core/paths.py` 唯一所有权；ADR-0002），由 `bind_storage_path` 绑定、JSON 持久层遵守 single-worker 不变量（ADR-0004）。标注"非数据根"的两行例外是部署本地资源。

| 路径（数据根相对） | 内容 | 隔离粒度 |
|------|------|---------|
| `chat_history/<id>.session.json` / `.transcript.jsonl` | 会话工作集 / 全量黑匣 | 会话（student_id 戳） |
| `chat_history/library/<sid>.json` + `library/data/<sid>/` | 资料库元数据 + 解析文本 + `.orig` 原件 | 账号 |
| `chat_history/library/<sid>.textbooks.json` | 教材注册记录（状态机/进度/warnings） | 账号 |
| `chat_history/workspaces/`（`ws_*.json` + `uploads/`） | 工作区（public_memory/selected_*） | 账号 |
| `chat_history/trash/items/<owner>/<trash_id>/` | 统一回收站归档包（manifest + payload） | 账号 / 公用 |
| `chat_history/classroom/<owner>/...` | 课堂私有运行数据（revisions/jobs/assets/runs/audio/exports） | 账号 |
| `chat_history/assistant/` | 站点助手运行数据 | 账号 / 全局 |
| `chat_history/settings/` | `guest_policy.json`、`ocr_policy.json`、`usage_docs.json` 等策略文件 | 全局 |
| `users/accounts.json`、`users/avatars/<sid>/` | 账户（bcrypt hash）与私有头像 | 全局 / 账号 |
| `students/<sid>.json` 等 `students/<sid>.*` | M2 画像/证据账本、M3 教学日志、M6 记忆、M7 评估、M8 UX、M9 编排 | 账号 |
| `knowledge/graph.json` | M5 图谱增量（reasoner 边） | 全局 |
| `knowledge/custom/<sid>/<topic>.json` / `.chunks.json` | 活动教材图谱 + 概念预索引（`sid=public` 为公用教材） | 账号 / 公用 |
| `knowledge/vector_db/` | Chroma 向量索引 | 全局 |
| `notes/<sid>/` | M-Notes 笔记仓库（vault/正文/修订/agent/附件） | 账号 |
| `uploads/` | 会话上传解析文本 + 原件 | 会话 |
| `illustrations/<sid>/` | 题图任务、运行事件、冻结 artifact 与 PNG | 账号 |
| `diagram_assets/` | 图示库资产 | 账号 / 公用 |
| `artifacts/`（含 `public_vectors/`） | 公共向量 artifact 等 | 全局 |
| `traces/` | 每轮 trace（JSONL） | 全局 |
| `.runtime/auth_jwt_secret`（数据根旁） | 本机生成的 JWT 开发密钥（0600） | 实例 |
| `services/voice/models/`、`services/voice/vendor/`、`services/voice/.venv/` | MeloTTS 模型缓存与 sidecar venv（**非数据根**，gitignored 本地资源，不入 orphan 扫描） | 全局（本地） |

以上全部被 `.gitignore` 覆盖；仓库不携带任何教材/派生数据资产（ADR-0001），公共教材库（`public` 命名空间）是部署本地运行时数据，默认为空。

## Main flows（关键流程）

- **请求生命周期**：浏览器 → Next.js（`apiFetch`）→ nginx（同源）→ FastAPI：`require_api_access` 鉴权 → 域路由 → `resolve_student_id()` 命名空间 → 业务存储（原子写）→ 响应（`X-Process-Time`）。
- **一轮对话**（M1 主流程见 [conversation.md](./conversation.md)）：Supervisor 八步管线逐事件 SSE，trace 同轮落 `traces/`。
- **启动（`./start.sh`）**：探测后端/前端实际端口并同步 `NEXT_PUBLIC_BACKEND_URL` 与本地 `CORS_ORIGINS`；默认完整运行时（Supervisor v2、Skill gated、Tool Message native、前端 prod 模式）；Edu 端口 8123/3001 + pnpm；清除代理环境变量；非交互自动进入后台会话；stop/退出经 `deploy/process_cleanup.py` 按 PID+启动时间校验清理。
- **智能层降级**：任一层开关关闭 → 上层自动降级、下层行为不受影响；每层读写钩子 try/except 包裹，失败只记 trace。

### 模块地图（M0 + M1-M10 + 领域模块）

| 层 | 名称 | 一句话职责 | 包路径 |
|----|------|-----------|--------|
| M0 | 身份基础设施 | 用户是谁、数据属于谁、如何安全访问 | `app/identity/` + `api/v1/auth.py` `user.py` `guest.py` |
| M1 | 任务智能（Supervisor） | 这一轮对话怎么完成：理解→规划→工具执行→状态更新 | `app/agents/supervisor.py` 等编排模块 |
| M2 | 学生模型 | 这个学生会什么：画像 + 统一学习证据账本 | `app/agents/student_model/` |
| M3 | 教学引擎 | 现在该怎么教：六模式状态机 + 跨轮教学记忆 | `app/agents/teaching_engine/` |
| M4 | 测评智能 | 真的学会了吗：三级评分 + 约束出题 + CAT | `app/agents/assessment/` |
| M5 | 知识智能 | 系统知道哪些知识：教材图谱（公用+自有）+ 概念检索 | `app/agents/knowledge/` |
| M6 | 记忆智能 | 有界 prompt 画像 + 策略聚合；旧情景/语义只读兼容 | `app/agents/memory/` |
| M7 | 评估改进智能 | 教师是否越来越好：TurnTrace 诊断 + 改进建议（人工确认） | `app/agents/evaluation/` |
| M8 | 交互体验智能 | 怎么表达最适合：UX 画像 + 输出适配（不改内容） | `app/agents/ux_intelligence/` |
| M9 | 学习编排智能 | 未来几周怎么学：多目标→周任务→今日任务 + SM-2 | `app/agents/learning_orchestration/` |
| M10 | 能力运行时与证据门 | Agent 能调用什么、契约是否满足、证据能否写回 | `app/agents/skill_runtime/` |
| 领域 | 课堂模式 | 私有课件生成/试听/运行/音频/导出 | `api/v1/classroom.py` + `core/classroom_store.py` |
| 领域 | 站点助手 | 站内导航/检索/代办动作/撤销/通知 | `app/agents/site_assistant/` + `api/v1/assistant.py` |
| 领域 | 笔记（M-Notes） | 笔记仓库 + 每笔记专属智能体 | `api/v1/notes.py` + `agents/notes_agent.py` + `core/notes.py` |
| 领域 | 图示库 | 配图资产与教材图示材料 | `api/v1/diagram_library.py` `diagram_materials.py`（`diagram_assets/` 根） |
| 领域 | 题图 | 题目插图任务管线与冻结 artifact | `api/v1/illustration_jobs.py` `assessment_illustration.py` + `core/quiz_illustration*.py` |
| 领域 | 语音通话 | push-to-talk 电话模式 + 板书同步 | `api/v1/voice.py` + `services/voice/` sidecar |
| 领域 | 资料库/教材库 | 私有资料 + 教材注册与图谱构建 | `api/v1/library.py` `textbook.py` + `core/library.py` `textbook*.py` |

层次关系：M5 是横向输入基础设施（提供知识）；M8 是横向输出适配层；M9 纵向编排 M1-M4；M10 横向能力控制层；M0 是所有 Agent 的身份入口，不属于 M1-M10。

## Dependencies（依赖与被依赖）

- 外部：LLM（必配，OpenAI 兼容）、Embedding/本地向量模型（可选）、多模态视觉 API（可选，回退 tesseract）、Chroma（可选向量轨；BM25 基线零依赖，ADR-0003）。
- 被依赖：前端（唯一后端）；OpenAI 兼容门面供第三方平台挂载。
- 进程形态：FastAPI 单 worker（JSON 持久层前提，ADR-0004）；语音 sidecar 独立进程由 start.sh 托管；教材图谱构建为进程内 asyncio 后台任务，启动 lifespan reaper 将残留 `building` 置 `graph_failed`。
- 改 agent 管线代码后必须重启 uvicorn（无热重载假设）。

## Invariants / security boundaries（不变量与安全边界）

- 原子写 + 文件锁：所有 JSON 持久化 tmp+replace；腐坏文件按空处理不崩。
- 路径防护：`_resolve` 统一剥目录防遍历（session/library/memory/knowledge 同规则）。
- 鉴权矩阵 fail-closed；按 id 资源端点对外人 404 不泄露存在性；JWT 唯一事实源见 [identity.md](./identity.md)。
- `CORS_ORIGINS` 白名单（生产禁 `*`）；主 LLM/Embedding/视觉的 httpx client 固定 `trust_env=False`，部署不走本机代理。
- 密钥与卫生：`.env`/`*.key`/`users/`/`students/`/私有 `chat_history/`/向量库/上传目录均 gitignored；公共教材库是部署本地数据，不随版本发布；`GET /model-info` 绝不返回 key。
- LLM 结构化小调用一律 `disable_thinking=True`（provider 拒绝可选字段时去参重试），防 reasoning 吃光预算导致 JSON 抽取静默全灭。
- 统一护栏原则：智能层读写钩子任何失败只记 trace，绝不影响对话流。

### 智能层正交开关总表

每层一个环境变量，默认全开（除注明），关闭任一层则上层自动降级、下层不受影响：

| 开关 | 默认 | 关闭/切换后行为 |
|------|------|----------------|
| `AUTH_MODE` | `0` | 部署安全守卫；生产设 `1`（默认 JWT secret 拒启）。游客访问由管理员策略独立控制 |
| `SUPERVISOR_MODE` | `v2` | `legacy` 走 V1 路径；v2 异常显式报错，仅 `SUPERVISOR_LEGACY_FALLBACK=1` 回退 |
| `STUDENT_MODEL_MODE` | `1` | `0` 关 M2：无画像/评价/策略注入（评价层显式 disabled，不假装已评价） |
| `TEACHING_ENGINE_MODE` | `1` | `0` 退回 M2 内置轻量 adapt 路径 |
| `ASSESSMENT_ENGINE_MODE` | `1` | `0` 关 M4：评分退二元、无 CAT |
| `KNOWLEDGE_INTELLIGENCE_MODE` | `1` | `0` 关 M5：SkillGraph 用自有种子，无知识指令 |
| `MEMORY_INTELLIGENCE_MODE` | `1` | `0` 关 M6：精简画像指令与写侧聚合 no-op |
| `EVALUATION_INTELLIGENCE_MODE` | `1` | `0` 关 M7：评估指令返回空、捕获 no-op |
| `UX_INTELLIGENCE_MODE` | `1` | `0` 关 M8：交互指令返回空、记录 no-op |
| `ORCHESTRATION_MODE` | `1` | `0` 关 M9：编排指令返回空、记录 no-op |
| `SKILL_RUNTIME_MODE` | 裸 uvicorn=`shadow`；start.sh=`gated` | `shadow` 只记录；`gated` 强制前置条件/澄清门/逐步工具暴露；`off` 关旁路诊断与 Skill Card |
| `STRUCTURED_ASSESSMENT_MODE` | `off` | `shadow` 旁路量规分析落盘对照；`active` 冻结量规开放题以结构化分析为权威判定 |
| `TEACHING_DECISION_MODE` | `rules` | `shadow` 旁路调用记 trace 对照；`active` 校验通过的 LLM 教学决策受限调整策略 |

排障可用各开关二分定位故障层。

## Configuration（环境变量与开关）

- 数据与端口：`NEXT_TUTOR_DATA_DIR`（存储单根）、`API_HOST`/`API_PORT`（默认 127.0.0.1:8000）、前端 `NEXT_PUBLIC_BACKEND_URL`（部署形态单一真相源）。
- LLM：`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`/`LLM_PROVIDER`/`LLM_TEMPERATURE` 及 [conversation.md](./conversation.md) 所列预算变量。
- 向量轨：`EMBEDDING_PROVIDER`（默认 off）、`EMBEDDING_BASE_URL`/`EMBEDDING_API_KEY`/`EMBEDDING_MODEL`/`EMBEDDING_MODEL_PATH`、`CHROMA_DIR`、`RAG_HYBRID`。
- OCR：`PDF_OCR_MODE`（auto/on/off，逐页稀疏判定）、`PDF_OCR_MAX_PAGES=1024`、`PDF_OCR_SYNC_MAX_PAGES=20`、`PDF_OCR_DPI=200`、`PDF_OCR_CONCURRENCY=20`。
- 教材管线：`TEXTBOOK_GRAPH_ENABLED`（默认 1）、`TEXTBOOK_GRAPH_MAX_CHAPTERS=30`、`TEXTBOOK_GRAPH_MAX_CONCEPTS=400`、`TEXTBOOK_PARSE_MODE`/`TEXTBOOK_BUILD_CONCURRENCY` 等。
- 门面与语音：`COMPAT_API_KEY`（未配置=门面 503 关闭）、`COMPAT_GRADE`；`VOICE_TTS_PROVIDER`（默认 off）、`VOICE_TTS_BASE_URL`（127.0.0.1:8130）。
- 鉴权/管理员：见 [identity.md](./identity.md)。
- 生产清单：`AUTH_MODE=1` + 强 `AUTH_JWT_SECRET` + `CORS_ORIGINS` 白名单 + `chmod 600 .env`；Python 依赖用 `services/api/requirements.txt` + `constraints.txt` 约束。

### 部署三形态（`NEXT_PUBLIC_BACKEND_URL` 单一真相源）

1. 开发/跨域生产：显式设置完整后端 URL → 客户端直连（CORS 放行）。
2. 同源生产：不设 → 相对路径 `/api/v1`，nginx 反代（SSE `proxy_buffering off`）；模板在 `deploy/`，systemd 专用用户 `edu-agent`（`NoNewPrivileges`/`ProtectSystem=strict` 等，`ReadWritePaths` 只放行存储根）。
3. 本地一键：`./start.sh` 自动探测端口并同步变量。

GitHub Pages 静态演示（`NEXT_PUBLIC_DEMO_MODE=1` 只读导出形态）见 [../operations/pages-demo.md](../operations/pages-demo.md)；同机生产部署手册见 [../operations/deployment.md](../operations/deployment.md)。

## Observability（trace/日志/指标）

- 每轮 trace 落数据根 `traces/`：决策链（understanding/TaskFrame/Skill 候选与拒绝/plan/tool 调用/后置条件）+ prompt/skill 版本 + token 用量；`GET /trace/{run_id}`（JSON）与 `GET /trace/{run_id}/html`（可折叠视图）。
- `X-Process-Time` 全响应头 + >1s 终端告警。
- 上下文/用量遥测：`GET /evaluation/context-budget`（统计聚合，无 Prompt/正文/隐藏 reasoning）。
- 结构化小调用与护栏事件（`provider_capability_fallback`、`incomplete_answer_recovery` 等）均记 trace。

## Tests / acceptance（测试索引）

后端 `services/api/tests/`（unittest，扁平目录），运行时/基建相关：

- `test_atomic_hardening.py`、`test_json_utils.py`（原子写与 JSON 工具）
- `test_cors_network.py`（CORS 与直连网络）
- `test_deployment_contract.py`、`test_requirements_contract.py`（部署/依赖契约）
- `test_bootstrap_readiness.py`（启动引导）
- `test_compat_api.py`（OpenAI 兼容门面）
- `test_docs.py`（使用文档）、`test_allowlist_sanitize.py`
- 其余各域测试索引见 [identity.md](./identity.md)、[conversation.md](./conversation.md) 及 [../development/testing.md](../development/testing.md)（环境搭建、浏览器 smoke/full 回归与 CI 所有权）。

## Related ADRs

- ADR-0001 source-only 仓库（不携带教材/派生数据资产）
- ADR-0002 运行数据统一 `NEXT_TUTOR_DATA_DIR`
- ADR-0003 BM25 基线 + 向量可选
- ADR-0004 JSON 持久层 single-worker 不变量
- ADR-0005 Pages demo 仅 synthetic fixtures
