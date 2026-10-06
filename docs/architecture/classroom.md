# classroom — 课堂模式（一键备课 → HTML 课件 → AI 讲授）

课堂模式把「选主题/教材章节 → 一次确认后台生成整节课程 → 受约束 LessonSpec 编译为安全 HTML 课件 + 逐页讲稿 → 语音讲授 → 插问/随堂题 → 断点恢复与课后导出」做成与聊天并列的一级教学模块（前端「备课上课」）。

## Purpose / Scope（职责与边界）

- 备课：三步备课弹窗（内容来源 → 课件设计 → 授课设置），从工作学习区已授权教材/会话附件或纯主题生成课程；主题必填 2–120 字，来源 8 文件 / 12 章上限，5 套教学模板 + 5 套视觉模板，时长/页数/学段/语言/联网时效/图片密度/检查点密度/语音设置可选。
- 生成：九阶段后台管线产出 `LessonSpec`（受约束 JSON，绝非模型 HTML），由本项目编译器渲染为课件；预算、检查点化恢复与幂等发布均由服务端保证。
- 讲授：播放器按讲稿音频逐段推进，支持暂停/断点恢复、跨标签页租约接管、插问、检查点（reflect/question）、字幕、倍速、阅读模式与全屏。
- 编辑与导出：课程详情/编辑器（组件级零 LLM 替换与单组件重写、快速修订）、HTML ZIP / Markdown 讲稿 / 授权打印页（浏览器另存 PDF）。
- 生命周期：单课与工作区归档/恢复、账号注销级联、孤儿课堂清理、管理端健康巡检。
- 不负责：插问的对话语义复用聊天内核（见 [conversation.md](./conversation.md)）；TTS provider 实现与 MeloTTS sidecar 归 [voice.md](./voice.md)；教材解析与知识图谱归资料库/知识层。

## Owned code（拥有的代码路径）

后端 `services/api/app/`：

| 路径 | 职责 |
|------|------|
| `classroom/service.py` | 领域服务入口（课程/版本/run 编排） |
| `classroom/pipeline.py` + `worker.py` | 九阶段生成管线与 job worker（检查点恢复、cancel/epoch、调度并发）；文件模式由 API lifespan 持有，durable 模式（ADR-0013）随监督 workflow 移到 worker 进程 |
| `classroom/llm_budget.py` / `llm_io.py` | 每课调用/token/deadline 预算账本与 LLM I/O（预留-结算、失败也记账） |
| `classroom/sources.py` | 来源冻结（教材卷/章/页码 + source hash）、严格教材模式、会话附件勾选 |
| `classroom/research/`（`tavily.py`） | 联网检索/提取唯一适配器（Tavily），`as_of` 时效元数据 |
| `classroom/media/`（`pexels.py`、`pixabay.py`、`download.py`、`service.py`） | 图片候选/缓存/限流、SSRF 校验下载、重编码与署名 |
| `classroom/render/`（`compiler.py`、`compiler_v2.py`、`check.py`、`themes.py`、`blocks.py`、`inline_math.py`、`assets.py`） | 课件编译器（v1/v2）、排版检查子进程、主题 token、block 判别、非代码公式识别、静态资产 |
| `classroom/runs.py` | 播放 run 状态机、lease、progress、音频段记账 |
| `classroom/revisions.py` / `revisions_ops.py` / `block_edit.py` | 版本快照/快速修订、组件级编辑（`replace_block` / `regenerate_block`） |
| `classroom/checkpoints.py` / `assessment_bridge.py` / `chat_context.py` | 检查点（reflect/question）、冻结题受理、插问上下文桥接 |
| `classroom/audio.py` | 本地优先 TTS 合成缓存与段级供给（provider 见 `voice/tts/`） |
| `classroom/exports.py` | HTML ZIP / Markdown 讲稿 / 打印页导出 |
| `classroom/lifecycle.py` | 单课/工作区归档、恢复、purge 级联 |
| `classroom/health.py` | 管理端健康巡检与恢复动作 |
| `classroom/capabilities.py` / `templates.py` / `validation.py` / `generation_normalize.py` / `idempotency.py` / `limits.py` / `errors.py` / `netcheck.py` | 能力面、模板目录、结构校验、生成结果无损规范化、幂等键、统一限制常量、错误映射、外部网络探测 |
| `classroom/static/generated/` | 课件 frame runtime 静态包（`pnpm run build:classroom` 部署期构建，gitignored） |
| `classroom/storage.py` | 唯一存储层（目录布局、发布事务、tombstone、缓存清扫） |
| `schemas/classroom.py` | API/schema 契约（Pydantic） |
| `api/v1/classroom.py` | `/api/v1` 课堂路由（45 个端点） |
| `prompts/classroom.py` | `classroom_outline` / `classroom_slide` / `classroom_json_continue` / `classroom_block` / `classroom_review` / `classroom_repair` 提示词（经 `prompts/registry.py` 注册，版本随 Job 冻结） |

前端 `apps/web/src/`：`app/(workspace)/course/`（课程中心）、`app/(workspace)/workspaces/[workspaceId]/classroom/`（列表/详情/`learn/[runId]` 播放器）、`components/classroom/`（`CreateLessonModal`、`LessonEditor`、`LessonOverview`、`SlideFrame`、`BlockEditor`、`player/` 下 `ScriptPanel`/`VoicePanel`/`CheckpointPanel`/`QuestionDrawer` 等）、`lib/classroom/`（`useClassroomPlayer`、`player-reducer`、`audio-controller`、`audio-focus`、`frame-runtime`、`useClassroomQA`、`useVoicePreview`）。

辅助脚本：`apps/web/scripts/build-classroom-assets.mjs`（静态包构建）、`check-classroom-render.mjs`、`inspect-classroom-course.mjs`；离线浏览器回归 `apps/web/tests/unit/test-classroom-*.mjs`；真实模型手工验收 `scripts/acceptance/classroom/live.py`。

## Public contracts（对外契约：API 端点/SSE/WS/数据结构）

前缀 `/api/v1`，统一 envelope；全部需登录（游客默认不可用）：

- 能力与模板：`GET /classroom/capabilities`（含 renderer/research/images/tts/voice 如实状态与用户配额）、`GET /classroom/templates`。
- 课程 CRUD：`GET/POST /workspaces/{ws}/classroom/lessons`（分页、三态筛选）、`GET/DELETE …/lessons/{les}`、`PATCH`（brief/outline）。
- 生成任务：`POST …/lessons/{les}/jobs` 及 `GET …/jobs/{id}`，附带 `cancel` / `retry` / `continue` / `outline PATCH` / `brief PATCH` / `preview` / `events`（SSE，认证 fetch 读流、心跳 15s；前端 10s 轮询 fallback）。
- 图片与音色试听：`POST …/classroom/image-search`、`POST …/classroom/voice-preview`、`GET …/classroom/voice-previews`。
- 版本与课件：`GET …/lessons/{les}/revisions/{n}/frame`（`mode=print` 提供授权打印页）、`POST …/revisions`（快速修订）、`edit_content`（`replace_block` / `regenerate_block`）。
- 导出：`POST …/exports` + `GET …/exports/{id}` / `content`（24h 过期）。
- 播放：`POST/GET …/runs`、`GET/PUT/DELETE …/runs/{id}/lease`、`PUT …/progress`、`GET/PUT …/audio-profile`（CAS）、`GET/POST …/audio`（段请求/状态/`{clip_id}/content` 认证内容端点）、`qa-session` / `qa-audio`（插问会话与插问语音）、`notes` / `save-note`（幂等桥接笔记中心）、`checkpoints/{cid}` 下 `hint` / `reveal` / `skip` / `submit` / `submission`、`GET …/runs/{id}/summary`（只汇总可观察事实）。
- 错误码固定映射：403 `classroom_disabled` / 404 `source_not_found` / 409 revision·lease·scope·idempotency 冲突 / 410 `export_expired` / 429 quota·`audio_busy` / 503 storage·tts·renderer·research 不可用 / 500 `damaged`；`Idempotency-Key` 为 16–128 位可打印字符，body hash 冲突返回 `idempotency_conflict`。
- 课件嵌入协议：`SlideFrame` 以 iframe `sandbox="allow-scripts"` + `srcdoc`（URL 不带 token）加载编译 HTML，握手 `classroom_ready{nonce}`（校验 `event.source`）后建立 MessagePort 通道。

## State & storage（状态与存储布局，含 runtime data 路径）

唯一根 `chat_history/classroom/`（单一数据根下，ADR-0002），按 owner 分目录：

| 路径 | 内容 |
|------|------|
| `<owner>/owner.json` | owner 级元数据 |
| `<owner>/image-search-cache/` | 图片候选缓存（24h） |
| `<owner>/voice-previews/` | 音色试听缓存 |
| `<owner>/workspaces/<ws>/index.json`、`operations/` | 学习区课程索引与 durable 操作日志 |
| `…/lessons/<les>/lesson.json` | 课程记录 |
| `…/revisions/<n>/` | manifest + `spec.private.json` + assets（正式题答案私有存储） |
| `…/jobs/<id>/{job.json,staging/}` | 生成任务与检查点产物 |
| `…/runs/<id>.json` | 播放 run（状态、lease、进度、音频记账） |
| `…/audio/`、`…/exports/` | 段级音频缓存（wav+meta 成对）与导出产物 |

- 权限与健壮性：目录 0700 / 文件 0600；读路径不 mkdir；symlink 逃逸拒绝；损坏 JSON → `LessonDamagedError`（标记 damaged 隔离，不返回空课冒充正常）；任何写路径 OSError → `ClassroomStorageError`（全局 handler 返回 `storage_unavailable` envelope）。
- 发布事务：staging → 逐文件 hash 校验 → manifest 最后写 → 同文件系统 rename → 课程锁内指针提交（commit intent 带 expected epoch）；崩溃后 `recover_pending_publish` 校验 manifest hash 再补指针。失败 revision 留空号永不复用；上限 20 版（`CLASSROOM_MAX_REVISIONS`）。
- revision 分配在同一次 lesson 锁内 mutation 中检查 base revision/版本上限、读取 next_revision 并递增；API 与 durable worker 共享文件锁时也不会分配重复版本。生成失败留下的空号保持不复用。这个局部并发约束不代表全部业务存储已完成数据库多实例切换。
- owner 级 tombstone（`.tombstones/`）在 purge 后拦截一切晚到写入。
- 缓存治理：音频 owner 500MB / 7 天 LRU；导出 24h TTL。
- **双模存储（ADR-0017）**：`classroom=sql`（`DOMAIN_DOCUMENT_BACKENDS`，需 `DATABASE_URL`）时 owner 记录与 lesson/job/run 事实、学习区索引写入 `classroom_documents` 表（kind `owner`/`lesson`/`job`/`run`/`index`）；CAS 更新在行锁 mutate 内完成（等价 file_lock）。tombstone 文件双模保留为持久围栏（9 域 `purge_owner` 循环会删行，根外文件不受影响）；配额/幂等记账统一走 `storage.mutate_owner_record`；归档快照在 SQL 模式把事实导出为同构 JSON 进 trash bundle、恢复时回灌。修订目录、assets、音频与导出字节、图片/试听缓存留文件/对象存储态；六步发布事务继续按目录执行、只经由双路由的 lesson/job 原语。健康扫描（`health.scan_alerts`）按双模 owner 口径收集 job 事实。历史数据用 `scripts/migrations/runtime_to_enterprise/import_documents.py --domain classroom` 迁移并 `--verify`。

## Main flows（关键流程）

### 备课生成管线（`pipeline.py` + `worker.py`）

1. 九阶段：`resolve_sources → research → outline → visual_assets → author_slides → checkpoints → review → render → publish`。每阶段产物写 staging，重启后从最近检查点续跑（自动恢复上限 3 次）；cancel/epoch 语义防止晚到发布。
2. 预算（`limits.py` / `llm_budget.py`）：每课输出 40,000 / 输入 180,000 token，LLM 逻辑调用上限 `min(40, 2N+8)`；结构化阶段单次输出上限——检索计划 1,200、大纲 4,800、复核 1,800、JSON 续写 2,400 token；写作页按剩余页数均分额度（单次 ≤6,000，预留一页修复空间）。请求发出前预留并持久化预算，返回后按 usage 结算；429 等被拒请求只计调用次数不扣输出 token，超时/断连按预留保守记账；恢复时完整累计，失败/取消也记账。job deadline 取 `CLASSROOM_JOB_TIMEOUT_SECONDS`（默认 900s）。
3. 频率与排队：单用户新建/重生成 3 次/10 分钟、20 次/天；每 owner 排队 3、全局 20；job 并发 2（owner 1）、LLM 并发 3。
4. 容错与缓存：已生成页写 `author_slides_partial`（带输入/产物 hash），随堂题独立缓存，中断后复用匹配结果；写作 JSON 结构错误最多一次修复；单页 JSON 未闭合时用 `classroom_json_continue` 在原始证据上下文后续写缺失尾部（每页最多一次），拼接结果仍过 schema 与来源校验，失败不发布半页；模型暂时不可用时在既有阶段产物基础上有界自动续跑三轮，配置错误直接失败；`finish_reason=length` 空正文明确报输出耗尽，不自动重发。
5. 内容复核（`LessonBrief.content_review_enabled`，默认 false）：开启时单次调用 reviewer，JSON 不额外修复，失败或预算不足记提示后继续发布；模型自报 blocker 只作为 major 建议，风格/时长不阻断生成。结构校验、来源授权、HTML escaping/CSP 与正式题答案私有存储始终保留。
6. 手动重试（`POST …/jobs/{id}/retry`）：保留已校验阶段产物与目标 revision，`attempts` 加一，当前 `JobBudget` 合入 `prior_attempts_budget` 后账本置零开新窗口；自动恢复与 `continue` 不重置额度。重试可行性按持久化账本与未完成页数判断，额度不足不再入队空转；修订任务只重写一页时按一页估算。
7. 执行位置（ADR-0013 双模式）：文件模式下 `ClassroomWorker` 由 API lifespan 启动，`service.enqueue_job` 钩子直连 `worker.enqueue`；配置 `TEMPORAL_ADDRESS` 后 API 只确保 `classroom.supervisor` 监督 workflow 存在（`app/workflows/classroom.py`，classroom 队列），worker 进程内的时间片 activity 启动同一 `ClassroomWorker` 并启用 adopt 模式——调度循环每 2s 扫描磁盘 queued job 补登记（事实源是 job.json），API 的 enqueue 钩子保持 None，lesson 创建/retry/continue 的 CAS 落盘后 ≤2s 被收养。检查点/预算/epoch/publish validation、`job_events` SSE 与 job 轮询 DTO 在两种模式下完全一致（ADR-0013 §13.4：Temporal 只保证「worker 进程在，调度就在」，不接管业务状态）。

### 来源、检索与图片

- 来源冻结：教材组卷顺序/章节/页码 + source hash；严格教材模式不借网络补证据；显式会话附件须本人勾选。
- 联网检索：Tavily search/extract 是唯一首发适配器（无 key 即 research 不可用）；时效元数据 `as_of`；prompt injection 不能改变工具 scope。
- 图片：Pexels/Pixabay 候选（24h 缓存、限流头、`candidate_id` 服务端签发防伪）；下载经 SSRF/IP 每跳重验、Pillow 重编码、EXIF 清理、asset hash，署名（creator/license/url）入 credits。配图上限由图片密度决定（balanced 最多约 1/3 页、rich 最多 1/2 页，至少允许一页），每页最多一张插图且须有同页解释。

### 渲染与排版规范（`render/`）

- **版本合同**：新课程固定 `GenerationJob.renderer_version=2.0.0` 与视觉主题 `@2`；历史 Job/Revision/Run 维持 `1.0.0`/`@1`，不批量迁移；修订作业继承基版 renderer，播放器始终按 `run.lesson_revision` 取固定课件。`compiler.py` 承载共用的 block escaping、CSP 与 iframe 协议；`compiler_v2.py` 只为 2.x 注入布局与主题样式。
- **画布与页型**：v2 每个 `SlideSpec` 恰好是一张 1280×720 画布（9 种教学页型：title、key_points、image_explain、compare、derivation、worked_example、timeline、checkpoint、summary），frame runtime 不拆分文本、不建隐藏子页、不做几何缩放；翻页、讲稿定位、打印共用同一逻辑页。无阅读模式子页；窄桌面窗口等比缩放整张画布。
- **构图**：`SlideSpec.composition` 为受控设计意图（`auto/stack/columns/sidebar/editorial` × 密度 `balanced/compact/airy` × 表面 `plain/soft/outlined` + `focal_block_id`/`emphasis_block_id`/通栏组件）；模型漏填按 auto 补齐，schema 拒绝悬空 ID，模型不能生成 HTML/CSS/JS。排版在字体/图片就绪后实测：稀疏适度放大字号间距，拥挤先收紧间距再尝试双栏/侧栏/完整组件双栏；sidebar 按主角与解释分别测量排列，不留等高网格空洞；主段落字号 32px，compact 档 27px、dense 档 22px；显示公式最小 20px，否则换更宽构图并诊断。
- **Block 判别**：paragraph / bullets / formula（离线 KaTeX，trust=false、有界展开）/ code（源码纯文本转义、保留缩进换行、长行软换行）/ image / diagram / checkpoint 等；所有非代码文本字段共享 `render_text`，支持显式数学定界符并识别常见裸写 TeX 命令（`\delta`、`\frac{a}{b}` 等），文件路径/未知命令不猜测修补；文字与公式交替最多 32 个 span。
- **图示**：流程图节点/边标签 ≤40 字符，超长组件经 `normalize_authored_slide` 无损转编号正文（保留全部节点/边文字与分支回环，组件 ID 不变，不追加模型调用）；简单 SVG 流程按节点与层数计算画布，节点标签随缩放重排且画布字号 ≥16px；SVG 数学标签经受控 foreignObject 接同一 KaTeX。
- **溢出策略**：排版检查（Node+Playwright 受控子进程）检查水平/纵向溢出、组件内溢出与相邻重叠，并识别 runtime 失败标记；新任务实测内容溢出时 render 阶段报告问题页并停止发布；历史超量页保留完整 DOM、页内滚动兜底并由检查器如实报告；打印前检查拒绝裁切导出并提示页码。
- **导出/打印**：每张合格 SlideSpec 打印为一张 1280×720 物理页（等尺寸分页，PDF 可检索全文）；在线打印等待字体/图片/打印布局就绪；HTML ZIP 与 Markdown 讲稿导出沿用，全部取当前选中 revision。
- **提示词冻结**：新任务冻结 `classroom_slide@2.6.0` / `classroom_outline@2.4.0`（大纲 JSON 契约含 `key_points` 分工，缺失在写作前拒绝）；`classroom_block@1.0.0`（组件重写）、`classroom_json_continue@1.0.0`；已排队任务按冻结版本继续执行。

### 讲授播放（`runs.py` + 前端 `useClassroomPlayer`）

- run 状态 `active/paused/completed/ended`；`state_revision` CAS 乐观锁（冲突 409 envelope），音频记账写不推 revision。
- lease：`client_id`（每标签页 sessionStorage 随机 ID）+ epoch，15s 心跳；过期他端可在用户点击后接管（旧控制器停声）。
- progress：5s 节流 + pagehide keepalive flush；播放行为不写任何学习证据；完成时服务端只在全部段确实听完时标 `listened`，否则 `browsed`。
- 音频：段推进只由 `<audio>` ended 驱动（媒体时钟为准）；跳页/暂停按 epoch 丢弃旧音频；最多 3 段 Blob 在存；合成 key（owner/文本 hash/provider/voice/speed/normalizer）+ single-flight + WAV 原子写；预取当前 + 后 2 段，0.5/1/2s 退避轮询，纯 GET 幂等。
- 界面：桌面课件与讲稿并排、窄屏面板覆盖；讲稿/字幕共用 Markdown/KaTeX 渲染；控制栏按段跳转、预计剩余、倍速、音量、字幕、语音来源与全屏；音量/倍速本地记忆；`VoicePanel` 支持上课中途切换 TTS 策略/音色（`PUT …/audio-profile` CAS，未播段缓存失效）。

### 插问与随堂题

- 插问复用聊天管线：qa_session 幂等创建（run 锁内预留 ID + 落 intent，崩溃同 ID 重建）；`ClassroomTurnContext` 提供当前页公开讲稿边界材料 + 仍授权教材 file_id 的可信检索 override；回复可语音播放；「继续原课」回到 resume anchor（多轮追问回最初打断段）。
- 检查点：`reflect`（思考停顿，不写证据）与 `question`（复用冻结题，`evaluate_submission` 唯一受理链）；`question_id` 由 owner+run+checkpoint+template_hash 确定性派生（崩溃恢复不换题）；hint/reveal 先记帮助事件（帮助后作答不标 independent）；跳过不算答错；同题同答案幂等、不同答案冲突；未揭晓答案不出现在 audio/HTML/讲稿/导出。
- 课后：`GET runs/summary` 只汇总可观察事实；笔记经 `save-note` 幂等桥接进笔记中心（`source=classroom` 查重，同键返回同一 note_id）。

### 组件编辑与修订

- 课程详情默认展示学习目标/预计时长/内容安排，不请求课件 HTML、不创建 run；`?edit=1` 按需加载编辑器（按内容区宽度三栏/抽屉，讲稿/来源/设置分 Tab）。
- `edit_content.replace_block` 零 LLM 只替换指定组件；`regenerate_block` 用 `classroom_block@1.0.0` 只发送目标组件 schema、当前页目标、≤3 段关联讲稿与 ≤3 条来源摘录（输入 ≤24,000 字符、单次输出 ≤3,000 token、不做结构修复重试、不额外全课复核）；组件 ID/类型、图片资产绑定、来源引用与随堂题受服务端约束，其他页/组件/讲稿保留；两者复用版本 CAS、授权检查、预算账本与渲染发布。

### 生命周期

- 单课归档进统一回收站 trash 类型 `classroom_lesson`（durable op → 冻结 job → 快照 → bundle commit → 删活跃副本；音频/过期导出不打包可重建）；重复归档返回同一归档条目；恢复仅回原学习区（学习区不存在时提示先恢复学习区）；恢复 run→paused、lease 清空、中断 job→`needs_input(recovered_after_archive)`。
- 工作区归档冻结该区课堂并随 bundle 携带；`purge_account` 先 tombstone 再删根；`uploads_only` 清理只删自有上传图与含其 bytes 的编译产物，冻结 spec 不动；删除后不留空目录；`orphan_cleanup` 含 classroom 分类。

## Dependencies（依赖与被依赖）

- LLM 单通道（`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`，多模态；课堂车道保留独立时延/重试语义）；运行参数（上下文窗口/输出预算/温度等）以内置默认运行、管理员经 `GET/PUT /admin/llm-policy` 在线热调（`core/llm_policy.py`）。
- 外部服务：Tavily（检索）、Pexels/Pixabay（图片）、Azure Speech（云端 TTS）、MeloTTS sidecar（本地 TTS，启动判定与共享并发见 [voice.md](./voice.md)）。
- 内部依赖：身份层 `resolve_student_id`；工作区与教材授权（来源 file_id）；聊天内核（插问复用 `run_turn`）；笔记中心（save-note）；trash/归档中心；`orphan_cleanup` 与 `account_data.purge_account`；`core/atomic.py` 原子写。
- 被依赖：站内学习助手的课程动作（lesson.generate/retry/cancel/export）复用课堂 job 服务（见 [site-assistant.md](./site-assistant.md)）；管理端 `/admin/classroom-health*`。

## Invariants / security boundaries（不变量与安全边界）

- **课件永不执行模型代码**：内容载体是受约束 `LessonSpec`，编译器全量 HTML escaping、固定 CSP、无 raw SVG/JS 执行；模型 HTML/CSS/JS 一律不被接受；iframe `sandbox="allow-scripts"` + srcdoc（无 token URL）+ nonce 握手校验 `event.source`。
- **来源授权**：只使用工作区已授权教材与本人勾选附件；来源引用严格校验，悬空引用本地修正；未知 claim 来源移除并提示，不猜测真实来源。
- **正式题答案隔离**：答案私有存储，未揭晓不出现在 audio/HTML/讲稿/导出；答案披露仅检查题目所属页的明确披露，确有披露的题页本地改为中性作答引导。
- **图片供应链**：SSRF/IP 每跳重验、Pillow 重编码、EXIF 清理、asset hash、`candidate_id` 服务端签发；下载大小/像素/长边受限（`limits.py`）。
- **排版检查子进程隔离**：Node+Playwright 全局单实例 + 硬超时，子进程只继承最小环境白名单（PATH/HOME/XDG_CACHE_HOME 等），供应商密钥与代理变量不透传；Chromium 以服务账号 + 内核 sandbox 运行（禁 `--no-sandbox`）。
- **播放不写学习证据**：run/progress/插问等播放行为不进入学习证据账本；只有检查点作答走 `evaluate_submission` 唯一受理链。
- **存储边界**：目录 0700/文件 0600、发布事务原子性、tombstone 拦截晚到写、失败空号不复用；一切写路径 OSError 显式 `storage_unavailable` 而非静默。
- **能力如实**：渲染静态资源缺失时 capabilities 显式 `renderer_unavailable`（旧聊天不受影响）；外部服务不可用逐项禁用并在备课弹窗说明；缺静态资源或结构无法编译返回明确错误，不发布不可打开的课件。

## Configuration（环境变量与开关）

| 变量 | 默认 | 说明 |
|------|------|------|
| `CLASSROOM_ENABLED` | `0` | 课堂总开关（关闭时路由 403 `classroom_disabled`，导航自动隐藏） |
| `CLASSROOM_ALLOW_GUEST` / `CLASSROOM_ALLOWED_USERS` | `0` / 空 | 灰度门控：共享 `student_default` 须显式放行；allowlist 逗号分隔账号白名单 |
| `CLASSROOM_WEB_PROVIDER` + `TAVILY_API_KEY` | `tavily` | 联网检索 provider 与密钥（无 key 即 research 不可用） |
| `CLASSROOM_IMAGE_PROVIDERS` + `PEXELS_API_KEY`/`PIXABAY_API_KEY` | `pexels,pixabay` | 图片候选源 |
| `CLASSROOM_TTS_POLICY` | `auto` | `auto/cloud/local/silent`；自动策略优先已启用且支持当前语言的本地 MeloTTS，否则 Azure 标准音色 |
| `AZURE_SPEECH_KEY`/`AZURE_SPEECH_REGION`/`AZURE_SPEECH_ENDPOINT` | — | 云端 TTS |
| `CLASSROOM_TTS_VOICE_ZH`/`_EN` | `zh-CN-XiaoxiaoNeural`/`en-US-JennyNeural` | 云端音色 allowlist（唯一批准集合） |
| `CLASSROOM_LOCAL_TTS_ENABLED` / `CLASSROOM_TTS_LOCAL_FALLBACK` | 继承电话 provider / `1` | 本地回退开关；云端失败一次即锁本地、每 run 只提示一次；无语音降级为文字课堂 |
| `CLASSROOM_JOB_CONCURRENCY` / `CLASSROOM_OWNER_CONCURRENCY` / `CLASSROOM_LLM_CONCURRENCY` / `CLASSROOM_TTS_CLOUD_CONCURRENCY` | `2` / `1` / `3` / `2` | 并发上限 |
| `CLASSROOM_JOB_TIMEOUT_SECONDS` | `900` | job 累计 deadline |
| `CLASSROOM_MAX_PAGES` / `CLASSROOM_MAX_REVISIONS` | `24` / `20` | 页数/schema 上限与版本上限 |
| `CLASSROOM_AUDIO_CACHE_MB` / `CLASSROOM_AUDIO_TTL_DAYS` / `CLASSROOM_EXPORT_TTL_HOURS` | `500` / `7` / `24` | 缓存治理 |
| `CLASSROOM_API_DAILY_TTS_CHARS` | `100000` | 每用户每日云合成字符上限（鉴权连击 ≥5 计入并进健康告警） |
| `CLASSROOM_RENDER_TIMEOUT_SECONDS` / `CLASSROOM_NODE_BIN` / `CLASSROOM_RENDER_SCRIPT` | `45` / `node` / 内置 | 排版检查子进程 |
| `profile.prefs.classroom` | — | 个人默认（theme/pedagogy/voice_policy/voice_id/allow_local_fallback/captions/auto_advance/low_stimulus/pause_on_hidden 白名单，经 `/user/profile` 严格校验；run 语音优先级 请求 > 课程 brief > 用户偏好 > 系统默认） |

## Observability（trace/日志/指标）

- `GET/POST /admin/classroom-health[/cleanup]`：磁盘 <1GB、云鉴权连击 ≥5、近 20 job 失败率 >20%、queued 停滞 >300s、renderer 连败 ≥3、损坏课程、owner 音频超压七类告警（阈值在 `limits.py` 常量）；恢复动作 `sweep_audio`（全 owner 过期音频清扫）。
- job 状态经 SSE（心跳 15s）+ 10s 轮询 fallback；终态 SSE 后前端取完整 Job 快照展示原因与可用操作。
- `capabilities` 如实反映 renderer/research/images/tts 各自可用性；Azure voices 列表缓存由显式刷新填充，能力读不发网络请求。

## Tests / acceptance（测试索引）

后端 `services/api/tests/classroom/`（继承 `StorageSandboxTestCase`；`tests/support/classroom_fake_llm.py` 提供 fake LLM 全管线回归）：`test_classroom_api.py`、`test_classroom_assessment.py`、`test_classroom_audio.py`、`test_classroom_block_edit.py`、`test_classroom_chat.py`、`test_classroom_checkpoints.py`、`test_classroom_composition.py`、`test_classroom_exports.py`、`test_classroom_generation_normalize.py`、`test_classroom_generation_policy.py`、`test_classroom_health.py`、`test_classroom_images.py`、`test_classroom_inline_math.py`、`test_classroom_jobs.py`、`test_classroom_lifecycle.py`、`test_classroom_overflow.py`、`test_classroom_pipeline.py`、`test_classroom_prompts.py`、`test_classroom_reliability.py`、`test_classroom_render.py`、`test_classroom_render_layout.py`、`test_classroom_research.py`、`test_classroom_revisions.py`、`test_classroom_run_user_voice_prefs.py`、`test_classroom_runs.py`、`test_classroom_schema.py`、`test_classroom_sidebar.py`、`test_classroom_sources.py`、`test_classroom_storage.py`、`test_classroom_typegen.py`、`test_classroom_worker.py`（共 31 件），以及 `test_voice_azure.py`。

前端/浏览器：`apps/web/tests/e2e/classroom-{create,editor,player,resume,questions,security,export,workflow}.spec.ts`（route 级 API mock + 真实 audio ended 驱动）；确定性播放器套件 `pnpm test:player`（`tests/unit/test-classroom-player.mjs`）；布局/构图/公式/层级浏览器回归 `tests/unit/test-classroom-{layout,composition,inline-math,hierarchy}.mjs`。真实模型手工验收：`scripts/acceptance/classroom/live.py`（存储全隔离，`--brief` 可换主题，`apps/web/scripts/inspect-classroom-course.mjs` 出截图与等尺寸 PDF）。

## Related ADRs

- ADR-0001 source-only 仓库（`classroom/static/generated/`、vendor 与模型缓存均不入库）
- ADR-0002 运行数据统一 `NEXT_TUTOR_DATA_DIR`（`chat_history/classroom/` 单一根）
- ADR-0003 BM25 基线 + 向量可选（来源证据 chunk 冻结走 BM25）
- ADR-0004 single-worker（生成 job 为进程内 asyncio 任务，靠检查点与 reaper 恢复）
- [ADR-0013](../adr/0013-durable-workflows.md) durable workflows（TEMPORAL_ADDRESS 配置后生成 worker 移交 worker 进程，检查点/预算/epoch 不变）
