# 工作学习区课堂模式：开发执行方案

版本：1.0；完成日期：2026-09-26；源码与外部接口核对：2026-09-25 至 26 日。本文是待实施方案，不表示功能已经开发或验收。

目标：在现有教材驱动的学习工作区内，增加“一键备课 → HTML 课件 → AI 教师完整讲授 → 随时插问 → 随堂练习 → 课后回顾”的课堂模式。幻灯片负责展示，逐页讲稿负责解释，课堂播放器负责教学节奏。沿用现有资料权限、身份、学习评价和本地语音能力。

本文基于本仓库源码及 `docs/DESIGN.md` 独立设计；外部查阅范围仅为图片服务、检索服务、语音服务和浏览器的官方接口文档，没有参考其他教学、课件生成或课堂产品的源码、界面或设计方案。实施同样遵守此边界。

“可直接执行”的含义：产品默认值、核心架构、数据契约、故障处理、文件责任、实施顺序和验收门均在本文确定。外部服务凭证、真实语音质量及不同浏览器表现仍需通过指定验收，不能把计划当作零故障保证。

## 0. 已确定的实现决策

| 项目 | 决策 |
|---|---|
| 产品归属 | 每个工作学习区内增加课堂列表与课堂详情；普通聊天继续使用现有 URL |
| 课件格式 | 模型生成受约束的 `LessonSpec`；本项目编译器生成真正的 HTML/CSS 展示文件 |
| 模型权限 | 可制定检索请求、选图、组织内容；不能生成并执行任意 JS、Python、Shell 或任意网络请求 |
| 展示与讲授 | 每页同时生成简洁展示块、详细讲稿段、板书/高亮动作、停顿与检查点 |
| 首发视觉模板 | 5 套原创主题、9 种固定布局；不接入第三方演示项目或模板市场 |
| 首发教学模板 | 概念精讲、例题推导、考前复习、案例探究、前沿专题，共 5 套 |
| 教材优先 | 默认教材为主；外部知识与教材分开标注；严格教材模式不以网络结果冒充教材依据 |
| 联网知识 | 新增受控服务；首个搜索与正文提取适配器为 Tavily，使用其官方 REST API |
| 图片 | Pexels 为首选、Pixabay 为补充；只下载选中的素材，保存署名和来源信息 |
| 首发云端语音 | Azure Speech REST 标准音色；抽象接口允许后续新增供应商，本期不同时实现多家云 TTS |
| 本地语音 | 保留 MeloTTS sidecar；课堂可显式选择本地，也可作为云端失败回退 |
| 播放协议 | 作业进度用认证 fetch SSE；课堂音频按讲稿短段经认证 REST 获取，浏览器统一播放 |
| 教学进度 | 后端保存课堂 run 检查点，前端以实际音频播放时间为准；不以 TTS 完成时间翻页 |
| 随堂评价 | 复用冻结题目与 `evaluate_submission`；不增加第三种学习证据来源 |
| 持久化 | 继续文件存储、原子写、单 uvicorn worker；独立课堂目录，接入回收站/账号清理/沙箱 |
| 首发导出 | HTML 离线课件 ZIP、逐页讲稿 Markdown、打印版；不把 HTML 改后缀伪称 PPTX |
| 更新策略 | 课件不可变版本；编辑/更新资料产生新版本；已开始的课堂固定原版本 |
| 本次实际改动 | 只写本计划，不修改业务代码、运行数据、密钥或已有未提交变更 |

## 1. 现状核实及必须保留的边界

以下以源码为准；`docs/DESIGN.md` 中部分历史段落仍描述旧评分协议，实施不可照抄旧段落。

| 能力 | 当前文件与事实 | 本期接入方式 |
|---|---|---|
| 应用骨架 | `frontend/src/components/shell/AppShell.tsx` 管理 SideNav、TopBar 和内容区 | 课堂仍置于该骨架内；只在显式专注模式隐藏外层导航 |
| 工作区入口 | `components/sidebar/WorkspaceItem.tsx` 当前展开资料、公共记忆、会话 | 增加“课堂”固定入口及菜单“一键备课”，不把每张幻灯片混入会话树 |
| 工作区实际形态 | 目前主要嵌在 chat 页 Sidebar；不存在完整的 `/workspaces/[id]` 页面 | 新增工作区课堂路由，不假定已有工作区主页可直接挂 Tab |
| 聊天 | `app/(workspace)/chat/[[...sessionId]]/page.tsx`，会话由 URL 定位，新工作区提示保存在 sessionStorage | 添加工作区模式切换条；课堂自身以 URL 中 workspaceId 为准 |
| 身份 | `backend/app/identity/deps.py::resolve_student_id` | 所有课堂、素材、音频、作业、导出端点复用；外来 ID 返回 404 |
| 资料范围 | `core/workspace.py` 的 `readable_files/readable_stores` 与 `resolve_textbook_file` | 课堂只能选当前工作区已授权来源；不自动读取整个个人资料库 |
| 会话附件 | 当前对话附件不会自动跨会话共享 | 从聊天进入备课时，只有显式选中的本人会话附件可成为额外来源 |
| 教材工具 | `tools/knowledge_search.py`、`tools/knowledge_read.py` | 复用检索和证据门，不能另写一套绕过权限的教材扫描器 |
| 联网能力 | 当前工具集中未发现通用 web search；`agents/preresearch.py` 是教材前置检索策略 | 新增联网模块；不能宣称已有网络检索只需打开开关 |
| 题目与作答 | `agents/assessment/manager.py::register_task_snapshots/register_quiz_payload/evaluate_submission`；`api/v1/quiz.py` 为 JSON 202 受理 | 题目先冻结，随堂答题走同一受理链；不新建课堂掌握度 |
| 证据类型 | `agents/student_model/evaluation/schema.py::SourceKind` 只有 dialogue、assessment | 学生课堂提问归 dialogue；随堂正式作答归 assessment；观看不产生掌握证据 |
| 题卡组件 | `components/chat/QuizCard.tsx` 导出 `QuizQuestionCard` | 复用实际组件，必要时抽取 transport hook；仓库没有独立的 `chat/QuizQuestionCard.tsx` |
| 语音 | `voice/base.py` 返回 PCM16 + sample_rate；factory 当前只有 off/stub/melo | 扩展 provider，课堂音频流程独立于现有电话 WS 状态机 |
| 电话模式 | `api/v1/voice.py` + `lib/voice/useVoiceCall.ts`，浏览器 STT + PCM 播放 | 保留现有协议；课堂不重新实现服务器 STT，也不启动电话式黑板浮层 |
| 配置 | `core/config.py` 的 `VOICE_TTS_PROVIDER` 默认 off；`start.sh` 仅在 melo 时启动 sidecar | 新增课堂云端优先配置；显式更新 sidecar 启动判定，防止配置了回退却未启动 |
| 删除 | `api/v1/workspace.py` 删除调用 `trash.archive_workspace` | 课堂必须随工作区归档/恢复，而非直接遗留在磁盘 |
| 原子写 | `core/atomic.py::atomic_write_text/file_lock`，锁是进程内 RLock | 不能跨 await 持 RLock；所有读改写为同步短临界区 |
| 测试 | `tests/storage_sandbox.py`；CI 禁真实凭证 | 新目录和缓存与测试沙箱、孤儿清理在同一提交注册 |

现有全局提示词、M1–M10 聊天流程、公共教材只读、演示账号的精确 Git 白名单均保留。课堂运行数据不因 demo 账号已有例外而自动成为可提交内容。

## 2. 交付范围与成功标准

### 2.1 首发必须完成

1. 在工作区选择主题/教材章节后，一次确认即可后台生成整节课程；可离开页面，回来继续查看。
2. 至少支持中文、英文授课；沿用显式学段与“自动”学段语义，默认继承账户/当前学习环境，最终兜底本科。
3. HTML 课件、逐页讲稿、来源、插图、讲解动作作为同一版本发布。
4. 用户可预览、修改单页文字/讲稿、换图、切换视觉主题、重生成单页、生成新版本。
5. 开始/暂停/继续、上一页/下一页、重复本段、字幕、语速、音量、专注、全屏、目录跳转、断点恢复。
6. “没听懂”“举个例子”“提问”在当前页旁展开；回答结束后由用户点击继续原课程。
7. 随堂思考/正式作答、提示、揭晓、跳过、结果恢复；课后可保存学习笔记。
8. 云端优先语音、本地回退、无语音也能完成课堂；不因图片/网络服务失败而失去已有内容。
9. 认证、版本一致性、取消重试、重启恢复、导出、删除恢复、账户清理与测试全部闭环。

### 2.2 明确不纳入首发

多人直播、教师真人形象/口型、摄像头情绪识别、自动录音、任意 JS 仿真、任意网页浏览器 Agent、商业模板市场、视频素材自动下载、原生 PPTX 导出、云端声音克隆、离线自动判分、离线云端问答。

首发导出 ZIP 可双击 `index.html` 看课件、查看讲稿、打印；联网问答和正式评价仅在应用内可用。课程界面名称为“课件（HTML）”，帮助文字解释它是浏览器幻灯片，不承诺 PowerPoint 原生编辑。

### 2.3 验收结果必须可观察

- 学生能在工作区找到课堂并完成 15 分钟示例课程，中途插问后回到原讲稿位置。
- 概念/推导页讲稿包含原因、条件、例子或误区，而不是把页面要点逐条复述。
- 无 API key 的测试环境可使用 stub 完成闭环；真实部署配置云端、图片、搜索 key 后通过单独烟测。
- 同一操作重试不重复建课、不重复正式作答；收费服务最多按本文预算重试，不能声称外部计费 exactly-once。
- 私有课堂 URL、asset ID、audio ID 被另一账户使用时不能读取内容。
- 图片失效、网络中断、服务重启、旧版本编辑、标签页竞争均有确定行为。

## 3. 信息架构与入口

### 3.1 路由

新增前端路径：

```text
/workspaces/[workspaceId]/classroom
  该学习区的课程列表、生成任务、上次课程
/workspaces/[workspaceId]/classroom/[lessonId]
  课件详情与预览；?revision=N 固定预览版本，否则最新已发布版本
/workspaces/[workspaceId]/classroom/[lessonId]/learn/[runId]
  已创建的课堂播放器；刷新不会重新创建 run
```

“开始上课”先 POST 创建/恢复 run，再导航 learn 路由；进入详情、预览、浏览器预取均不能创建 run 或触发 TTS。所有 ID 用 `encodeURIComponent` 构造路径，工作区现有中文 slug 不得二次解码或被新增 UUID 校验拒绝。

### 3.2 现有界面如何自然接入

1. 展开的 WorkspaceItem 在资料/公共记忆下、会话列表前显示固定“课堂”入口，带小型课程数量；菜单增加“一键备课”。原工作区标题仍负责展开，不改变已有习惯。
2. 当前聊天属于工作区时，在聊天标题区域显示 `工作区名称 · 对话 | 课堂`。选“课堂”进入该区课堂列表；返回“对话”回到该区最近访问且仍存在的普通会话，否则 `/chat?ws=<id>`。
3. 工作区课堂页面顶部复用同一 `WorkspaceModeBar`；“对话”与“课堂”使用真实链接与 `aria-current`，避免伪装成可键盘切换内容的同页 Tab。
4. 无工作区的普通聊天不显示误导性的课堂 Tab；“根据这段对话备课”先打开选择工作区 Modal，明确选入的消息和附件。不得默认把整个聊天或所有附件发给外部检索服务。
5. 知识图谱概念抽屉增加“上一节课”，深链仅携带 `concept_ref`/章节 ID；备课 API 再检查它属于目标区。学习编排任务可进入同一备课 Modal，携带受信任任务绑定。
6. 不新增全局“课堂中心”顶级导航；以后确需跨区聚合再做，不在首发让用户在两个课堂入口之间判断。
7. Sidebar 快照只增加 `classroom_summary={lesson_count,active_job_count,last_lesson_id}`；从可重建索引批量读取，绝不逐课解析讲稿/音频。已有快照 ETag 包含新摘要。

模式切换必须保留聊天草稿：当前 `ChatInput.tsx` 的正文和待上传 File 在组件 useState 中，路由卸载会丢失，不能假定已有持久化。新增 owner+session_id（新会话用 workspace_id）键的内存草稿仓，最多 20 项；保存正文、未发附件引用及 File 对象，仅 SPA 切换保留。刷新只恢复 sessionStorage 中的正文/已上传引用，不序列化 File bytes；未上传文件提示重新选择。发送成功、删除会话、登出清除对应草稿，不能把文件自动上传来换取草稿保存。

与用户需求对应的快捷菜单可标“一键授课”，点击打开同一个备课 Modal；其中主要动作仍叫“生成课程”，生成完成后“开始上课”。这里的一键指免写长提示词和自动完成备课流程，浏览器声音仍由开始按钮明确触发。

### 3.3 列表页

- 标题：“大学物理 · 课堂”；主要按钮“一键备课”。
- 有未完成 run：置顶“继续上课：动量守恒 · 第 4/10 页”，副按钮“从头开始”（新建 run，并复用课件内容）。
- 卡片展示主题、教材章节、预计授课时长、页数、风格、最近更新时间、生成/可上课/需检查/失败状态。
- 筛选：全部/可上课/生成中；排序按最近更新。列表统一 `Pager`，默认 5 条。
- 生成中的任务和课程使用同一张卡，不额外制造一个无法定位内容的任务中心。
- 空态说明两条路径：“选择本区教材章节”或“输入想学的主题”；教材为空仍可生成主题课程，但明确标“通识资料”，不伪造教材依据。
- 旧任务失败显示具体阶段和“重试此阶段/调整设置”，成功的旧版本继续可用。

## 4. 一键备课、预览与修改体验

### 4.1 备课 Modal

长表单使用现有 `Modal`、`Input/Textarea/Field/FIELD_CLS`。桌面宽 880px 左右，窄屏全屏；顶部固定标题、底部固定操作，中间独立滚动。

默认只展示主题、来源、时长和模板；“更多设置”折叠。选择章节后自动填主题和教学目标，允许改写，不强制逐步向导。主要按钮“生成课程”，高级流程才有“先看大纲”。

| 设置 | 默认/限制 | 行为 |
|---|---|---|
| 主题 | 必填 2–120 字 | 选章节可自动填；空白不可提交 |
| 学习目标 | 最多 5 项，每项 ≤120 字 | 不填由模型提出；最终稿必须可观察、可检查 |
| 教材/章节 | 本区当前来源；最多 8 个文件、12 个章节 | 与工作区来源取交集；跨卷保留卷名和页码 |
| 额外对话材料 | 默认不选 | 仅本人显式指定的 session 和附件；不扩大工作区全局可见性 |
| 时长 | 15 分钟；可选 5/10/15/20/30 | 指讲授+建议思考时间，不含用户自由提问时间 |
| 页数 | auto | 对应目标 4–6/6–9/8–12/10–15/14–20；硬上限 24 页 |
| 学段 | 继承当前有效值 | “自动”保持空串语义，不强制本科措辞 |
| 语言 | 继承明确回答语言，否则主题语言 | v1 为 zh/en；不自动生成双语整套课件 |
| 教学模板 | 概念精讲 | 选择后展示一句说明 |
| 视觉模板 | 学术清晰 | 5 个静态原创缩略图，无外部请求 |
| 教材策略 | 有教材时 textbook_plus；无时 web_topic | strict_textbook / textbook_plus / web_topic，详见 §7 |
| 联网补充 | 有服务能力时开，否则显示不可用 | 用户可关；严格教材仍允许单独启用图片搜索 |
| 时效 | 基础知识 | 基础/近一年/近一月；前沿专题默认近一年 |
| 图片 | 适量 | none / balanced / rich；rich 仍只在有教学作用时使用 |
| 随堂检查 | 标准 | none / light / standard；默认 15 分钟 2 个检查点 |
| 语音 | 自动：云端优先 | auto / cloud / local / silent；按能力展示可选音色 |
| 云失败回退 | 开 | 回退至已启用本地；用户选择 cloud 且关回退时只变文字模式 |
| 语速 | 继承个人 tts_speed，非法则 0.9 | 播放倍速 0.5–1.5；合成基准与播放倍速分开，见 §11 |
| 自定义教学要求 | ≤1000 字 | 数据字段，不能覆盖权限、引用与 schema 约束 |

表单底部只用简短说明：“生成会使用所选资料。启用联网时会检索主题关键词；启用云端语音时讲稿将发送给语音服务。”用户选择持久化为偏好，不每节课重复确认。

有默认值和合理目标时，点击一次即可生成，不要求用户批准大纲。用户主动选择“先看大纲”才进入 `awaiting_outline`，修改/确认后继续。

### 4.2 生成过程

显示可理解的阶段：“读取教材 → 查找补充资料 → 组织课程 → 编写课件与讲稿 → 检查排版 → 准备开课”。展示当前页数、已完成页数及可见失败原因，不展示内部思维链或原始 prompt。

进度值来自阶段和完成页数，不按时间伪造 99%。暂不提供精确倒计时；显示“通常需要数分钟”，真实运行后再用历史分位数给范围。用户可以关闭 Modal、离开页面、取消任务。

已生成页可显示带“草稿”水印的只读预览；整套一致性检查通过前不能自动开课。取消只停止后续工作，保留草稿和旧发布版本；网络请求已被供应商受理时不能承诺退还费用。

### 4.3 预览与编辑

详情页左侧缩略图/目录，中间课件，右侧“讲稿 | 来源 | 设置”。默认只打开当前页讲稿，避免一次加载全部复杂 DOM。

- 每页可修改标题、要点、图注、讲稿段、允许的高亮位置；不提供 HTML/JS 编辑器。
- “更详细/更简洁/换个例子”创建本页修订任务；单页重生成时携带前后页摘要、目标、既有术语表及来源。
- “换图”只展示当前主题候选图；选择后生成新 revision，不替换正在上课的版本。
- “换视觉风格”确定性重编译，无需重新请求 LLM；内容布局不适配时按模板约束重新排版并检验。
- 页面删除/排序必须校验目标覆盖、引用、checkpoint 位置及过渡讲稿；有错误则保存草稿并显示待修复项，不能发布。
- 每次保存以 `base_revision` 做 CAS；冲突显示“内容已更新”，提供重新载入/保留本地草稿，禁止静默覆盖。
- 发布新版时旧 run 继续原版；“用新版从头学习”显式创建新 run。没有自动迁移讲稿 offset。
- 查看完整讲稿不包含随堂题答案；编辑器不提供答案预览，解答只能在课堂题卡通过正式揭晓取得并记帮助事件。

## 5. 课堂中的布局与交互

### 5.1 桌面布局

```text
应用导航 | 工作区名称  [对话] [课堂]      课程名      退出课堂
         | 章节目录       HTML 幻灯片舞台             讲稿/提问/笔记/来源
         | 1 引入         16:9，保持内容完整           默认折叠
         | 2 概念         当前讲解块高亮
         | 3 推导         字幕在舞台下方独立区域
         |-------------------------------------------------------------
         | 上页  播放/暂停  下页   本段重讲   4/10   速度  音量  专注  全屏
         | [没听懂] [举个例子] [提问]          本节约剩 8 分钟
```

舞台是视觉中心；提问区不是主界面的聊天瀑布。讲授时不显示聊天输入大框、电话手机模拟或三块黑板。板书出现在幻灯片的受控区域，随讲稿段展开。

宽度 ≥1280px：目录 200–240px、右栏 300–360px 可折叠；960–1279px：默认收起右栏，目录可抽屉；<960px：以课件+底部操作为主；<640px：固定“播放/提问/目录/更多”四个主操作，其他设置放底部抽屉。

### 5.2 舒适度细节

1. 第一次生成完成只提示“课程已准备好”；必须用户点击“开始上课”后申请音频播放，不突然发声。
2. 切页动画 160–220ms 淡入，禁止大幅旋转/缩放；跟随 reduced-motion。低刺激模式关闭光标移动和渐变动画。
3. 字幕默认开，最多两行，单独 18px 起；完整讲稿可滚动。用户手动滚动后暂停自动跟随，出现“回到当前讲解”。
4. 页内正文按 1280×720 设计稿至少 28px；移动端提供“阅读版”重排，同一份数据和讲稿位置，不把整张桌面页缩到字不可读。
5. 不把图片压暗后强行叠大量文字；正文与图注有独立色块；科学图示不裁切标签。
6. 用户开来源/复制文字/查看图片时不自动翻页，默认暂停讲授；收起后由用户恢复。音量弹出、调倍速不暂停。
7. “没听懂”自动暂停并解释当前段；“举个例子”提供一个针对当前概念的具体例子；这些操作不改写整节课程。
8. 恢复原课时提示“继续第 4 页：碰撞前后的总动量”，默认从打断段开头重讲，避免半句话续接；同页可选“从本页开始”。
9. 后台标签页/锁屏默认暂停，恢复可见后保持暂停；不计入有效学习时长。首发不承诺后台连续播放。
10. 全屏失败、浏览器不支持全屏时使用应用内专注布局；退出全屏不结束 run。Esc 优先退出抽屉/图片放大，再退出全屏。
11. 45 秒以上无进展缓冲显示“语音准备较慢”，提供“切本地/只看讲稿/稍后继续”；没有无限旋转。
12. 总时长为当前预计讲授时间+未完成建议停顿，提问/学生思考不倒扣；有音频后用实际时长替换估算。
13. 语音失败不把讲稿隐藏；图片失败显示图注/替代说明；独立服务故障不丢用户进度。
14. 没有强制倒计时答题或连续失败惩罚；提供“再想一会儿”“跳过此题”。跳过不算答错或掌握。
15. 连续授课约 20 分钟时在页边界给轻量休息提示，可关闭；不在推导中间弹窗。

### 5.3 键盘与辅助访问

- 播放/暂停 Space；上一/下一页 ←/→；字幕 C；全屏 F；提问 Q；打开设置时展示快捷键帮助。
- 焦点在输入框、textarea、contenteditable、菜单中时不响应全局快捷键；不能截断中文输入法操作。
- 页面、控件、进度、图片 alt、公式可读文本都有语义标签。幻灯片标题变更用克制的 live region，不把每个高亮和字幕都重复播给读屏器。
- 键盘焦点可见；主触控目标至少 44×44px；色彩不作为唯一状态信号。
- 浅色/深色各有真实主题，不对彩色照片整体 invert；课件主题和应用主题可同步，也可固定浅色课件。

### 5.4 退出、断点和跨设备

普通退出保存进度并暂停，不额外弹确认。只有未提交的讲稿编辑显示保存提示。问答草稿按 owner+run 键本地短期保存，登出清除。

刷新后先取服务端 run，显示“继续上次课堂”；恢复音频必须重新点击播放。断网时可看已经载入的页和讲稿；不能提交正式答题或新提问，UI 明确待联网。已缓存音频最多播完当前段后暂停。

同一 run 只允许一个播放控制者：服务端 lease 15 秒心跳、45 秒到期；第二标签页只读并提供“在这里继续”，显式接管提高 `lease_epoch`，旧控制者心跳/写进度返回 409 并停止声音。广播频道只作快速提示，最终以服务端 lease 为准。不同 run 同一浏览器也由共享 audio focus 保证只能一个发声。

## 6. 教学内容与逐页讲稿规范

### 6.1 三层内容

1. **展示层**：标题、短要点、公式、图示、对比表、示例步骤、必要引用。每页围绕一个主要教学目标。
2. **教师讲授层**：逐段说明动机、含义、推理、条件、案例、反例和衔接；生成可直接读出的口语稿，不含“此处补充例子”等占位语。
3. **教学动作层**：与讲稿段绑定的显示块、高亮、板书步骤、思考停顿、checkpoint。动作是有限枚举，不是可执行代码。

讲稿和课件同一次按页生成，再做整课校验；不得先做完 PPT 再让模型凭一张缩略图临时编整段授课。

### 6.2 课程教学结构

默认 15 分钟课程：约 1 分钟引入/前置 → 7 分钟核心概念与推导 → 4 分钟例子与误区 → 2 分钟检查/反馈 → 1 分钟总结。该比例是预算，按模板调整，不机械每页都塞齐全部环节。

每个核心概念必须至少覆盖：是什么、为什么、何时成立、一个具体例子、一种常见误解。数学/物理推导必须说明关键变形理由与量纲/条件。案例型课程必须区分事实与解释；前沿专题必须标明时间及不确定性。

章节过大时优先缩小本课目标并建议“下一课”，不能把整章压成 20 个名词。生成结果须显示“本课范围”和未覆盖内容。

### 6.3 讲稿分段

- 一页通常 3–10 个段；每段中文目标 40–100 字、英文 25–65 词，硬上限朗读规范化后 240 字符；超限由确定性句界切分，不能截断公式语义。
- 每段 `role` 为 `motivation/explain/derive/example/misconception/transition/summary` 之一。
- `display_text` 可包含数学；`spoken_text` 是口语表达；TTS 只接后者。公式口播优先由作者稿说明语义，`to_speakable` 仅为最后兜底。
- 例如页面显示“总动量守恒：系统合外力为零”；讲稿必须解释“系统”“合外力”、内力抵消及近似条件，而非反复朗读该句。
- `pause_after_ms` 仅 0–3000，用于句间节奏；思考停顿用独立 `reflect` 节点，不能塞进长静音音频。
- 每个音频 chunk 与一段或该段明确的子片段绑定；段级字幕精确同步，首发不宣称字级高亮。
- 页切换在最后一段播放完成并完成规定 checkpoint 后发生；默认停 800ms，可关闭自动翻页。

### 6.4 五种教学模板

| ID | 页面组织 | 强制质量要求 |
|---|---|---|
| `concept_deep@1` | 情境→定义→机制→条件→例子→误区→检查→总结 | 核心概念必须有直觉与严谨表述两层 |
| `worked_example@1` | 问题→已知/未知→方法选择→逐步解→检验→变式 | 展示“为什么选这个方法”；例题演示与独立检查题分开 |
| `exam_review@1` | 目标→知识框架→易混对比→代表题→错因→自测 | 不虚构真题年份/出处；时间受限时强调辨析而非堆题 |
| `case_inquiry@1` | 现象→预测→证据→解释→边界→迁移 | 先有思考停顿，后有解释；案例事实有来源 |
| `frontier_briefing@1` | 背景→现状→关键证据→争议→应用→开放问题 | 必须联网成功、记录 as_of；缺证据不能假称“最新” |

模板是版本化的结构规则、段角色分配和样例，不是自由 prompt 字符串直接拼接。用户自定义要求位于数据标签内；系统 schema、权限、来源规则优先。

### 6.5 提示词注册与固定输出

新增 `backend/app/prompts/classroom.py`，统一经 `prompts/registry.py` 注册以下 `@1.0.0` 定义；更改文本必须升版本：

| prompt id | 输入 | 输出 |
|---|---|---|
| `classroom_outline` | brief、章节结构、教材证据摘要、学段、目标时长、教学模板 | 目标/前置知识/有序页计划/预算/所需证据/选图意图 |
| `classroom_search_plan` | 页计划、已覆盖证据、联网策略 | 最多 6 个公开关键词查询，目的、时效、期望来源类型 |
| `classroom_slide` | 当前页计划、证据包、相邻页摘要、术语表、图片候选元数据 | 单页 SlideSpec 与讲稿段、原始 claim→source 对应 |
| `classroom_review` | 整课精简稿、逐页依据、课程目标 | 逐条 issue(code,severity,slide_id,field_path,reason)，不能自报通过盖章 |
| `classroom_repair` | 原页、明确错误列表、同一证据包 | 完整替代页，仅修改允许范围 |
| `classroom_explain` | 当前页/段、已讲内容、学生问题、受权来源 | 插问回复风格约束，实际回复经既有 run_turn |
| `classroom_visual_queries` | 教学含义、图像角色、排除项、语言 | 最多 8 个图片搜索意图；不返回猜测 URL |

大纲/页生成等结构化调用统一 `complete(disable_thinking=True)`；通过 Pydantic 校验，失败最多一次受预算约束的修复。外部资料放 `<material_excerpt>` / `<web_excerpt>` 数据边界内；不输出或持久化原始 CoT。

关键系统指令内容在实施时按下列确定要求写入，不留开放设计：

```text
你在编写一节可以实际讲授的课程。只输出所给 JSON schema。
页面用于让学生看，spoken_text 用于让教师讲；不能只把页面文字改写一遍。
使用已提供的 source_id 与 asset_id，不创造来源、URL、图片内容或页码。
证据不足时提出明确缺口，不把网络事实写成教材结论。
每页服务一个主要目标；重要定义说明适用条件，推导说明理由。
讲稿须为自然口语且可直接播放；不得出现待补充、参见某页等未解析占位。
行为只从允许的动作枚举选择，不生成脚本、样式代码或工具调用代码。
不得提前给出正式 checkpoint 的答案；答案进入独立受保护的题目材料。
```

## 7. 来源策略、联网检索与时效

### 7.1 资料授权及冻结

`resolve_classroom_sources(owner, ws, selection)` 返回可读 source records 与 scope fingerprint：

1. 读取 owner 的工作区并验证归属。
2. 所选 textbook/library 文件必须属于 `readable_files(ws)`；会话附件必须显式指定本人 session 且为该 session 可读。
3. 使用原有 BM25/hybrid 与 `KnowledgeReadTool` 获取章节相关证据；没有全文解析就提示“资料尚未就绪”，不得根据文件名编内容。
4. 冻结 file_id、namespace、内容 hash、chunk_id、物理页/印刷页、章节、取得时间、概念 scope revision。模型只看到服务端签发的 `src_...`。
5. 保存用于本课的必要摘录及 hash，不复制整本书进课堂目录。新查询不得读取未授权文件。
6. 生成途中或发布前重新检查原始来源授权和 hash；权限丢失中止/等待用户重选，不用旧内存绕过变更。

大纲个性化只读取本工作区已有学习评价摘要、明确学段和讲解偏好；每项保留 evidence_status，未知前置知识写“尚未确认”而非“不会”。M2/M3 关闭或无数据时用 brief 确定的教学模板，不为备课生成伪造学生画像。此摘要只给课程 LLM，绝不作为搜索查询或图片检索参数。

生成课件是当时授权材料的私有派生快照。原教材取消选入或删除后，已发布课件可由原 owner 继续看，显示“原始来源已变化/不可用”；不再允许点击原文、刷新引用或基于被撤销源继续生成。新任务必须重新授权。删除账号或课程会同时删除派生快照；public 命名空间从不写入课堂数据。

### 7.2 三种策略

| 策略 | 教材要求 | 网络知识 | 发布条件 |
|---|---|---|---|
| `strict_textbook` | 必须选教材/本区资料 | 不能支撑新增教学结论；图片可独立启用 | 核心目标均有教材证据；检索失败则 needs_input |
| `textbook_plus` | 至少一个本区来源 | 可补背景/应用/时效，标“拓展资料” | 教材结论与拓展结论分别可追溯；不得冲突后无说明混写 |
| `web_topic` | 可无教材 | 主支撑来源 | 普通知识可关闭联网并标“未外部核验”；前沿/最新请求必须检索成功 |

基础知识、推导中的自造例子需标 `author_explanation` / `constructed_example`，不伪造逐句引用。所有外部事实、数字、时效结论必须有 claim-level 来源；核心结论不能只引用搜索摘要。

### 7.3 首个搜索适配器

新增 `WebResearchProvider.search()` / `extract()` 与 `TavilyResearchProvider`，使用服务端 httpx、`trust_env=False`。`POST https://api.tavily.com/search`，Bearer key；显式设置 `search_depth="basic"`、`auto_parameters=false`、`include_answer=false`、`include_raw_content=false`、`include_images=false`、`max_results=5`；有时效约束才附带日期范围。避免自动参数无提示增加请求成本。官方契约见 [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)。

搜索返回候选后，挑选最多 8 个页面，用 `POST /extract` 提取正文。处理 `results` 和 `failed_results`，HTTP 200 不代表每个页面成功。提取文本只作资料，不执行页面脚本；v1 不在本机搭建通用网页爬虫。官方契约见 [Tavily Extract](https://docs.tavily.com/documentation/api-reference/endpoint/extract)。

`web_search`/`web_read` 作为课堂生成管线内部受控工具提供给规划器；模型只能提出有限请求，服务器执行预算、URL 和来源限制。首发不将它们无条件挂到所有普通聊天 ReAct 工具列表，避免改变现有聊天行为和费用。

### 7.4 研究流程与质量规则

- 先教材检索，再做覆盖矩阵；仅对未覆盖目标、现代应用、用户要求最新的内容联网。
- 搜索词只包含必要主题/术语/年份；不发送姓名、账号、学习评价、完整聊天、私有教材原文或答案。
- 优先原始论文、标准发布方、官方机构/产品文档、大学课程；不把排名第一视为权威。
- 时效事实保存 `published_at`、`updated_at`（允许 null）、`retrieved_at`、`as_of`；查询返回时间不是发表时间。
- “最新”无法被证实就改为“截至 YYYY-MM-DD 检索到的资料”；关键时效事实无来源则禁止该页发布，用户可选择删除该拓展页。
- 冲突事实并列记录 `conflicts_with` 和解释；不让模型自选一个数后抹去冲突。教材旧版与新资料的差异独立成拓展说明。
- 来源摘要每页通常 1–3 条，页脚短标记，右栏完整标题/机构/日期/原网址；TTS 不逐字念 URL。
- 用户手动提供 URL 时也只允许公开 HTTPS 页面，不能绕过域名/IP/长度过滤；收费墙、登录页面不自动绕过。
- 网页检索/提取每节课的总调用和字数有硬上限，详见 §15；失败保持已有材料并展示未完成项。

### 7.5 外部输入与网络边界

外部文本和 API 返回都不可信。结果中的“请忽略上文”“调用工具上传密钥”等内容只能作为被引用数据，不能改变工具权限。LLM 给出的 URL 不直接下载；只能引用服务器收集的 source/asset ID。

对 URL 做 scheme/host/端口/长度、userinfo、IP literal 与解析地址检查，禁止 localhost、私网、link-local、metadata、file/data/javascript 协议；域名白名单比较完整 hostname，不用字符串后缀漏洞。外部提取不传递内部凭证。图片下载每跳重验并使用经核验的解析地址建立连接，TLS SNI/Host 保持原域；仅“先 DNS 检查、随后默认客户端重新解析”不足以防 DNS rebinding。

无需本地直抓任意网页：第一版正文读取统一交给配置好的提取 provider；本机下载范围仅为图片 provider 的批准 CDN。出站网络进一步按部署允许名单限制。凭证不得进入浏览器、讲稿、导出文件或 trace；Pixabay query key 特别从错误日志和 URL 中剥离。

## 8. 图片资源管线

### 8.1 按用途选图

`VisualIntent.role` 只能是 `scene/object/process/diagram/data/decoration`；每项包含教学用途、必要对象、不可出现内容、横竖比、建议检索词、替代文字。

- 照片适合情境引入、设备/现象观察、应用案例；不以普通图库照片证明某次实验或某个新闻事件。
- 准确受力图、函数图、流程图和带数据的图表用本项目受控图形块生成，不靠图库搜索拼凑。
- 科学图示中若存在决定结论的标签/数值，必须来自可追溯数据或确定性例子；没有视觉核验能力时不使用来源不明的专业示意图。
- `balanced` 目标约 30–50% 的适合页面有图；`rich` 目标约 50–70%；这不是硬配额，数学推导页可完全不用照片。
- 禁止每页重复同一张大图、无意义背景图、图片盖字、图不对题。至少用图注说明图与当前教学目标的关系。

### 8.2 Provider 具体规则

| Provider | 请求与处理 | 本项目限制 |
|---|---|---|
| Pexels | `GET https://api.pexels.com/v1/search`；Authorization 为原始 key；query、orientation、locale、per_page | 每次取 8 个候选，优先 landscape、zh-CN/en-US；保存 photo ID、摄影师、原始页面、尺寸与授权链接 |
| Pixabay | `GET https://pixabay.com/api/`；key 在服务端 query；q、lang、image_type、orientation、safesearch | `safesearch=true`，per_page=8，q≤100 字符；优先 photo/illustration，不下载未经处理的外部 SVG |

Pexels 文档当前默认额度为每小时 200、每月 20,000 请求，程序读取实际限流响应并使用配置上限，不将默认额度视为永久保证；中文查询不足时允许一次英文改写。见 [Pexels 官方文档](https://www.pexels.com/api/documentation/)。

Pixabay 要求搜索结果缓存 24 小时，长期使用的图片要下载到自身服务，不能长期热链；默认限流每 60 秒 100 次。其高分辨率/矢量字段可能需要额外权限，不能假定每个 key 都有 `imageURL/vectorURL`。见 [Pixabay API 文档](https://pixabay.com/api/docs/)。

应用内候选图结果、正式页脚/来源面板和导出 credits 都展示作者与平台链接；遵守平台 API 署名约定，不把图库素材当成公共领域。Pexels 的署名说明见 [Pexels API](https://www.pexels.com/api/)。不在本期增加独立图库下载/转售功能。

### 8.3 获取、筛选、存储

1. 页计划先产出 visual intents，合并同主题意图；每节课最多 8 个实际图片搜索请求（包含双语重试与 provider 回退），每个意图最多保留 8 候选。
2. 可配置服务按 Pexels→Pixabay 顺序查询；用户选定某平台时遵循选择。无 key 时直接用本地原创图形/无图布局。
3. 先按文字相关性、尺寸/比例、平台信息过滤；可用现有视觉模型做至多 4 次候选核对，检查图意是否匹配，不推断未知人物身份。
4. 最终每页最多 2 张图、全课最多 12 张。下载有并发 2、每张 8MB、总下载 64MB 的硬上限。
5. 只接受 JPEG/PNG/WebP；检查实际魔数，用 Pillow 解码、检测尺寸上限 20MP、移除 EXIF，转为长边最多 1600px 的 WebP 或 PNG。透明图保留透明；不以扩展名决定内容类型。
6. 正式 assets 单图目标 ≤700KB、硬上限 1.5MB；整课打包内嵌图片上限 12MB，超出先降尺寸/压缩，再降图量。
7. 每个 asset 保存 sha256、尺寸、MIME、provider、provider_asset_id、source_url、creator、creator_url、license_url、fetched_at、alt、caption、role、审核结果。文件名只用服务端 ID/hash。
8. 搜索缓存是每 owner 的缓存，同 key 24h 内复用；缓存内容不含 API key，不在用户间暴露查询；用量计数在进程内全局共享，尊重同一个 key 的总限额。
9. 选中的图片随课件版本保存；未选候选缩略图/搜索缓存 24h 后可清理。已有课件显示读取本地 asset，不依赖临时 CDN URL。
10. 下载失败用同一意图下候选替代一次；仍失败则确定性改为无图布局并记 warning，不阻塞核心教学内容。

手动“换图”也是上述 pipeline，不开放任意远程图片代理。上传自有图片经既有上传限制与同一图片清洗处理，使用 owner 私有课堂 assets；不得复用公共上传裸链接。

下载 hostname 初始精确白名单为 `images.pexels.com`、`pixabay.com`、`cdn.pixabay.com`；实际结果出现其他 CDN 时标 candidate_unavailable，由管理员审核后改部署配置，不允许模型自动扩域。HTTP API 请求主机与图片下载主机使用不同白名单，重定向最多 3 跳。

## 9. HTML 展示文件、模板与隔离

### 9.1 为什么采用结构化生成

最终确实生成可独立展示的 HTML 文件，但大模型只输出内容/布局选择和有限图形数据。`LessonSpec → validate → compile_html → render_check → publish` 是唯一生产路径。这样同一份内容能支持课堂、阅读模式、离线导出，且能校验讲稿与页面是否对应。

不把 LLM 原始 HTML 放入应用 DOM；不接受模型 `<script>`、style 字符串、任意属性、URL、SVG XML 或 JS 动画。页面布局是项目内原创模板，模型通过模板 slots 表达内容。

### 9.2 五套视觉主题与九种布局

主题仅改变设计 token 和有限装饰，教学模板独立选择：

| theme id | 设计取向 | 典型用途 |
|---|---|---|
| `academic_clear@1` | 暖白背景、深蓝文字、单一蓝色强调、清楚分区 | 默认通用课程 |
| `chalk_focus@1` | 深绿/深灰板面、暖白字、浅黄重点，真实照片保留原色 | 推导、公式、板书 |
| `visual_story@1` | 大图与短标题、单独解释区域、简洁时间线 | 人文、历史、情境引入 |
| `lab_notebook@1` | 网格/图表、明确单位、步骤编号、实验观察框 | 理工、实验、数据分析 |
| `gentle_beginner@1` | 柔和浅色、较大字号、较少要点、克制插图 | 初学、小学/初中课程 |

布局枚举：`title`、`key_points`、`image_explain`、`compare`、`derivation`、`worked_example`、`timeline`、`checkpoint`、`summary`。每个布局实现固定 slots 与数量上限；禁止新加任意 layout 字符串。

基本限制：标题 ≤36 个中文字符或 12 英文词；一个列表 ≤5 项，每项 ≤40 个中文字符或 22 英文词；正文页总可见文字目标 ≤180 中文字/100 英文词（公式/图注另计）；表格最大 5×5；推导最多 5 步，超出拆页。标准画布 1280×720，安全边距 64px，页脚 20px 起。最终是否溢出以实际浏览器测量为准，不能只靠字数。

颜色从 token 白名单取值；展示正文对比度目标 ≥4.5:1。标题/正文用系统 CJK 无衬线栈，避免外网字体导致首屏闪烁；导出同时包含 KaTeX 所需本地字体和许可证。

### 9.3 受控内容块

使用 §10 的 discriminated unions。文本块由编译器做 HTML escaping；要强调的词使用 `InlineSpan(kind="text|emphasis|math")`，不是任意 Markdown HTML。公式由固定 KaTeX 版本渲染，`trust=false`、限制宏展开与大小，禁用户自定义宏/链接命令。生成的 SVG 仅来自本项目图形构造器，不接受模型提供的 XML。

首发图形：

- `flow`：最多 10 节点/12 条边，节点纯文字，服务端排布。
- `cartesian_plot`：显式有限数值点，不接收可 eval 的函数表达式；坐标轴/单位/来源必填，最多 3 series×100 点。
- `force_diagram`：位置和箭头的有限数值/枚举，标注文本转义；经过领域审核才用于推导。
- `table`：最大 5×5；数值数据有 source_id 或 `constructed_example` 标记。

已有 `quiz_illustration.py` 的出题 SVG 审核语义保持独立；可复用底层安全解析原语，但不能放松既有题图限制来服务彩色课件。

### 9.4 编译器与浏览器 runtime

- 后端新增 `app/classroom/render/compiler.py`、`blocks.py`、`themes.py`；用显式字符串模板与标准库 `html.escape`，所有 dynamic slot 按类型构造，不新增一个允许任意模板代码的语言。
- 前端新增 `src/lib/classroom/frame-runtime.ts`：独立 DOM runtime，仅接页切换、块显隐、高亮、主题/阅读模式等指令。无业务 fetch、无 token、无 localStorage。
- 用独立 `tsconfig.classroom.json` 将无模块导入的 runtime 编译为经典脚本（源码为单一 IIFE 闭包，局部类型声明，不使用全局 namespace 或 ESM import）；固定 module=none、target=ES2020、lib=ES2020/DOM、strict=true、types=[]，只 include 该 runtime 文件。新增 `scripts/build-classroom-assets.mjs` 将编译结果、固定 KaTeX dist/CSS/font 和主题 CSS 打成有 manifest/hash 的后端可读包。
- 输出至 `backend/app/classroom/static/generated/`，Git 忽略；源码与构建脚本版本化。dev、CI、production build、后端独立启动文档都执行该构建；缺包时 capability 明确 `renderer_unavailable`，不运行时从 CDN 下载补齐。
- `renderer_version` 与 `schema_version` 写入课件 manifest；打包 manifest 包含各文件 hash。原版 HTML 已保存的历史课件仍用原版可运行内容，不随最新 runtime 重新解释。

`package.json` 新增 `build:classroom = "tsc -p tsconfig.classroom.json && node scripts/build-classroom-assets.mjs"`。脚本从固定目录读取 KaTeX dist，将 CSS 的字体 URL 转为 data URI，输出 runtime/theme/assets manifest；失败退出非零。`start.sh` 当前直接调用 next build，因此必须在该调用前显式执行 build:classroom，不能只依赖不会被触发的 npm prebuild hook。生成包之外的临时 TS 编译目录也加入 .gitignore。

### 9.5 应用内 iframe 与资源鉴权

在线展示由父页面用 `apiFetch` 带 JWT 读取只包含当前 public 内容的自包含 HTML，再设置 `iframe.srcdoc`；不要给 `<iframe src>` 塞带 token 的 query。正式 HTML 的图片和字体内嵌 data URI，避免 opaque sandbox 内部无法带 JWT 获取 asset。

iframe 只授予 `sandbox="allow-scripts"`，不加 `allow-same-origin`、form、popup、top-navigation。父窗口拥有音频和互动 UI，frame 只负责画面。HTML 内 CSP：

```text
default-src 'none';
script-src 'sha256-<编译时固定 runtime hash>' 'sha256-<固定 katex hash>';
style-src 'unsafe-inline';
img-src data:;
font-src data:;
connect-src 'none';
object-src 'none'; base-uri 'none'; form-action 'none';
```

style 的 inline 放行仅供受控 CSS/KaTeX，模型不存在 CSS 输入字段。不得为修复样式而放开 script unsafe-inline。可变 JSON 内容用非可执行 data 节点并编码 `<`/`>`/`&`/U+2028/U+2029；不将用户文本嵌入脚本源码。

通信：父页面为每次 frame mount 生成随机 channel nonce；初始化用 postMessage 传一次 MessagePort。opaque frame 无可比对站点 origin，因此校验 `event.source === iframe.contentWindow`、nonce、消息 schema；后续只走持有的 MessagePort，卸载立即 close。不得仅凭 `event.origin === "null"` 信任消息。frame 只上报 ready、page_selected、layout_measurement、source_clicked；打开来源由父页面校验 source_id 后执行安全链接跳转。sandbox 原则依据 [MDN iframe](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/iframe)。

### 9.6 排版验收和导出

后端生成 worker 在发布前调用受控 Node 校验脚本 `frontend/scripts/check-classroom-render.mjs`，使用仓库已有 Playwright；将生产所需的 `playwright` 从 devDependency 调整为精确 pin 的运行依赖（与现有 `@playwright/test` 保持相同版本，当前仓库为 1.61.1）。部署时显式安装该版本 Chromium；不在用户请求中安装浏览器。

每次最多一个 headless 校验进程，超时 45s，禁止外网请求；使用固定本地文件/`setContent`，只运行已编译的可信 runtime。检查 1280×720、960×540、390px 阅读模式；等待字体与公式完成，再检测每个 block 的 scroll/client bounds、内容与页脚交叠、不可见文字和图片解码。测量报告只含 block_id/尺寸/错误码。失败先用确定性拆分/布局回退，再最多一次模型修复，仍失败保留草稿。

ZIP 结构：

```text
index.html           自包含课件及可信离线前后页控件
speaker-notes.md     每页讲稿，含来源短标记
credits.html        资料与图片署名、许可证链接
manifest.json       schema/renderer/theme 版本与文件 hash，不含 owner/token
licenses/           打包依赖所需许可证
```

不默认打包音频，避免文件暴增与未确认的云供应商导出限制。也不打包完整教材/网页、私有学习评价或正式随堂题答案。打印视图每页一张，浏览器“打印为 PDF”；不宣称所有浏览器生成完全相同的 PDF。课件源文件作为 attachment 下载，不能同源直接 inline 执行。

编译入口显式 `mode=online|offline|print`，共用相同 block/theme 渲染；offline runtime 自带前后页/目录/讲稿链接，不等待父页面 MessagePort、不尝试业务 API；online 由父页控制且无重复内嵌控制条；print 显示全部页并分页。三个产物内容 hash 指向同一冻结 spec，渲染模式和文件 hash 分别记入 manifest。

## 10. 数据模型与版本契约

所有新模型 Pydantic `extra="forbid"`，枚举闭合；业务对象、模型生成 draft 和 public DTO 分开。服务端字段（owner、状态、审核结果、hash、路径、题目答案）不能由客户端或 LLM 写入。字符串长度、数组上限在 schema 验证，不只在 UI 限制。

### 10.1 标识、时间、版本

- 新 ID：`les_/job_/run_/ast_/src_/seg_/blk_/ckp_` + 24 位随机十六进制，页 ID `s_` + 12 位；安全路径严格校验新 ID，不通过 `Path.name` 静默修正坏参数。
- 既有 workspace/student/session ID 使用原系统校验与归属加载；禁止试图将中文 workspace slug 转为新 ID 格式。
- 时间均 UTC ISO8601；展示按用户时区；`as_of` 是事实截止日期，不等于课件最后修改日。
- `schema_version=1`、`revision>=1`；内容修改新 lesson revision，播放进度修改 run `state_revision`，不要混用。
- hash 为 canonical JSON（排序 key、UTF-8、不含临时字段）的 SHA-256；bytes hash 直接计算文件 bytes。

### 10.2 实体字段

| 实体 | 必填/关键字段 | 约束 |
|---|---|---|
| Lesson | lesson_id, owner_id, workspace_id, title, created_at, updated_at, latest_ready_revision?, published_revisions[], next_revision, latest_job_id?, lifecycle | lifecycle=active/archiving/archived/purging；title≤120；只有 published_revisions 中的版本可经正式内容 API 读取 |
| LessonBrief | topic, goals[], source_selection, source_policy, research, duration_minutes, language, grade, pedagogy_id, theme_id, image_density, checkpoint_density, voice_preferences | duration 为离散 5/10/15/20/30；selection 服务端校验；voice 不放 key |
| LessonRevision | revision, schema_version, brief, source_snapshot, slides[], checkpoint_templates[], objectives[], glossary[], renderer_version, prompt_versions, review_report, content_hash, created_at | 发布后不可修改；slides 1–24；checkpoint 模板为服务端私有 |
| SourceRecord | source_id, kind, title, locator, excerpt, excerpt_hash, retrieved_at, published_at?, as_of?, verification | kind=textbook/workspace_file/session_file/web；locator 各类型独立 schema |
| AssetRecord | asset_id, sha256, mime, width, height, provenance, alt, caption, role, bytes, status | bytes≤1.5MB；status=ready/unavailable；物理路径不返回前端 |
| GenerationJob | job_id, owner_id, workspace_id, lesson_id, base_revision?, target_revision, state, phase, state_revision, brief_hash, stage_inputs, artifacts, budget, attempts, cancel_requested, last_error?, next_retry_at?, epoch | 详见 §15；任何 phase 变化都先落盘 |
| SlideSpec | slide_id, order, title, learning_objective_ids[], layout, blocks[], segments[], claims[], source_ids[], transition, estimated_seconds | blocks≤16；segments≤12（切分后上限 24）；transition=auto/manual；引用必须存在 |
| NarrationSegment | segment_id, role, display_text, spoken_text, show_block_ids[], focus_block_ids[], pause_after_ms, source_ids[], estimated_ms | spoken_text 1–240；动作只引用本页 block |
| CheckpointTemplate | checkpoint_id, slide_id, kind, prompt, verified_question_template?, reflection_seconds, optional | kind=reflect/question；question 私有含答案/量规，需沿既有出题审核 |
| ClassroomRun | run_id, owner_id, workspace_id, lesson_id, lesson_revision, content_hash, status, state_revision, cursor, checkpoint_refs, audio_profile, qa_session_id?, lease, visited_slides[], listened_segments[], created_at, updated_at | 固定 revision；播放完成和评价状态分开 |
| AudioClip | clip_id, owner_id, lesson_id, revision, kind, content_ref, segment_id, chunk_index, synthesis_key, provider, voice_id, language, sample_rate, sample_count, bytes, sha256, state, error? | kind=narration/qa/feedback；content_ref 分别定位公开讲稿/已保存回复/已受理或揭晓反馈，不接任意正文 |
| ExportJob | job_id, lesson_id, revision, format, state, artifact_id?, expires_at | format=html_zip/notes_md；下载仍验证 owner |

`SourceRecord.locator`：教材/文件为 `{namespace,file_id,chunk_ids,page?,printed_page?,section_path,content_hash}`；网页为 `{url,canonical_url,domain,publisher?,retrieved_at}`；不得把模糊字符串“第十页附近”当正式页码。

### 10.3 Block 判别联合

```typescript
type InlineSpan =
  | { kind: "text" | "emphasis"; text: string }
  | { kind: "math"; latex: string; spoken: string };

type SlideBlock =
  | { id: string; kind: "paragraph"; spans: InlineSpan[] }
  | { id: string; kind: "bullets"; items: InlineSpan[][] }
  | { id: string; kind: "formula"; latex: string; spoken: string; label?: string }
  | { id: string; kind: "image"; asset_id: string; alt: string; caption: string; fit: "contain" | "cover" }
  | { id: string; kind: "table"; headers: string[]; rows: string[][]; source_ids: string[]; constructed: boolean }
  | { id: string; kind: "steps"; steps: { label: string; spans: InlineSpan[] }[] }
  | { id: string; kind: "diagram"; diagram: DiagramSpec }
  | { id: string; kind: "checkpoint"; checkpoint_id: string }
  | { id: string; kind: "callout"; tone: "note" | "warning" | "summary"; spans: InlineSpan[] };
```

`DiagramSpec` 定义固定为下列联合；所有数值必须 finite，API 文档不能留下 `dict[str,Any]` 当最终契约：

```typescript
type DiagramSpec =
  | { type: "flow"; direction: "horizontal" | "vertical";
      nodes: { id: string; label: string }[];
      edges: { from: string; to: string; label?: string }[]; alt: string }
  | { type: "cartesian_plot"; x_label: string; y_label: string;
      x_range: [number, number]; y_range: [number, number];
      series: { label: string; points: [number, number][] }[];
      source_ids: string[]; constructed: boolean; alt: string }
  | { type: "force_diagram";
      bodies: { id: string; shape: "point" | "box"; x: number; y: number; label: string }[];
      arrows: { body_id: string; dx: number; dy: number; label: string }[];
      source_ids: string[]; constructed: boolean; alt: string };
```

flow 的节点引用闭合、无重复 ID，首发仅支持 DAG；plot range 严格递增、坐标绝对值≤1e9，超出 range 的点拒绝而非无提示裁切；force 坐标为 0–1 画布相对值、箭头分量 -1–1，最多 4 bodies/8 arrows，不以箭头屏幕长度冒充实际力大小。alt≤500 字，节点/箭头 label≤40 字。数值和图形语义需与课程依据一致。

`TeachingClaim={claim_id,text,kind,block_ids[],segment_ids[],source_ids[],as_of?}`；kind=textbook_fact/web_fact/author_explanation/constructed_example，每页≤12 条、text≤600 字。前两种 source_ids 不能为空并匹配来源 kind；模型不得设置 verification。正文事实与讲稿事实都须覆盖到 claim target；确定性检查只保证引用闭合，独立 review 再检查内容支持。`estimated_seconds` 由服务端计算；模型可建议但不权威。

### 10.4 一页讲稿示例（格式示意，发布前补齐正式 ID/依据）

```json
{
  "slide_id": "s_012345abcdef",
  "order": 3,
  "title": "动量守恒有一个前提",
  "learning_objective_ids": ["objective_2"],
  "layout": "derivation",
  "blocks": [
    {"id":"blk_111111111111111111111111","kind":"formula","latex":"\\Delta \\vec{P}=\\int_{t_1}^{t_2}\\vec{F}_{\\mathrm{ext}}\\,dt","spoken":"系统总动量的变化，等于这段时间内外力的总冲量。"},
    {"id":"blk_222222222222222222222222","kind":"callout","tone":"warning","spans":[{"kind":"text","text":"先选系统，再判断外力冲量能否忽略。"}]}
  ],
  "segments": [
    {
      "segment_id":"seg_111111111111111111111111",
      "role":"explain",
      "display_text":"先把两个碰撞的小车看成一个系统。它们之间的相互作用是内力，不能把某一辆车单独受到的力直接当成整个系统的外力。",
      "spoken_text":"先把两个碰撞的小车看成一个系统。它们之间的相互作用是内力，不能把某一辆车单独受到的力直接当成整个系统的外力。",
      "show_block_ids":["blk_111111111111111111111111"],
      "focus_block_ids":["blk_111111111111111111111111"],
      "pause_after_ms":500,
      "source_ids":["src_111111111111111111111111"],
      "estimated_ms":19000
    },
    {
      "segment_id":"seg_222222222222222222222222",
      "role":"misconception",
      "display_text":"动量守恒并不要求每个物体都不受力。我们要检查的是系统受到的合外力，或者碰撞这段很短的时间里外力冲量是否可以忽略。",
      "spoken_text":"动量守恒并不要求每个物体都不受力。我们要检查的是系统受到的合外力，或者碰撞这段很短的时间里外力冲量是否可以忽略。",
      "show_block_ids":["blk_222222222222222222222222"],
      "focus_block_ids":["blk_222222222222222222222222"],
      "pause_after_ms":800,
      "source_ids":["src_111111111111111111111111"],
      "estimated_ms":19000
    }
  ],
  "claims":[
    {"claim_id":"claim_1","text":"系统总动量的变化等于合外力的冲量。","kind":"textbook_fact","block_ids":["blk_111111111111111111111111"],"segment_ids":["seg_111111111111111111111111"],"source_ids":["src_111111111111111111111111"]},
    {"claim_id":"claim_2","text":"判断动量守恒应先选系统，再判断合外力冲量。","kind":"textbook_fact","block_ids":["blk_222222222222222222222222"],"segment_ids":["seg_222222222222222222222222"],"source_ids":["src_111111111111111111111111"]}
  ],
  "source_ids":["src_111111111111111111111111"],
  "transition":"auto",
  "estimated_seconds":39
}
```

上例重点是讲稿解释页面没有写出的系统选择与误区；真实课程每个 src 必须绑定检索到的有效材料，不能直接复制示例占位 ID。

### 10.5 Public 投影

`LessonPublic` 包含展示 blocks、普通讲稿、短 source metadata、已发布版本、可用操作；不含答案、rubric、critic 原文、供应商请求体、密钥、内部路径或完整私有检索摘要。

`CheckpointPublic` 通过既有 `TaskSnapshot.public_view()` 生成。按 `run_id` 实例化的新题引用与 lesson 模板分开；播放器初始 HTML、讲稿和预取音频都不能含未揭晓答案。

`LessonEditor` 不返回随堂答案，也不提供绕过 run 的答案端点；需要查看时进入课堂题卡走 reveal 并记录帮助，避免预览课程后仍把同一题算成独立作答。

## 11. 云端优先 TTS 与本地兼容

### 11.1 与现有电话模式的关系

保留 `VOICE_TTS_PROVIDER=off|stub|melo` 的原有含义，同时新增可选 azure/auto；不在升级时强行打开旧电话按钮。课堂独立配置 `CLASSROOM_TTS_POLICY=auto` 和 `CLASSROOM_TTS_CLOUD_PROVIDER=azure`。

课堂有效选择顺序：本次 run 显式选项 > 个人课堂偏好 > 实例课堂默认。auto：可用且已配置云端 → 已启用/可用本地 → silent；local：仅本地→silent；cloud：云端，只有 `allow_local_fallback=true` 才转本地。无配置显示“文字课堂”，不是伪装成本地语音成功。

新增 `voice/tts/service.py` 统一 provider 的配置、能力、健康与并发；原 `get_tts_provider()` 无参接口保持可用，调用 service 获取旧电话配置。课堂通过 `resolve_classroom_tts(profile)` 获取不可变 profile，不能修改全局 factory 实例的音色影响其他用户。

### 11.2 Provider contract 的兼容扩展

```python
@dataclass(frozen=True)
class TTSOptions:
    voice_id: str = ""
    language: str = "zh-CN"
    synthesis_speed: float = 1.0

@dataclass
class TTSResult:
    pcm16: bytes
    sample_rate: int
    provider: str = ""
    voice_id: str = ""

async def synthesize(text: str, *, speed: float | None = None,
                     options: TTSOptions | None = None) -> TTSResult: ...
```

旧 `speed` 调用继续生效；未传 options 时保留原行为。Melo/stub 同步接受新关键字，不使用的字段忽略或按 capability 校验；输出仍为单声道 PCM16，电话 WS 不变。

Provider 必须返回 `capabilities={languages,voices,speed_range,has_word_boundaries,max_text_chars}`。首发 Azure REST 与 Melo 都按段级同步实现，`has_word_boundaries=false`，不能凭模型估计字级时间戳。

### 11.3 Azure 首发适配

管理员配置 `AZURE_SPEECH_KEY`、`AZURE_SPEECH_REGION`；可选受限的官方 resource endpoint，普通用户不能设置 base URL。默认区域地址由 region 生成 `https://<region>.tts.speech.microsoft.com/cognitiveservices/v1`。需要自定义域时按官方 resource endpoint 使用固定路径，服务端校验 HTTPS 与批准域。

请求头使用 `Ocp-Apim-Subscription-Key`、`Content-Type: application/ssml+xml`、`X-Microsoft-OutputFormat: riff-24khz-16bit-mono-pcm`、User-Agent。后端构造转义后的 SSML，只允许 speak/voice/prosody/break 的受控结构，模型不能传原始 SSML。复用 `wav_to_pcm16` 与 `normalize_pcm16`，CPU 后处理移出事件循环。官方字段与音频格式见 [Azure TTS REST](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-text-to-speech)，SSML 结构见 [SSML 文档](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-structure)。

部署时调用官方 voices list 验证允许音色；默认候选 zh-CN-XiaoxiaoNeural、en-US-JennyNeural，仅在当前资源支持时启用。没有候选则根据 locale 选第一个管理员批准音色，否则报 voice_unavailable；禁止静默以中文音色硬读整页英文。可用音色列表只返回 ShortName/语言/展示名，不返回供应商内部凭证信息。

不硬编码价格、不承诺免费；按实际请求字符数/次数记录使用量，费用显示只有管理员提供单价时才估算。后续添加其他云供应商只增加 adapter 与配置，不更改 LessonSpec/播放协议。

### 11.4 合成、缓存、预取

- 发布课程只做文字与排版校验，不自动合成整课。用户点击开始/试听后才 POST 请求音频；GET 音频/状态不会产生新计费。
- 预取窗口为“当前段+后 2 段”，最多跨下一页且不能跨未解决 checkpoint；每个 run 最多 1 个新合成在途。
- 云端全局并发默认 2，Melo 全局并发必须为 1，覆盖旧电话与课堂共同请求。当前电话代码的单连接 worker 不等于跨用户全局保护，此处必须补齐共享 semaphore。
- 当前播放优先于试听/后续预取；合成队列有上限 32，满时返回可重试 audio_busy，不能无限堆积长课。
- 基准合成速度固定 1.0；课堂个人 `tts_speed` 解释为播放倍速，使用 `<audio>.playbackRate` + `preservesPitch`；该改变不重新计费。旧电话仍按原 synthesize(speed=个人值) 运行。
- 统一把 PCM 打包 WAV 后落盘，24kHz 16-bit 单声道每分钟约 2.9MB；15 分钟约 43MB，因而不能无限保留。采样率必须读取实际返回值，Melo 不强制假定 24kHz。
- synthesis key = owner + hash(spoken_text) + provider/version + voice + language + synthesis_speed + speech_normalizer_version；不含 playbackRate。保存内容 hash，不能仅用 slide index 作 key。
- 讲稿改动只使相关段缓存失效；换视觉主题且讲稿不变可复用同 owner 音频。跨账号不共享有内容的音频缓存。
- 音频文件用临时文件+fsync+replace，metadata 最后提交；相同 key 用 single-flight 合并请求，下载和完整性校验通过后才 ready。
- 暂停保留已缓冲音频但停止新的预取；跳页撤销无关等待者。外部调用可能已执行，返回后可缓存但不能把声音送入旧播放队列。

### 11.5 错误处理

连接超时 5s、整段云端请求 25s、本地 60s；401/403 不重试，标配置错误；429 遵守 Retry-After 并纳入总 deadline，单次自动重试；5xx/网络异常最多重试一次，仍失败按 run policy 回退。

已开始播放的段禁止中途切音色；云端在未播段失败时，将该 run 的后续音色锁为本地，提示一次“已切换本地语音”。未启用/不支持该语言的本地回退变文字模式。用户显式“重试云端”只在暂停/段边界生效；不每段在两家间来回跳。

回退 clip 的 metadata 与 synthesis key 必须改为实际本地 provider/voice，不得把 Melo 音频缓存到 Azure key 下。音色切换提高 audio_profile_version，并在安全边界递增 playback_epoch、撤销旧预取；改变播放倍速不改变 audio_profile_version。W/voice-preview 的固定试听音频保存在 owner 下独立 voice-previews 缓存，纳入同一 LRU/删除/配额体系，不需要创建虚假 lesson/run。

浏览器必须由用户交互启动媒体，捕获 play() 拒绝并显示“点击继续播放”；不能把 autoplay 拒绝当网络故障。约束见 [MDN 自动播放](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Autoplay)。课堂只接收服务端 WAV bytes，经 `apiFetch` 创建 Blob URL，再交给复用的两个 audio 元素播放；卸载/切账号时撤销所有 Object URL。

### 11.6 本地 sidecar 的运行保障

`start.sh::start_voice_sidecar` 在以下任一条件成立时启动：旧电话 provider=melo；旧电话 auto 且允许 melo；课堂启用且 local enabled，且默认策略 local/auto 或允许本地回退。缺 venv/模型时提示并保持文字课堂，不临时安装大型模型。

systemd 部署增加/更新 sidecar unit 与启动说明，不能只修 start.sh。保留当前 CPU-only、固定 revision、HF 离线与许可证策略。健康检查仅报告 configured/ready/degraded，不在每次 GET capability 时合成音频。

## 12. 播放状态机与问答恢复

### 12.1 状态分层

后端 `run.status` 仅保存 `active/paused/completed/ended`；浏览器临时状态更细：

```text
loading → ready → buffering → playing
                       ↘ unavailable_audio → paused_text
playing → paused → playing
playing → asking → answering → paused（显示“继续原课”）
playing → checkpoint → paused/playing
playing → completed
任意可运行态 → suspended（断网/后台/lease 丢失）→ paused
任意态 → ended（明确结束）
```

页面卸载、音频错误、abort 都不得悄悄转成 completed。服务端读取过期 active lease 时投影为 paused；不要 GET 时顺便写状态文件。

### 12.2 游标与播放时钟

```typescript
type Cursor = {
  slide_id: string;
  segment_id: string;
  chunk_index: number;
  offset_ms: number;
  last_completed_segment_id: string | null;
};
```

offset 是该 chunk 的媒体时间；播放倍速改变时媒体时间仍来自 `audio.currentTime`。字幕、高亮、段结束依赖实际 audio 事件与媒体时钟；`setTimeout` 只能做短停顿，不推算长音频完成。音频 ready 只是可播放，不触发翻页。

播放控制统一 `useClassroomPlayer` reducer + 独立 `ClassroomAudioController`，generation token 为 `{run_id, lesson_revision, lease_epoch, playback_epoch}`。每次跳页/切段/打断/离开使 playback_epoch 递增；HTTP 迟到音频、旧 `ended`、旧 SSE、旧问答完成事件如果 token 不匹配直接丢弃。

同页暂停继续保留 currentTime；插问后默认回到被打断段开头；刷新恢复也从该段开头，最多重复约 30 秒。用户手动拖动段内进度可恢复 offset，值须夹在音频实际时长内。

### 12.3 进度持久化

- 每 5 秒节流保存一次；段结束、跳页、暂停、checkpoint 提交、退出时立即 flush。
- 请求携带 `client_event_id`、`expected_state_revision`、`lease_epoch`、cursor、action、有限的已播放段增量。服务端验证段存在于固定 revision，offset 不越界，事件 ID 去重；重试返回原接受结果。
- 更新执行 load→CAS→merge→atomic write，同步短临界区；同一客户端串行发送进度，合并尚未发送的高频 tick，禁止多个 PUT 自己互相制造冲突。
- client_event_id 的有限去重表保存最近 256 个事件；低于已接受序号的旧事件可确定性忽略。音频 epoch 与状态 revision 不能由前端任意降低。
- pagehide 用已带身份的 `apiFetch(...,{keepalive:true})` best effort，小于 8KB；不能以不带 JWT 的 sendBeacon 代替。正常 5 秒 checkpoint 才是主要可靠机制。
- “完成”要求用户抵达最后一页并结束最后一段或显式选择文字阅读完成；跳到末页时记录 skipped_slides，文案为“浏览完成”而非“完整听完”。
- listened/visited 是用户体验统计，可被客户端影响，永不作为高可信掌握证据或考试凭证。

### 12.4 问答接入现有聊天

第一条课堂提问才创建普通本人 chat session，标题 `课堂答疑 · <课程名>`，绑定相同 workspace，并在 run 中存 `qa_session_id`。课件生成、播放本身不创建空白聊天或伪造学生消息。

首次 session 的 grade、默认 output_language 从冻结课程 brief 继承；显式用户翻译/切换讲解语言仍遵循现有语言策略。课堂课程语言与应用界面语言分开，不因用户打开英文 UI 就把中文课程改为英语。

扩展 `schemas/chat.py::ChatRequest` 的可选 `classroom_ref={run_id,lesson_revision,slide_id,segment_id?}`；普通调用缺省 None，旧客户端保持兼容。`api/v1/chat.py` 在执行之前验证：

1. run 属于当前身份且固定 revision 匹配，课堂/工作区未归档。
2. 目标 session 等于 run 的 qa_session_id；首次创建须在 run 锁内预留 session ID，并用可恢复操作记录完成绑定。
3. 指定 slide/segment 存在；读取其公开讲稿、可见块、前置已讲摘要、来源记录。不能读取未揭晓的 checkpoint 答案。
4. 检索 overlay 限于当前仍授权的 lesson 来源；历史撤销来源只有既有派生快照可解释，不重新取原文。
5. 构造服务端 `ClassroomTurnContext`，作为新的可选 keyword-only 参数传入 `run_turn`、dispatch、supervisor.run、legacy chat_turn；各路径都通过同一格式化 helper 加入受边界标记的材料区。

客户端不传完整 slide JSON、系统提示词或答案；用户消息原文保持原样，不能把整页讲稿拼成“学生说了……”再送入 learning evidence。必须测试 v2、legacy、显式 fallback 和 `run_turn` finally 的 dialogue receipt 钩子。

将 `_build_tools` 的公共构建逻辑提取至 `core/agent_tools.py`；`api/v1/chat.py::_build_tools` 保留薄 wrapper 及现有 patch 点，课堂复用服务而非从路由内部复制。增加可选 trusted source override，仅内部构造，普通聊天行为不变。

插问保持现有 `POST /chat/stream` 事件协议；课堂 UI 只消费 answer/tool status/done/error，过滤 thinking。默认不自动触发额外随堂题，除非学生明确要求练习；通过课堂 turn policy 对齐两种 supervisor 模式。

### 12.5 问答声音与恢复

回复文字实时显示；首发在该次 answer 完成并持久化后按句切片合成，不把整轮聊天 WS 引入课堂。通过 `POST /runs/{run}/qa-audio` 引用服务端保存的 reply message ID；服务端核对 session/run/owner 后读取正文，不接受任意客户端 text。

用户再次提问时立即停止上一回复声音并保存原课堂 resume anchor；只有最初的原课堂 anchor 被保留，不能被每次追问覆盖。问答结束保持暂停，主按钮“继续原课”，副按钮“从本页开始”。

语音输入可复用/抽取 `useVoiceCall.ts` 中的浏览器识别适配器，但不调用 voice.start、不开旧电话黑板；默认按住说话，最终转写先展示可修订文本，再发送。浏览器不支持时保留文字输入。不偷偷录音或增加后台识别。

### 12.6 快捷补讲与笔记

“没听懂”发送真实用户操作表达，如“请用更直观的方式解释刚才这一段”，附课堂 ref；“举个例子”同理。回复是正常 dialogue，不把用户点击理解为“掌握”。“这一页太快”只暂停并把倍速降低一级，不调用模型。

“记下这里”保存 slide/segment 定位、当前可见内容和用户补充，可立即落本 run 笔记；“保存到笔记中心”走后端 `core/notes.py` 的正式写路径并生成普通私有笔记。自动摘录与用户手写要区分，不能伪装成学生总结。

## 13. 随堂练习、学习证据与课后回顾

### 13.1 两种检查点

- `reflect`：思考/观察问题，可选 15–60 秒建议时间，支持“继续/再想一会儿”，不自动判分、不写学习证据。
- `question`：正式题卡，复用已有 M4 出题审核、冻结量规、提示/揭晓/作答受理及结果投影。

默认每 5–8 分钟最多一个正式检查点；5 分钟 0–1，10 分钟 1，15/20 分钟 2，30 分钟最多 3；“不出题”优先。不得在讲稿中再偷偷生成同一题的答案和解析。

首发检查点只在其所属页的最后一段结束后触发；需要“先思考再讲解”的教学顺序时拆成思考页和讲解页，不在段内插入尚未定义的跳转程序。每页最多一个 checkpoint；用户关闭自动翻页不跳过检查点。

### 13.2 生成与实例化

1. 备课时根据本课实际教授的概念/教材证据，通过现有 `GenerateQuizTool` / `generate_verified_questions` 生成题目模板；必须保留现有 critic、grounding、SVG permission 语义，不写新的一次模型直出评分器。
2. 模板存在服务端 revision 私有材料中；课程可以声明“随堂题准备失败，本课为讲授模式”，在检查点为 optional 的情况下发布降级版本；用户明确要求必需题目时不得自动略过。
3. 创建 run 时将每个模板确定性实例化为 run 独立 question_id（由 owner+run+checkpoint+template_hash 导出稳定 ID），冻结 TaskSnapshot。注册必须幂等且在第一次公开题干前完成。
4. 同一 run 恢复复用同一 question ID、revision 与已受理状态；“重新上一遍”新 run 若复用题面，关联 `origin_question_ref` 和 task_family。通过 owner 内 `template_hash→question_ref` 索引（可由课堂 run 与 journal 重建）记录 prior_exposure；旧题有 answer_revealed/worked_example 时对新题同样记录对应帮助事件，不能仅有 origin_ref 就假定 evaluator 自动知晓。重复同题不得被当作独立新题证据。
5. 题卡 source_session_ref 可以为空；服务端传可信 workspace_id 给 `evaluate_submission`，保持 SourceKind.ASSESSMENT。Q&A session 不需要为注册题卡提前创建。
6. 初始化跨课堂 store/journal 的流程用 run initialization op：先持久化 planned question IDs → 幂等 register → 标 ready。崩溃后重复 register，不生成新 ID。

### 13.3 正式提交与帮助

课堂题卡由外层 React 渲染，iframe 只含 checkpoint 占位。复用 `QuizQuestionCard` 的展示，抽取 `useQuizSubmission` 并允许注入 transport（submit/get/hint/reveal）；原 chat 默认 transport 保持原 API，课堂 transport 额外校验 run/checkpoint。

课堂 submit 只接 `student_answer`、question_ref、idempotency_key、expected_scope_revision；不接客户端 correct_answer、判分结果、concept_refs 或帮助级别。受理调用 `evaluate_submission(source_surface="classroom",...)`，返回既有 `SubmissionReceipt`/`QuizSubmitOutcome` 形状和 202，前端有限轮询已受理结果。

提示/揭晓调用现有 `record_assistance`；必须先持久化帮助事件成功，再把答案/提示返回。想通过缓存、导出、讲稿、预取音频提前取答案也必须经过相同规则。正式问题反馈音频根据已受理/已揭晓记录派生，并经过 owner/run 绑定校验。

在未提交的正式检查点中打开自由问答，服务端从 run 得到 active_checkpoint，不信任客户端自报“与题无关”。先记录 hint_requested；首发对此期间的 AI 补讲保守记 worked_example，反馈进入 full_demo 条件，UI 标“在讲解帮助后作答”。需要独立验证时生成新的迁移题。否则用户在侧栏问出答案后回到题卡提交，会绕开帮助记录。该记录只作用于当前正式题，不把整节课的概念讲授一律算作题目答案揭晓。

检查点答后显示“本题反馈”和“学习评价”两层。长期语义评价未完成时保留 pending，不阻止继续上课；答案已受理立即锁定，不能因 verdict=null 解锁重新提交。同题不同答案的冲突遵循现有 409 语义。

### 13.4 范围与关闭开关

M2 关闭：保存课堂体验进度，题目判分按当前 disabled/task-only 路径运行，不能伪造评价完成。M3/M5/M8/M9 关闭：使用显式 brief、教材文本与固定模板降级。课堂发布内容本身不依赖所有智能层必须在线。

工作区所选教材/概念 scope 在上课中变更：内容仍显示固定 revision；提交时根据当前权威 scope 校验。若 expected_scope_revision 变化，返回 scope_changed，前端刷新范围；不能把旧教材概念强塞入新范围，必要时只提供 task-only 反馈。

### 13.5 课后回顾

展示实际看过页数、听过段数、提问列表、检查点已答/跳过/待评价、待复习点，分别标明数据来源。未作答时显示“尚未通过作答确认理解”，不是“掌握 90%”。

提供“回看这一页”“继续提问”“保存课堂笔记”“做一道新的迁移题”。课堂笔记默认由本课公开内容与用户笔记确定性汇总，不额外调用 LLM；用户要求润色再调 notes agent。

课程完成可作为已有学习编排任务的活动完成信号，但掌握/独立召回仍只由评价 outbox 驱动；不直接调旧 `record_quiz_evidence` 再写一遍评价。不自动向 global prompt memory 写入完整讲稿或未经验证的能力结论。

## 14. HTTP API、事件及错误协议

统一前缀 `/api/v1`，以下简写：

```text
W = /workspaces/{workspace_id}/classroom
L = W/lessons/{lesson_id}
R = L/runs/{run_id}
J = W/jobs/{job_id}
```

后端新增 `api/v1/classroom.py` 和 `schemas/classroom.py`，在 `api/v1/router.py` 注册；复杂工作由 service 执行，route 只做身份、schema、状态码投影。所有 ID 都需验证完整 owner→workspace→lesson→revision/run/job 链，不能查到同 owner 的另一工作区资源就直接放行。

### 14.1 能力、课程和生成任务

| 方法/路径 | 请求 | 响应/副作用 |
|---|---|---|
| GET `/classroom/capabilities` | 无 | enabled、renderer、research/images/tts 的 configured/available/reason、允许音色与限制；不触发外部计费 |
| GET `/classroom/templates` | lang? | 5 教学+5 视觉模板元数据、默认值、版本；不回全部 prompt |
| GET `W/lessons` | page≥1,page_size=5(≤20),status? | items,total,page,page_size；只读索引，不启动作业 |
| POST `W/lessons` | brief,start_mode=automatic/outline_first；Idempotency-Key | 202 `{lesson_id,job_id,target_revision,status_url,events_url}`；重复同 key+body 返回同结果 |
| GET `L` | revision? | LessonPublic、最新任务摘要、最近 run；未完成时返回 brief/progress，不伪造 slides |
| GET `L/revisions` | page,page_size | 版本号、时间、来源变化、可用性；不附全量 HTML |
| POST `L/revisions` | base_revision,operation；Idempotency-Key | 202 新 revision 作业；operation 判别联合见下文 |
| GET `L/revisions/{rev}/frame` | mode=presentation/reading | 受鉴权自包含 HTML 文本；`Cache-Control: private,no-store` |
| DELETE `L` | 无 | 202 归档操作 ID；完成后课程从列表消失并进回收站 |
| GET `J` | 无 | 完整任务 snapshot、state_revision、phase、warnings、next actions |
| GET `J/preview` | slide_id? | 只读草稿 public DTO；已有安全编译结果时可附草稿 HTML，否则仅展示结构化预览；含 draft 水印，无答案 |
| GET `J/events` | Last-Event-ID? 或 after_revision? | 认证 fetch SSE，发 snapshot/heartbeat/terminal |
| POST `J/cancel` | expected_state_revision | 202 cancel_requested；终态返回 200 当前状态；不新增调用 |
| POST `J/retry` | expected_state_revision；Idempotency-Key | 明确可重试阶段再次排队，保留已校验阶段产物与目标 revision |
| PATCH `J/outline` | expected_state_revision,outline | 仅 awaiting_outline/needs_input，200 新 snapshot |
| PATCH `J/brief` | expected_state_revision,brief_patch | 仅 failed/needs_input/awaiting_outline，白名单修改 §4 用户字段；重算依赖 hash、失效受影响阶段；不修改 owner/lesson/target_revision |
| POST `J/continue` | expected_state_revision | 审核大纲后排队；权限/来源变更先重新 resolve |
| POST `W/image-search` | lesson_id,visual_intent,provider?；Idempotency-Key | 搜索候选 metadata，受同一预算/隐私规则；只用于换图 |
| POST `L/assets` | multipart file | 自有图片清洗后 AssetPublic；不接受 SVG/HTML |
| GET `L/assets/{asset_id}/content` | 无 | 图片 bytes；归属+引用链校验，供父页面认证 fetch 预览 |
| GET `W/operations/{operation_id}` | 无 | 归档/恢复/笔记等可恢复操作状态；不暴露原始路径 |
| POST `W/voice-preview` | language,voice_preferences；Idempotency-Key | 返回固定、非用户私有试听句的 WAV；最多 40 汉字或 30 英文词，25s deadline，可缓存；不接任意 text |

`operation` 为 `edit_content{changes[]}`、`change_theme{theme_id}`、`regenerate_slide{slide_id,instruction}`、`replace_image{slide_id,block_id,candidate_id|asset_id}`、`refresh_research{scope}` 五者之一。edit changes 仅允许替换字段、删除页、有序 page_ids；禁止任意 JSON Patch 路径。单次最多 50 项，校验目标完整性。candidate_id 是服务端短期签发/保存的候选引用，不能由客户端拼图片 URL。

### 14.2 课堂 run、音频、检查点

| 方法/路径 | 请求 | 响应/副作用 |
|---|---|---|
| POST `L/runs` | lesson_revision,mode=resume_or_create/restart,voice_preferences；Idempotency-Key | 201 新 run 或 200 已有未结束 run；初始化题目但不合成音频 |
| GET `R` | 无 | 固定课件 revision、cursor、检查点公开状态、lease 摘要与可恢复状态 |
| POST `R/lease` | client_id,takeover=false | 200 lease_epoch,expires_at；占用且未接管 409 |
| PUT `R/lease` | client_id,lease_epoch | 续期；15s heartbeat，45s TTL |
| DELETE `R/lease` | client_id,lease_epoch | 释放并暂停；旧 epoch 不影响新 lease |
| PUT `R/progress` | expected_state_revision,client_event_id,client_seq,lease_epoch,action,cursor,played_delta | 200 新 state_revision；CAS 冲突 409；action 仅 progress/pause/complete/end |
| POST `R/audio` | segment_ids[1..3],lease_epoch；Idempotency-Key | 202 `{clips:[{clip_id,state,status_url,content_url?}]}`；仅允许当前/合法预取范围 |
| GET `R/audio/{clip_id}` | 无 | 状态与真实时长、provider/voice；GET 不合成 |
| GET `R/audio/{clip_id}/content` | 无 | audio/wav；通过 apiFetch 拉完整小片段，首发不要求 HTTP Range |
| PUT `R/audio-profile` | expected_state_revision,voice_preferences | 暂停/段边界生效，产生新的 profile version，已播放进度不变 |
| POST `R/qa-session` | 无；Idempotency-Key | 201/200 `{session_id}`；只在首次提问时调用 |
| POST `/chat/stream` | 原 ChatRequest + classroom_ref | 保持现有 SSE；服务端校验/构造上下文 |
| POST `R/qa-audio` | reply_message_id,lease_epoch；Idempotency-Key | 202 回复音频段列表；仅限本 run 答疑 session 的已保存 assistant 回复 |
| GET `R/checkpoints/{cid}` | 无 | QuestionPublic + 已受理状态或 reflect；不含未揭晓答案 |
| POST `R/checkpoints/{cid}/submit` | question_ref,student_answer,idempotency_key,expected_scope_revision | 202 既有受理结果；确定性已完成可返回同样 shape 的 200 |
| GET `R/checkpoints/{cid}/submission` | 无 | 只读现有 quiz_submission；未提交 null |
| POST `R/checkpoints/{cid}/hint` | 无；Idempotency-Key | 先持久化 assistance，再返回提示 |
| POST `R/checkpoints/{cid}/reveal` | 无；Idempotency-Key | 先持久化帮助，再返回答案/解析；允许后续反馈音频 |
| POST `R/checkpoints/{cid}/feedback-audio` | lease_epoch；Idempotency-Key | 202；只有已受理有反馈或已揭晓时允许，正文由服务端取，不接客户端答案文本 |
| POST `R/checkpoints/{cid}/skip` | expected_state_revision | 200 skipped；不伪造作答，不触发评价 |
| POST `R/notes` | slide_id,segment_id?,user_text≤4000 | 201 run annotation；不自动写个人长期评价 |
| POST `R/save-note` | title?,include_user_notes=true；Idempotency-Key | 201/200 既有 notes 系统 note_id |
| GET `R/summary` | 无 | 确定性课堂回顾，评价仍消费统一投影 |
| POST `L/exports` | revision,format=html_zip/notes_md；Idempotency-Key | 202 export job；不合成音频 |
| GET `L/exports/{export_id}/content` | 无 | 校验 owner 后 attachment；到期返回 410，可重建 |

音频轮询退避 0.5/1/2 秒、最长 60 秒并允许取消，后台暂停轮询；超过仅停止等待，服务端作业状态仍可恢复。run 读侧最少化 full lesson 重复返回，避免每次 5 秒存进度都传整份课件。

### 14.3 幂等与错误

创建类操作要求 `Idempotency-Key`（16–128 可打印安全字符）。服务端以 owner+endpoint scope+key 定位，并比较 canonical body hash；同 key 不同 body 返回 409 idempotency_conflict。映射至少保留 7 天，课程仍在时创建原始 key 与 lesson/job 同寿命，避免延迟重试生成第二份。清理按账号删除一起清理。

错误 envelope 固定：

```json
{
  "error": {
    "code": "source_changed",
    "message": "所选教材已变化，请重新确认来源。",
    "retryable": false,
    "phase": "resolve_sources",
    "request_id": "req_..."
  }
}
```

404 对不存在/他人资源统一；409 用于 revision/lease/scope/状态冲突；422 schema/教材不足/语言不可用；429 配额与排队已满（Retry-After）；503 功能/渲染器/服务不可用。网络错误不得直接把第三方 response body/key/base URL 回显给学生。

必须定义的 code：`classroom_disabled`、`source_not_ready`、`source_not_found`、`source_changed`、`research_unavailable`、`freshness_unverified`、`image_unavailable`、`content_invalid`、`layout_overflow`、`renderer_unavailable`、`budget_exceeded`、`quota_exceeded`、`generation_failed`、`job_cancelled`、`revision_conflict`、`lease_conflict`、`scope_changed`、`audio_busy`、`tts_unavailable`、`voice_unavailable`、`export_expired`、`storage_unavailable`。

### 14.4 SSE 重连契约

浏览器不使用不能设置 Authorization 的原生 EventSource，而是 `apiFetch` + `ReadableStream`。GET SSE 显式传 AbortSignal，避开 apiFetch 默认 30 秒 GET timeout。每 15 秒 heartbeat，断开后指数退避 1/2/4/8 秒、上限 15 秒，离开页面立即 abort。

事件形式：

```text
id: 17
event: snapshot
data: {"job_id":"job_...","state_revision":17,"state":"running","phase":"author_slides","completed_slides":4,"total_slides":10,"warnings":[]}
```

事件 ID 就是持久化 snapshot revision；每次连接先发送当前完整 snapshot。只承诺最新状态，不承诺逐条重播全部历史阶段，因此无需新增无限增长的进度 event journal。客户端忽略 ≤当前 revision 的旧消息。终态发送 terminal snapshot 后结束；SSE 断开不会取消生成，取消必须 POST。

## 15. 后台生成管线、预算与质量门

### 15.1 作业状态

`state = queued | running | awaiting_outline | needs_input | succeeded | failed | cancelled`；`phase = resolve_sources | research | outline | visual_assets | author_slides | checkpoints | review | render | publish`。

`awaiting_outline` 仅由用户选择先看大纲触发；`needs_input` 只用于缺来源、明确约束冲突等必须用户处理的问题。超时/服务暂不可用先 bounded retry，不把技术故障伪装成等用户批准。

只有以下转换合法：

```text
queued → running → succeeded
            ├→ awaiting_outline → queued
            ├→ needs_input → queued
            ├→ failed → queued（显式 retry）
            └→ cancelled
queued/awaiting_outline/needs_input → cancelled
```

state 与 phase 分离，重试可以从已验证阶段恢复；成功版本不可回写 running。取消和发布竞态按 lesson 锁序列化：取消先取得写权则不得发布；发布已提交则 cancel 返回 succeeded，不删已发布版本。

GET `J/preview` 和 PATCH `J/brief` 编辑的对象始终是草稿；正式 frame/导出/run 创建只接受 Lesson.published_revisions 中的版本。磁盘上有完整 manifest 但指针提交失败的候选，不因此对正式 API 可见。

### 15.2 阶段输入、输出与恢复

| 阶段 | 输入 | 持久化输出 | 重试/恢复 |
|---|---|---|---|
| resolve_sources | brief+身份+当前工作区来源 | authorized source snapshot、内容 hash、教材摘录 | 权限/内容变化重新 resolve，绝不盲用旧路径 |
| research | 缺口/时效要求 | QueryPlan、SourceRecords、claim evidence bank | 相同查询先读有效缓存；API 不可用可按策略降级 |
| outline | brief+可用证据+模板 | 目标、页计划、术语表、时长分配 | 合法大纲 hash 未变则复用；先看大纲在此停 |
| visual_assets | 有图意图的页计划 | 选中的 AssetRecords 与本地 bytes | 下载 hash 完整才复用，半文件清理重下 |
| author_slides | 单页计划+相关证据+相邻摘要 | 逐页 SlideDraft，逐页 checkpoint | 每页输入 hash 未变直接复用；并发最多 2 页 |
| checkpoints | 本课内容+目标 | 经过现有出题质量门的私有题模板 | 基于本课实际内容；不可独立发散出教材外题 |
| review | 全部页+讲稿+依据+题模板 | 确定性报告+教学复核问题列表 | 修复只针对问题页；一次修复后仍严重则失败 |
| render | 已校验 LessonSpec | compiled HTML、layout report、credits | renderer/theme 改变必须重编译与测量 |
| publish | 完整 artifact manifest | immutable revision + latest_ready_revision | CAS/recheck source/epoch 后提交；终态可恢复 |

查询计划可先用 brief 与教材缺口，再在大纲后补查一次；两轮总额仍为同一预算。不能大纲↔检索无限循环。所有每阶段重试都计入同一工作请求的总预算。

### 15.3 Worker 模型

使用独立 `app/classroom/worker.py` 的 lifespan 后台 asyncio supervisor，继续单 uvicorn worker，不引入 Redis/Celery。持久化 job 是事实源，内存 queue 仅为加速；启动扫描未终结 job，重建队列。

- 默认全局最多 2 个生成 job，每 owner 同时 1 个，按 owner 轮转防饥饿；同 lesson 编辑串行。
- 每 job 页生成并发 2，全局 LLM 生成并发 3；音频队列独立，不让备课占住实时讲授资源。
- io/模型调用不持文件锁，提交时核对 job epoch、cancel flag、owner/account/lesson 是否仍可写。
- running job 重启后变 queued/recovering，增加 recovery_count；最多自动恢复 3 次，超出标 failed 并提供显式重试。
- shutdown 停止接受新 job，取消等待任务，给在途写检查点至多 10 秒；不在退出时把所有可恢复 job 标永久失败。
- `next_retry_at` 持久化，服务端重启不重置限流等待。deadline/剩余预算按累计实际耗时与 attempts 保存，不因进程重启获得无限预算。
- 分阶段记录 provider request_id 和输入 hash；请求受理后进程崩溃且未存结果的外部费用可能重复，最多一次自动重做该未确认调用并记 `unknown_external_outcome`。

### 15.4 预算默认值

以下是本项目限制，不是供应商承诺；配置只能由管理员调高。用户界面不暴露内部 token 参数。

| 项目 | 默认上限 |
|---|---|
| 单课页数/时长 | 24 页 / 30 分钟 |
| 单用户新建/重生成 | 3 次/10 分钟、20 次/日；重复幂等请求不扣额 |
| 生成排队 | 每 owner 最多 3 个待处理，全局 20 |
| 全课 LLM 逻辑调用 | `min(40, 2*N+8)`，N 为计划页数；实际 provider HTTP 重试另计并受同一时间预算 |
| 全课 LLM tokens | 输入累计 180k、输出累计 40k；超出停止并保留草稿 |
| 生成总 deadline | 活跃处理累计 900 秒，不含等待队列/用户改大纲时间 |
| 单次 LLM | 90 秒，SDK 隐式重试关闭；每页 schema 修复最多 1 次 |
| 搜索/提取 | 搜索总计 6 次，正文最多 8 URL；缓存命中不计外部额度，计内部步骤 |
| 每篇资料 | 模型上下文必要摘录≤4000 字符；全课证据输入≤40k 字符，超出按目标分配 |
| 图片搜索/下载 | 8 次搜索、12 张正式图、64MB 下载总量、12MB 最终内嵌图片 |
| TTS | 每 run 30k 规范化字符预算，试听每用户 10 次/小时；云端每用户 100k 字符/日 |
| 音频/导出磁盘 | 每 owner 音频 500MB、单课音频 150MB、导出 200MB，超限 LRU/到期清理 |
| renderer | 全局 1，单次 45 秒，失败最多 1 次排版修复 |
| 问答上下文 | 当前页完整、前后页短摘要、最近 6 轮问答；完整课程不每次重灌 |

非课堂普通聊天已有额度不受这里计数覆盖。TTS 失败后重试与回退同样计数真实 usage；只记录必要统计，不记录密钥或原始请求正文。

在 `core/llm_async.py` 增可选的 contextvar budget hook，只在课堂 worker 设置；在真正发 HTTP 前预留一次调用和 token 上限，完成后按 usage 结算，无法知道实际用量时以预留上限扣账。这样现有 quiz/critic 内部嵌套调用也计入同一预算，不会因工具内部重试绕开课堂限制；普通 chat 未设该 context 时行为不变。请求重试也先预留预算，整个子调用不得超过 job remaining deadline。

### 15.5 质量门

**确定性结构门**：所有 ID 唯一、引用存在、顺序连续、页/段/图片数量合规；title/正文/讲稿无占位；checkpoint 引用无答案泄漏；所有动作指向本页存在块；数值 finite；TTS 文本不含 HTML/Markdown 控制符或待解析公式命令。

**证据门**：strict 模式所有核心目标有教材证据；网络事实与数据都有 source；source ID 必须来自本 job 授权 evidence bank；教材页码只能由后端元数据投影；最新请求有有效检索日期与支持来源。

**教学门**：核心概念有条件/例子/解释；相邻页术语一致；讲稿不是纯页面复述。可计算文字重合度作为告警，但不得只以字数或相似率宣称教学合格。独立 reviewer 输出严重问题才阻止发布，仍不把 LLM 自评当事实正确的绝对证明。

**时长门**：初估中文 180–220 字/分钟、英文 120–160 词/分钟（教学停顿另加），超过目标 ±25% 时压缩/拆课或提示预估变化；真实音频形成后更新时长，不回改讲稿。用户要求的时间是目标，不靠加静音凑够。

**视觉门**：实际 Chromium 检查溢出/遮挡/公式与字体/图片解码，严重问题禁止 ready；网络图片不足可无图发布并给 warning。不能把未验证布局标“可上课”。

发布最低条件：内容、来源、动作、HTML、布局全部通过；TTS 与非必需图片可降级。`ready` 不等于音频全量已合成。

## 16. 存储、事务、删除和迁移

### 16.1 目录（全部私有运行数据）

新增唯一根常量 `app/core/classroom_store.py::_CLASSROOM_DIR = <root>/chat_history/classroom`。其他模块调用 store 路径函数，不复制 root 常量或在 import 时冻结派生路径。

```text
chat_history/classroom/
  <owner>/
    owner.json                         生命周期、配额、幂等请求索引的小型元数据
    image-search-cache/<query_hash>.json 24h 搜索缓存，不存 key
    voice-previews/<synthesis_hash>.*    固定试听句的音频与元数据，纳入清理
    workspaces/<workspace_id>/
      index.json                       可重建课程摘要及 job_id→lesson_id 映射，不是内容事实源
      operations/<op_id>.json          跨文件归档/恢复/笔记/QA 绑定意图与完成标记
      lessons/<lesson_id>/
        lesson.json                    latest_ready_revision、next_revision、生命周期
        revisions/<revision>/
          manifest.json                内容/源码/HTML/assets 引用的 hash，完整发布标记
          spec.private.json            含 checkpoint 私有模板
          spec.public.json             白名单投影，可重新生成
          frame.html                   在线与离线共用的基础可信 HTML
          speaker-notes.md
          credits.json
        jobs/<job_id>/
          job.json                     状态、预算、epoch、检查点
          staging/                     中间证据/大纲/逐页 draft/排版报告
        assets/<asset_id>.<ext>
        assets/<asset_id>.json
        runs/<run_id>.json              进度、lease、题目映射、批注、QA 引用
        audio/<synthesis_hash>.wav
        audio/<synthesis_hash>.json
        exports/<export_id>.zip
        exports/<export_id>.json
```

run annotations 最多 100 条，每条 4000 字；问答历史使用既有 session/transcript，不在 run 重复存整份消息。clip_id 到 synthesis_hash 的映射存 run 的 bounded audio_refs（最多 1000），防止持有同 owner 另一课 hash 就可绕过路径绑定。

权限路径 `owner/workspace/lesson` 层层检查。读取不得隐式 mkdir；list/capability/orphan scan 不生成空目录。写入目录按 owner 私有权限 0700、文件 0600（部署 umask 配合）。拒绝路径中的 symlink 逃逸，最终 resolve 路径必须位于正确 root。

### 16.2 单文件与发布事务

所有 JSON 写操作通过 `core/atomic.py`，调用者持路径锁；新增 `atomic_write_bytes`（唯一临时文件、fsync、replace）作为该模块对音频/ZIP 的扩展，不散落自制 write helper。现有固定 `.tmp` 的 atomic_write_text 仍必须在同一文件锁内调用。

版本发布顺序：

1. lesson 锁内分配单调 `target_revision` 并保存 job 意图；失败 revision 允许留空号，永不复用。
2. 生成中只写 staging、assets 和独立检查点，不覆写任何已发布 revision。
3. 发布前生成完整 revision 临时目录，校验每个文件/hash/引用，fsync 文件和目录；manifest 最后写。
4. 同一文件系统 atomic rename 临时目录到 `revisions/N`；再在 lesson 锁内复查 owner、workspace、epoch、base revision/cancel，单次原子写同时追加 `published_revisions` 并更新 `latest_ready_revision`。
5. 更新 job succeeded 和可重建 index；若在步骤 4/5 之间崩溃，启动恢复根据 manifest+job 意图补齐 pointer/state，不重新调用模型。
6. 取消/归档先于 pointer 提交时，已生成目录只是未发布候选，恢复器不得自动提升它。commit intent 保存 expected epoch 与授权快照，防止“看见 manifest 就无条件发布”。

所有强制写失败返回 `storage_unavailable`；不吞掉进度/答案/删除错误后显示“保存成功”。账号、workspaces 原有 corrupt-as-empty 逻辑不得扩散到新已发布 manifest：损坏课程标 damaged、隔离并报告，不返回一份空课冒充正常。

### 16.3 锁与竞态

同线程同步锁顺序固定：account record lock → classroom owner lifecycle lock → workspace lifecycle lock → lesson lock → run/job/audio 文件锁；触及既有 journal 时按现有账户→journal 顺序做独立幂等步骤，不在 classroom 锁内 await 或倒序取得 account lock。

跨 store 的业务不能假装一个 RLock 就能原子提交：用 operations 意图+稳定外部 ID+重试收敛。例：保存笔记时预分配 note_id，落 intent，调用可幂等的 notes 写入，落结果；崩溃后读相同 note_id 补齐，不新建第二份。

`threading.RLock` 不提供协程级 await 互斥；worker 调度使用 asyncio.Lock/Semaphore，文件锁只包纯同步读改写。发布的最终授权复查必须和指针更新处于同一 lifecycle 临界区。

现有 `Vault.create_note(note_id=...)` 遇到同 ID 会另造 ID，并非天然幂等。课堂桥接须在 vault/index 锁内先按预留 note_id 查找并核对 `source={kind:"classroom",run_id,operation_id}`，相同来源返回已有笔记；不匹配报冲突；不存在才创建。不能仅重复传 note_id 就假定不会产生重复笔记。

### 16.4 删除、回收站与账号注销

- 单课程删除使用新 trash 类型 `classroom_lesson`；归档可后台执行（大音频），UI 显示处理中。先写 durable archive op、标 lifecycle=archiving、提升 epoch、取消生成/音频/lease，再快照内容与 run。
- 回收站保存所有已发布 revision、必需图片、run/notes/来源，音频缓存和过期 exports 可不打包（可重建）；manifest 明确哪些为可重建缓存。
- 工作区归档扩展 `trash.archive_workspace`：在快照工作区前冻结该区所有课堂写入，打包 classroom 子树与归档 op；只在 trash bundle commit 后删活跃课堂。现有 session/library 语义保持。
- 恢复按原 ID 检查冲突，恢复课程为 paused，所有 lease 清空；已中断 job 进入 needs_input(recovered_after_archive)，不在恢复时未经操作重新付费生成。
- 清空回收站/永久删除课程清理整个 lesson 子树，释放 assets/audio/exports；不清理普通 Q&A 聊天除非用户同时删除该会话，run 链接显示来源已删除。工作区整体归档已包含成员聊天，依现有机制共同处理。
- 账号注销/管理员删除走 `core/account_data.purge_account`：先吊销课堂工作资格和所有在途任务，再删除 `<owner>` 整个课堂根、相关 trash 与外部索引；最后删除账号记录。所有后台提交复查账号仍存在且没有 lifecycle tombstone，禁止删除后“晚到写回”复活目录。
- 管理员 `clear_chat_data(scope="all")` 同时清理课堂与 `classroom_lesson` 回收站；`scope="uploads_only"` 删除课堂原始自有上传图及包含这些 bytes 的已编译 HTML/导出，保留派生讲稿、spec 元数据和进度（与保留聊天文本一致）。独立 artifact availability 记录失效，不篡改冻结 spec；阅读时明确“上传图已清理”，只允许重编译带缺图说明的派生展示。恢复原图需显式上传并创建新版。实际仅有 all/uploads_only 两个 scope，不能新增错误的 uploads 枚举。
- `account_data` 用量统计新增 classroom_bytes（不含 audio）/audio_bytes（独立项），总量各加一次，避免重复计数或删后仍显示旧值。
- `core/orphan_cleanup.py` 增加 classroom 分类：不存在 owner 的根、找不到 workspace 的活跃子树、无 lesson 引用且过期的 assets/staging、未提交 op；可恢复中的 op 和合法 trash 不能被扫成孤儿。
- 孤儿清理保留 `student_default` 的既有游客规则，但生产课堂生成默认仅认证用户可用，见 §20。

删除/恢复和账号清理不得全盘遍历公共教材并误删 public 内容，也不能扩大 demo 账号的 Git 白名单。

### 16.5 清理与保留

音频 LRU：默认 7 天未访问可清，超 owner 500MB 优先清理非活动 run 的最旧音频；当前 lease 持有或当前段在播放中的文件暂不删。磁盘删除失败记录可重试 cleanup op，不报告已释放空间。

exports 24h 过期；搜索缓存按 24h TTL；失败/取消 job staging 保留 7 天供重试，过后保留 brief、错误和必要状态，再重试须重新生成缺失阶段。已发布 revision 和课程内容不会按音频 TTL 自动删除。

默认每课最多 20 个已发布版本；达到上限时拒绝新修订并提示归档本课或另建课程。不能自动删被 run 或导出引用的版本。显式删除单个旧版本端点不在首发范围；首发只提供整课归档/恢复，避免出现未定义的版本 GC。

### 16.6 迁移与可回滚性

新增目录按需创建；旧 workspace/session 无需批量迁移，新增字段均 optional 默认。`TutorSession` 只增加可选课堂答疑引用/上下文来源元数据，不将课堂正文混入已保存消息。

既有 `SourceKind` 和 journal checksum/事务 schema 不改写旧记录；必要的 source_surface 展示从 run/question 映射生成，不能重写历史 learning_evidence。TaskSnapshot 需要新增来源展示字段时采用有缺省的 additive 字段，并保持旧 raw envelope checksum 校验顺序。

`docs/DESIGN.md` 当前包含对旧 plan.md 章节的历史引用，本期实施时把新课堂设计正式写入 DESIGN 新节，并修正相关旧 plan 锚点为长期文档；本次只交付计划，不篡改现状文档。

## 17. 文件级改动清单

目录仅列计划新增路径；现有文件以当前实际名称为准。文件职责按下表分配，避免把所有逻辑塞进 route 或 React 页面。

### 17.1 后端新增

| 路径（均相对 backend/app） | 职责 |
|---|---|
| `schemas/classroom.py` | Brief、Spec、public DTO、API 请求/响应、错误枚举 |
| `api/v1/classroom.py` | §14 所有课堂端点；复用认证和 rate_limit |
| `core/classroom_store.py` | 唯一 root、路径、原子持久化、CAS、索引与迁移 |
| `core/agent_tools.py` | 从 chat 提取共用教材/出题工具组装，支持可信范围 override |
| `classroom/service.py` | 课程/修订/run/幂等操作的应用服务 |
| `classroom/worker.py` | 队列、恢复、取消、deadline、生命周期 |
| `classroom/pipeline.py` | 生成各阶段及输入/产物 hash |
| `classroom/sources.py` | 工作区权限、来源冻结、RAG 证据适配 |
| `classroom/research/base.py` / `tavily.py` | search/extract 契约和唯一首发实现 |
| `classroom/media/base.py` / `pexels.py` / `pixabay.py` | 素材候选、元数据映射、限流头解析 |
| `classroom/media/download.py` | URL/CDN 验证、受控下载、图片清洗、hash |
| `classroom/validation.py` | schema 之外的引用/目标/讲稿/时长/答案边界检查 |
| `classroom/render/compiler.py` / `blocks.py` / `themes.py` | HTML 编译、图形构建、主题定义 |
| `classroom/render/check.py` | Node/Chromium 受限检查进程、超时与报告 |
| `classroom/audio.py` | cache、段解析、合成队列、provider failover、WAV 写入 |
| `classroom/runs.py` | cursor、lease、checkpoint 转换、问答恢复位置 |
| `classroom/assessment_bridge.py` | 私有模板→冻结题、帮助、evaluate_submission、只读反馈 |
| `classroom/chat_context.py` | 验证 classroom_ref、生成上下文，不伪造用户消息 |
| `classroom/exports.py` | ZIP/讲稿导出、credits、安全文件名 |
| `classroom/lifecycle.py` | 归档/恢复/purge/定时清理、可恢复操作 |
| `classroom/templates.py` | 教学模板注册、默认 brief、教学预算 |
| `prompts/classroom.py` | 所有新提示词的文本与版本 |
| `voice/tts/azure.py` / `voice/tts/service.py` | 云 adapter、能力/健康/全局并发、兼容 factory |

### 17.2 后端及部署必须修改

`api/v1/router.py` 注册；`main.py` lifespan 启停；`core/config.py` 配置；`prompts/registry.py` 注册；`voice/base.py`、`voice/tts/{__init__,melotts,stub}.py` 兼容扩展；`api/v1/voice.py` 复用共享 TTS 并发；`schemas/chat.py`、`api/v1/chat.py`、`agents/chat_agent.py`、`agents/supervisor.py`、相关 preamble 构造 helper 传递可选课堂上下文；`core/session.py` 保存必要引用。

`core/llm_async.py` 增 classroom purpose 与预算钩子；新增 `classroom/limits.py` 统一限制；新增根目录 `scripts/generate_classroom_types.py` 从公开 Pydantic schema 生成 DTO，支持 object/array/enum/nullable/ref/discriminated union 并为这些转换添加固定 fixture 测试。

`api/v1/sidebar.py`、workspace summary helpers 读课堂索引；`core/trash.py`、`core/account_data.py`、`core/orphan_cleanup.py` 全生命周期；`core/notes.py` 支持来自课堂的稳定 ID 幂等保存（不可改变旧 note revision 行为）；`core/atomic.py` 添加二进制原子写。

`tests/storage_sandbox.py` patch `_CLASSROOM_DIR`、重置课堂 provider/queue/cache；`tests/__init__.py` 清除新增供应商凭证变量；`start.sh`、`deploy/edu-backend.service`、sidecar unit/安装说明、nginx 模板、`.env.example`、`.gitignore`、CI、仓库不变量脚本、`docs/DESIGN.md`、`docs/VOICE_LICENSES.md` 与部署文档一同更新。

### 17.3 前端新增

| 路径（相对 frontend） | 职责 |
|---|---|
| `src/app/(workspace)/workspaces/[workspaceId]/classroom/page.tsx` | 课程列表 |
| `.../classroom/[lessonId]/page.tsx` | 预览/编辑/版本 |
| `.../classroom/[lessonId]/learn/[runId]/page.tsx` | 固定 run 播放器 |
| `src/components/workspace/WorkspaceModeBar.tsx` | 对话/课堂与工作区定位 |
| `src/components/classroom/ClassroomList.tsx` / `LessonCard.tsx` | 列表、Pager、状态 |
| `.../CreateLessonModal.tsx` / `TemplatePicker.tsx` | 备课设置与原创缩略图 |
| `.../GenerationProgress.tsx` / `LessonPreview.tsx` / `LessonEditor.tsx` | 生成、大纲审核、修改 |
| `.../ClassroomPlayer.tsx` / `SlideFrame.tsx` / `PlayerControls.tsx` | 场景组装、iframe、控制条 |
| `.../SlideOutline.tsx` / `NarrationPanel.tsx` / `CaptionBar.tsx` | 目录、讲稿、字幕 |
| `.../QuestionDrawer.tsx` / `CheckpointPanel.tsx` / `ClassroomNotes.tsx` | 插问、题卡 transport、笔记 |
| `.../SourcePanel.tsx` / `ImagePickerModal.tsx` / `LessonSummary.tsx` | 引用、选图、回顾 |
| `src/lib/api-classroom.ts` / `types-classroom.ts` | 认证请求、错误处理、DTO |
| `src/lib/classroom/useClassroomPlayer.ts` / `player-reducer.ts` | 有限状态、cursor、epoch |
| `src/lib/classroom/audio-controller.ts` / `audio-focus.ts` | 媒体播放、预取、跨模块音频互斥 |
| `src/lib/classroom/useGenerationJob.ts` | SSE snapshot、重连、取消 |
| `src/lib/classroom/usePlaybackLease.ts` | 心跳/接管/租约丢失 |
| `src/lib/classroom/frame-runtime.ts` | 可信 frame DOM runtime |
| `src/components/classroom/strings.ts` | zh/en 全量课堂词条 |
| `scripts/build-classroom-assets.mjs` / `check-classroom-render.mjs` | 构建与真实排版校验 |
| `tsconfig.classroom.json` | 独立 frame runtime 构建 |

现有组件修改：WorkspaceItem/Sidebar 增入口与摘要；chat 页增模式条/classroom_ref 支持；QuizCard 抽 transport hook；useVoiceCall/VoiceCallLayer 接共享 audio focus；必要时从 STT 代码抽 `lib/voice/browser-recognition.ts`；AccountCard 增课堂语音偏好（原 tts_speed 保留）；AppShell 增专注布局状态；auth-store 登出时清理课堂内存/Blob/草稿。

所有 API 经 `apiFetch`，不私自引入 axios；错误响应先检查 `res.ok`；新路由重组件 `next/dynamic`，避免普通 chat 初次加载被课堂编辑器/运行时增重。服务端数据与媒体状态分开；不把 Audio/MessagePort/AbortController 持久化进 zustand。

## 18. 按依赖顺序执行的开发任务

实施者依次完成 A–K，每阶段合并前过该阶段门；阶段内可拆小提交，但不能用“后续再处理”跳过权限、状态和清理。阶段 J 的场景验收是整合复核，基础删除/沙箱接口必须在 A 就落地。下列均为待办，不代表本次已执行。

### A. 契约、基础存储和生命周期骨架

依赖：无。

- [x] A01 将 §10/§14 的字段、枚举、限制落为 Pydantic；从 schema 生成一份 `frontend/src/lib/types-classroom.generated.ts`，手写前端行为类型置 types-classroom.ts，不维护两份漂移 DTO。用项目内小型生成脚本输出确定性类型，CI 比较生成结果。
- [x] A02 实现课堂 root、ID 校验、文件锁/CAS、atomic_write_bytes、lesson/job/run 读写、manifest hash 和可重建 index。
- [x] A03 为新增 root 同时修改 storage_sandbox、orphan_cleanup、account_data/trash 的入口和 tombstone 防晚写逻辑；添加 fixtures 用已隔离临时目录。
- [x] A04 注册 feature flag/capabilities/templates/router；功能关闭时能力端点仍可读，生成端点返回明确 disabled。
- [x] A05 实现幂等键/body hash、owner 生命周期、配额计数与错误 envelope，测试多账户访问。

退出门：空课程 CRUD/归档恢复在 sandbox 下可重复执行；重启能读；未知字段拒绝；没有任何生产目录写入；GET 列表不 mkdir。

### B. 原创主题、受控 HTML 与排版检查

依赖：A。

- [x] B01 实现 5 主题×9 布局的 slots/token，制作项目内原创预览 fixture；先覆盖最长中文标题、英文长单词、公式、图表与无图情况。
- [x] B02 实现 Block/Diagram 编译、HTML escaping、KaTeX、显隐/高亮 runtime、CSP、MessagePort bridge。
- [x] B03 构建 generated assets，加入 dev/build/CI/deploy；实现静态 HTML 下载的认证与 srcdoc 展示。
- [x] B04 编写 Node layout worker，禁止网络；测量 3 尺寸并提供结构化 overflow report，确定性布局回退。
- [x] B05 实现阅读模式、credits、讲稿 Markdown、HTML ZIP 和 print CSS。

退出门：手工构造的合法 LessonSpec 可在线/离线看；恶意 script/url/LaTeX/图形数据不能执行；有长内容时不会缩字到不可读；frame 无法请求外网或读取父页面。

### C. 来源适配与外部检索/图片服务

依赖：A；不依赖 UI。

- [x] C01 将原工作区材料解析封装为 classroom.sources，支持教材组卷顺序、章节、source hash 和显式 session attachments。
- [x] C02 实现 Tavily search/extract adapter、结果部分失败、时效元数据、预算与缓存。
- [x] C03 实现 Pexels/Pixabay adapter、24h 缓存、署名数据、候选 ID、限流和无 key 降级。
- [x] C04 实现 CDN 下载、SSRF/IP 校验、每跳重验、Pillow 重编码、EXIF 清理、asset hash。
- [x] C05 加 provider HTTP mock fixtures，仅保存人工构造的响应，不提交真实用户查询/key/下载素材。

退出门：strict 模式不借网络补教材证据；跨工作区/账号/会话附件越权失败；没有图片 key 仍可编译；临时 CDN URL 过期不影响已保存课件。

### D. 备课 Agent 与可恢复 worker

依赖：A、B、C。

- [x] D01 注册七个 prompt 与五个教学模板，统一调用 `get_llm("classroom")`（新增 purpose 路由，默认回现有主模型，不复制密钥配置）。
- [x] D02 按 §15 实现各阶段及 checked artifact；fake LLM 可从 Brief 生成完整教材课程。
- [x] D03 接入题目生成/审核并保留私有模板；内容生成和题模板不提前注册为学生作答。
- [x] D04 实现 checkpoint、恢复、epoch、取消、上限、provider retry 预算；发布前复查来源与版本。
- [x] D05 实现单页重生成/手工修改/换主题/换图/刷新来源五种 revision operation，保留旧版本可用。
- [x] D06 实现 job snapshot SSE 和轮询 fallback；前端断线不影响 worker。

退出门：stub 生成可真正产出 HTML+讲稿+来源+题模板；每个阶段注入一次 crash 后可恢复；cancel 不发布晚到结果；坏 JSON/无来源/溢出均不会标 ready。

### E. 工作区入口与备课/编辑 UI

依赖：B、D。

- [x] E01 增路由和 WorkspaceModeBar、工作区菜单/固定课堂入口、Sidebar 批量摘要。
- [x] E02 列表三态、Pager、CreateLessonModal、模板预览、默认值和能力不足提示。
- [x] E03 生成阶段、关闭后恢复、取消/重试、可选大纲审核、失败草稿预览。
- [x] E04 实现 LessonPreview/Editor、单页操作、来源右栏、图片选择、修订冲突、下载。
- [x] E05 添加全部 zh/en 文案；截图核验浅/深色和窄屏，chat URL/侧边栏旧操作回归。

退出门：用户能从现有工作区完成一键备课、看讲稿、换风格、新版发布；返回聊天后会话和草稿不串区。

### F. 云端 TTS 与统一资源控制

依赖：A、D；可先用已发布 fixture 开发。

- [x] F01 扩展兼容 TTS contract/service，Azure REST adapter，voices list 及音色 allowlist。
- [x] F02 原电话 factory 保持无参调用；Melo/stub 参数兼容，云端/本地共享 semaphore 和错误分类。
- [x] F03 实现段 audio key、single-flight、WAV 原子写、usage/cost counters、LRU、认证 content。
- [x] F04 实现有界预取、取消、provider 切换边界、pure GET、local/silent 回退。
- [x] F05 更新 start.sh/systemd/.env.example/VOICE_LICENSES；加入个人课堂 voice prefs 严格校验，不能浅合并任意供应商 URL。

退出门：旧 test_voice 全绿；云端 stub 失败转本地只提示一次；同一文本/音色重放命中缓存；不同用户/音色不串；单个 Melo 模型同时最多 1 请求。

### G. 课堂播放器和断点恢复

依赖：E、F。

- [x] G01 实现 reducer、audio controller、共享 audio focus、media clock、epoch 丢弃机制。
- [x] G02 将段动作绑定 frame MessagePort；实现字幕、目录、页/段切换、语速/音量、阅读模式。
- [x] G03 实现 run 创建/恢复、进度 CAS/节流、lease 心跳/接管、refresh/background/offline 行为。
- [x] G04 实现专注/全屏/键盘/触控/reduced-motion；播放器之外不挂全局快捷键。
- [x] G05 完成真实音频与 fake audio clock 两套验证，确保 ended 才进入下一段。

退出门：一整节课程可无问答播放完成；任意跳页/暂停/刷新不出现旧声音；双标签接管停止旧控制器；无 TTS 也能读讲稿完成。

### H. 插问、补讲、语音输入与笔记

依赖：G；修改旧聊天代码之前先补兼容回归。

- [x] H01 实现 qa_session 幂等创建、ClassroomTurnContext、受信任来源 override，并贯通 supervisor/legacy/fallback。
- [x] H02 问答抽屉、快捷补讲、原课 resume anchor、回复正文语音，保证 ask 不使 run 自动结束。
- [x] H03 抽浏览器 STT 适配，麦克风按住开启/松开结束；不支持时文字输入可用；启用 STT 时停止课堂输出避免回声转写。
- [x] H04 批注、记录当前页、保存 notes 的幂等操作与深链；课堂链接被删除后的无效状态可识别。

退出门：多次追问返回最初被打断段；问答只注入真实提问作为用户消息；删除/切换课堂后迟到 SSE 不污染新页；普通聊天无 classroom_ref 时行为不变。

### I. 随堂题、学习评价与课后闭环

依赖：G、H 及 D 的题模板。

- [ ] I01 实现 run→question 稳定实例化和崩溃恢复；同题重听关联原题族和已获帮助。
- [ ] I02 QuizQuestionCard transport 抽取，课堂提交/提示/揭晓/恢复与原聊天共用视图。
- [ ] I03 测试音频/HTML/讲稿/export/preview 中均无未揭晓答案，M2/M4 disabled 与 scope changed 的 UI 文案。
- [ ] I04 实现 summary、再学习入口、notes 汇总和已有 M9 outbox 消费；仅有播放行为不提交任何 assessment/dialogue 观察。

退出门：同一正式答案只受理一次；跳过不算失败；观看完不写掌握结论；帮助后作答绝不标 independent。

### J. 全生命周期与运行可靠性收口

依赖：A–I。

- [ ] J01 用完整音频/图片/run/QA 课程回归 archive/restore/purge_account/orphan scan/管理员清理，不留空 owner 目录。
- [ ] J02 逐个注入 crash：发布、笔记写入、QA 绑定、题目注册、归档 bundle commit、删除后晚到 TTS。
- [ ] J03 加配额/磁盘满/并发/进程重启/供应商限流/JSON 损坏的告警与恢复动作。
- [ ] J04 核查 .gitignore、systemd 可写路径、Chromium sandbox/依赖、SSE nginx、构建产物缺失行为。

退出门：所有持久化与权限故障都是可观察状态，重试收敛；生产根没有合成测试数据；无 secrets/raw CoT 落盘。

### K. 验收、文档和发布

依赖：所有前阶段。

- [ ] K01 执行 §19 测试矩阵与命令，保存必要测试报告与 UI 截图。
- [ ] K02 管理员测试账号配置真实 provider 后跑受预算约束烟测，确认中文公式口播、图片署名与日期来源。
- [ ] K03 更新 DESIGN（只记录已实现现状）、API/配置/部署/操作说明；移除过时 plan 锚点。
- [ ] K04 按 §20 灰度，记录真实 P50/P95；达到发布门后开放默认入口。

完整首发完成的定义是 K 结束，而不是 E 的界面能看或 G 的音频能播。提交建议按上述范围命名，例如 `classroom: add versioned lesson storage`、`classroom: render narrated lessons`、`voice: add cloud speech with local fallback`。

## 19. 测试矩阵、可执行检查与验收

### 19.1 测试环境硬约束

所有会读写存储的 unittest 继承 `StorageSandboxTestCase`；异步测试通过既有项目模式或调用 `patch_all_storage_roots` 并确保清理。新增缓存、provider singleton、队列、lease、clock 都提供 reset；测试前后重置。不得使用不清理的 mkdtemp，不向真实 students/chat_history/notes/uploads 写合成 ID。

新增 `tests/fixtures/classroom/` 只放手写 JSON、项目原创小图和合成短 WAV；不放真实教材、用户对话、下载网页或真实云端返回。网络 adapter 用 httpx.MockTransport；fake research/media/TTS clock 负责稳定失败场景。单元测试禁止出站，只有显式真实服务烟测才读取凭证。

E2E 延续 `frontend/e2e/prepare-backend.mjs` 隔离副本；扩展复制课堂源码、生成 renderer assets 和 fake 模型 responses，确认 renderer cwd/path 指向隔离副本。浏览器测试即使保留失败截图也不能含真实账号材料。测试端口沿用 8124/fake 8199，不能打生产 8123。

### 19.2 后端测试文件与必须覆盖的断言

| 文件（backend/tests） | 关键用例 |
|---|---|
| `test_classroom_schema.py` | 闭合字段、ID/引用/enum、空讲稿、长度、NaN/Infinity、坏 layout、未知 action、schema version 不支持 |
| `test_classroom_storage.py` | 原子写、CAS、读不 mkdir、hash 损坏、重建 index、版本空号、跨文件操作恢复、幂等同 key 不同 body |
| `test_classroom_identity.py` | A/B 账户交叉访问每类资源 404；owner body 无效；同 owner 跨工作区错误绑定也拒绝；已删账号 worker 晚写失败 |
| `test_classroom_sources.py` | 公共教材只读、未选教材不可见、本区共享文件、显式 session 附件、其他区附件拒绝、分卷/页码、source hash 变化 |
| `test_classroom_research.py` | 搜索失败/无 key、extract 部分成功、假日期、冲突资料、正文不足、最新要求失败、prompt injection 不能改变工具 scope |
| `test_classroom_images.py` | 两个 API 参数映射、24h 缓存、429、候选 ID 防伪、热链失效、下载上限、坏 MIME、EXIF、像素炸弹、署名字段 |
| `test_classroom_network.py` | userinfo/私网 IPv4/IPv6/编码 IP/重定向/localhost/metadata；DNS 重绑定不发生二次不受控解析；查询 key 不出日志 |
| `test_classroom_render.py` | 每种 block escaping、脚本注入、URL/CSS 注入、KaTeX trust、图表数值、无 raw SVG、public HTML 无答案、runtime hash 一致 |
| `test_classroom_generation.py` | fake LLM 完整管线；坏 JSON 修复一次；来源不足；页内容不一致；预算耗尽；主题更换零 LLM；单页重生成不重做整课 |
| `test_classroom_jobs.py` | 每阶段重启、cancel/publish 竞态、deadline 跨恢复、排队公平、恢复次数、snapshot revision、断开 SSE 不取消 |
| `test_classroom_audio.py` | 音频 key 含 owner/voice/provider、single-flight、WAV 时长、纯 GET、queue 满、云失败一次回退、未授权段不可合成、私有反馈防泄漏 |
| `test_voice_azure.py` | SSML escaping、请求头/格式、voices list、中文英文音色、401/429/5xx/超时、解析失败不伪成功、无 key 零请求 |
| `test_classroom_runs.py` | cursor 校验、过期事件、lease 接管、幂等 progress、刷新暂停、complete 与 skipped 区分、结束后不接受旧播放事件 |
| `test_classroom_chat.py` | 服务端上下文、三种执行路径、无 CoT TTS、真实用户原文、坏 session/run绑定、Q&A 创建 crash 恢复、不提前读题答案 |
| `test_classroom_assessment.py` | 题目冻结先于交付；同题同答案幂等；不同答案冲突；hint/reveal 先记帮助；已揭晓题重听不是独立证据；播放零学习证据 |
| `test_classroom_lifecycle.py` | 完整课程归档/恢复/永久删除/账号注销/两个清理 scope、音频不留孤儿、删除时 provider 返回、空目录清除、public资产不受影响 |
| `test_classroom_exports.py` | ZIP 路径安全、自包含离线 HTML、无 token/答案/全文教材、credits、过期、删除后下载拒绝、源图已清理不导出旧 bytes |

现有必须回归：test_voice、workspace isolation/shared_materials/sources、notes、orphan_cleanup、admin_account_data、quiz/assessment 身份与受理、journal、supervisor/legacy。不要只跑新增测试就认定旧聊天没受影响。

### 19.3 前端与端到端场景

新增 E2E 文件及用例：

| 文件 | 场景与通过条件 |
|---|---|
| `classroom-create.spec.ts` | 工作区进入→默认设置→一次生成→关闭→重进，仍是同一 job/lesson；空教材/无外部服务也有明确路径 |
| `classroom-editor.spec.ts` | 逐页讲稿、换主题、换图、单页重生成、双窗口编辑 409、旧 run 不换版本、分页末页删除钳位 |
| `classroom-player.spec.ts` | 真正 audio playing 时高亮；ended 才翻页；pause/resume/倍速/跳页/旧音频丢弃/纯文本模式 |
| `classroom-resume.spec.ts` | 刷新、退出返回、网络断开、后台标签、lease 超时/接管、登出清 Blob，恢复必须用户手势 |
| `classroom-questions.spec.ts` | 插问多轮返回原 anchor；检查点提示/揭晓/已受理 pending/刷新恢复；跳过与已答不同；后续段不提前朗读答案 |
| `classroom-security.spec.ts` | iframe 无 parent storage/network；伪造 postMessage 无效果；A 用户的 HTML/audio/assets/export 对 B 不可读 |
| `classroom-export.spec.ts` | 离线打开 index.html 切页/公式/图片可见，无任何外部请求；打印版分页面；讲稿与 credits 完整 |
| `classroom-visual.spec.ts` | 5 主题，浅/深色，390×844/768×1024/1440×900，200% 字号，reduced-motion；控件不遮挡课件 |

重点添加媒体控制器的确定性测试（用 fake HTMLMediaElement 和受控 clock），不要通过 sleep 十几秒赌 audio 事件。Playwright 音频测试验证事件/可用媒体时间和 sample，不仅检查图标变为“暂停”。

视觉快照从原创固定 fixture 生成，不能用随机模型文案作 golden。每套主题最少 3 张代表页面：公式、照片、长内容；至少一次人工看图确认自动 bounds 检查未漏掉视觉层次问题。

### 19.4 内容质量样本

用下列自写小教材片段/结构化证据作为固定测试输入，人工评审只对生成结果进行，不借鉴其他项目课件：

1. 本科物理“系统与动量守恒”：公式、条件、图示、近似、反例，验证不是照读公式。
2. 初中数学“一次函数斜率”：坐标图、数值例子、易错对比，小屏可读。
3. 高中生物“酶活性与温度”：示意图/实验数据区分，不拿图库照片充当测量证据。
4. 中文人文阅读：段落摘录和解读分开，引用长度受控，不输出整本受保护作品。
5. 英文科学课程：英文音色/长词/公式口播，界面语言与授课语言互不混乱。
6. 前沿专题：设置检索 as_of，模拟互相矛盾和缺日期资料，拒绝编造“最新数据”。

每份抽查 3 个核心页，以正确性、目标覆盖、解释深度、讲稿自然度、页面可读性各 1–5 分记录。发布门：正确性不得出现明确严重错误，其他维度平均至少 4；未达标修 prompt/模板并重跑对应样本，不靠删评测样本通过。

### 19.5 实施后的命令顺序

以下是开发完成后要运行的命令；本次文档任务不执行这些业务测试，也不假称已通过。

先在仓库根目录生成/检查 DTO：

```bash
python scripts/generate_classroom_types.py --check
```

在 `backend/` 跑 focused，随后全量：

```bash
python -m unittest discover -s tests -p 'test_classroom_*.py'
python -m unittest tests.test_voice tests.test_voice_azure
python -m unittest tests.test_workspace_isolation tests.test_workspace_sources tests.test_workspace_shared_materials
python -m unittest tests.test_orphan_cleanup tests.test_admin_account_data tests.test_notes
python -m unittest discover -s tests
```

在 `frontend/`：

```bash
pnpm run build:classroom
pnpm exec tsc --noEmit
pnpm exec eslint src/
pnpm exec next build --webpack
pnpm exec playwright test e2e/classroom-create.spec.ts e2e/classroom-editor.spec.ts e2e/classroom-player.spec.ts e2e/classroom-resume.spec.ts e2e/classroom-questions.spec.ts e2e/classroom-security.spec.ts e2e/classroom-export.spec.ts e2e/classroom-visual.spec.ts
pnpm exec playwright test e2e/voice-smoke.spec.ts e2e/quiz-card-contract.spec.ts e2e/auth-isolation.spec.ts e2e/strict-qa.spec.ts e2e/notes.spec.ts
```

浏览器安装是部署/CI 初始化步骤 `pnpm exec playwright install --with-deps chromium`，不在每个测试或请求里重复运行。在仓库根目录最终执行：

```bash
python scripts/check_repository_invariants.py
git diff --check
git status --short
```

CI 增 `classroom-checks` job（schema/type generation、renderer、课堂 E2E），保留现有 backend-core/backend-vector-regression/frontend-checks/repository-invariants 名称。后端普通 unit 不依赖 Chromium，真正 layout/E2E 在新 job 安装浏览器；生产 capability 必须确认 renderer 可用。

### 19.6 性能与真实服务验收

以下是发布目标和测量方式，不是现在已经实现的性能承诺：

- 本机基准环境记录 CPU/内存/磁盘/网络、provider/model、时间；warm/cold 分开统计。
- 课程列表 50 课时后端 P95 <300ms，详情不含 HTML P95 <500ms；同机课件 iframe warm 可见 <1.5s。
- 有缓存时点击播放至第一帧可听目标 <500ms；云端无缓存首段 P95 目标 <5s，超目标必须能明确缓冲/切换，不冻结控制条。
- 暂停/插问/跳页本地停止声音 <150ms，不等待服务端取消响应。
- 15 分钟/10 页课程生成目标 P50 <180s、P95 <480s；达到 900s 预算时明确停止保留草稿。以实际选定 LLM/provider 测量，不能以 fake LLM 数字宣称达标。
- 两人播放+一人备课时 chat/health 不因 WAV decode 或 renderer 阻塞事件循环；媒体 CPU 工作全部移出 async 主循环。
- 20 分钟播放内存不得随已播段线性增长；最多 3 段 Blob 和当前/相邻页面渲染缓存，长时间播放后可回收。
- 同一课程重播已缓存部分，云端请求数为 0；调倍速不增加合成次数；音频 LRU 能释放磁盘且不删当前 clip。

真实烟测只用测试账号、原创非敏感讲稿，分别验证 Azure zh/en、Melo 中文、云失败回退、Pexels/Pixabay 选中图本地保存、Tavily 有日期结果；记录实际 provider 请求数和用量。缺真实凭证时记录该项未验收，禁止把 stub 成功填成生产验收通过。

## 20. 配置、部署、监控和发布回滚

### 20.1 环境变量与默认值

新变量写入 `.env.example`，实际 key 只留部署 `.env`；`NEXT_PUBLIC_` 不能承载以下任何凭证。已有 LLM 配置继续复用。

| 变量 | 默认 | 说明 |
|---|---|---|
| `CLASSROOM_ENABLED` | 0（开发阶段），验收后发布模板设 1 | 总闸，只影响课堂 |
| `CLASSROOM_ALLOW_GUEST` | 0 | 默认仅认证用户可生成/播放私有课堂；开发测试可显式 1，仍用原游客 namespace |
| `CLASSROOM_MODEL` | 空 | `get_llm("classroom")` 模型覆盖；空用主模型，凭证/base URL 不另建 |
| `CLASSROOM_WEB_PROVIDER` | tavily | provider 无 key 即不可用，不伪联网 |
| `TAVILY_API_KEY` | 空 | server-only |
| `PEXELS_API_KEY` / `PIXABAY_API_KEY` | 空 | server-only |
| `CLASSROOM_IMAGE_PROVIDERS` | pexels,pixabay | 顺序固定，可配置禁某个 provider |
| `CLASSROOM_TTS_POLICY` | auto | auto/cloud/local/silent |
| `CLASSROOM_TTS_CLOUD_PROVIDER` | azure | 首发只支持 azure，可扩 adapter |
| `AZURE_SPEECH_KEY` / `AZURE_SPEECH_REGION` | 空 | 未同时配置则 cloud unavailable |
| `AZURE_SPEECH_ENDPOINT` | 空 | 可选官方资源域，不能由普通用户配置 |
| `CLASSROOM_TTS_VOICE_ZH` / `CLASSROOM_TTS_VOICE_EN` | 上述默认候选 | 必须经区域 voices list 验证 |
| `CLASSROOM_LOCAL_TTS_ENABLED` | 未设置时继承 VOICE_TTS_PROVIDER 是否为 melo；其余 0 | 设 1 才独立启动课堂本地回退，保留已启用的旧本地配置 |
| `CLASSROOM_TTS_LOCAL_FALLBACK` | 1 | local 未启用则最终 silent，不擅自安装模型 |
| `CLASSROOM_JOB_CONCURRENCY` / `CLASSROOM_OWNER_CONCURRENCY` | 2 / 1 | 受限作业调度 |
| `CLASSROOM_LLM_CONCURRENCY` | 3 | 页生成/复核共同限制 |
| `CLASSROOM_TTS_CLOUD_CONCURRENCY` | 2 | local 固定 1 |
| `CLASSROOM_JOB_TIMEOUT_SECONDS` | 900 | 累计活跃 deadline |
| `CLASSROOM_MAX_PAGES` / `CLASSROOM_MAX_REVISIONS` | 24 / 20 | schema 与 capability 同步 |
| `CLASSROOM_AUDIO_CACHE_MB` / `CLASSROOM_AUDIO_TTL_DAYS` | 500 / 7 | 每 owner 上限与未访问 TTL |
| `CLASSROOM_EXPORT_TTL_HOURS` | 24 | 导出过期可重建 |
| `CLASSROOM_RENDER_TIMEOUT_SECONDS` | 45 | node child 硬 deadline |
| `CLASSROOM_NODE_BIN` | node | 部署级可信可执行文件，不能来自 API 参数 |
| `CLASSROOM_RENDER_SCRIPT` | 仓库固定脚本绝对路径 | 启动解析，客户端/模型不可改写 |
| `CLASSROOM_API_DAILY_TTS_CHARS` | 100000 | 每用户每日云合成字符上限 |

其余 §15 数字统一置 `classroom/limits.py` 常量并通过 capability 返回用户相关限制；不在多份前后端配置复制。约束变动必须带验证用例，不能零散 magic number。

个人 `profile.prefs.classroom` 只接受有类型的 theme_id、pedagogy_id、voice_policy、voice_id、allow_local_fallback、captions、auto_advance、low_stimulus、pause_on_hidden；tts_speed 沿用既有值。`api/v1/user.py` 必须补新字段白名单及类型校验，旧 prefs 键保持兼容。

### 20.2 部署步骤

1. 在隔离测试环境安装已有 backend/frontend 依赖；固定 lockfile，构建 frame assets；确认部署镜像/机器含 Node、匹配 Playwright 包与 Chromium 系统库。
2. Chromium 以专用服务账号、可用 sandbox 运行；若当前 systemd 限制不支持，先调整并在 readiness 标出，不默认加 `--no-sandbox` 逃过验收。
3. `deploy/edu-backend.service` 继续 `--workers 1`，增加课堂脚本只读路径和浏览器缓存/临时目录所需读写权限；课堂数据仍在已批准的 chat_history 范围内。Node 子进程继承最小环境，不传供应商密钥给 renderer。
4. `deploy/edu-voice-sidecar.service` 与 start.sh 按本地启用条件一致；检查实际 fallback 端口通过 VOICE_TTS_BASE_URL 传主服务。
5. nginx 对 job SSE 关闭 buffering/cache，read_timeout 至少 120s，透传 Authorization；已有 voice WebSocket Upgrade 保留。HTML/音频端点不加 public CDN 缓存。
6. 执行 ready：renderer、目录可写、schema supported 为课堂关键项；TTS/图片/搜索缺配只影响该能力，不让整个聊天服务 not_ready。GET ready 不打真实付费请求。
7. 管理员显式运行供应商 smoke；随后对测试账号/允许名单开放，再开默认入口。
8. 备份包括 classroom、trash、journal 和 notes 的一致快照；部署前停止写 worker/保存 checkpoint。密钥按既有安全备份流程，不混入课程导出。

不假设仅部署 FastAPI 时已存在前端 node_modules：部署文档必须明确准备 renderer 的最小 Node 包依赖与生成包；缺失则 capability 显式不可用，旧聊天可启动。

### 20.3 可观测性

结构化事件：classroom_job_created/stage_started/stage_completed/failed/recovered/cancelled/published、render_failed、tts_fallback、audio_cache_hit、lease_taken_over、classroom_archived/purged。只记录 ID/hash、阶段、耗时、字节/token/字符用量、错误码、模板/prompt/provider 版本；日志不记录全文讲稿、外部网页、学生回答、SSML、密钥或原始 CoT。

指标：生成成功率/阶段失败率、P50/P95、每课模型和图片/检索请求数、TTS 首段等待/缓存命中/回退率、队列长度、磁盘占用、恢复次数、取消后晚写拦截、SSE 重连率、layout overflow。已有 trace 仅增加摘要事件；私有教学产物保存到课堂数据区，不当作调试日志公开。

告警阈值初始：连续 5 次 cloud 配置/鉴权失败；过去 20 个 job 失败率 >20%；队列最老等待 >300s；磁盘可用空间 <1GB 或 owner 超额无法清；renderer 连续 3 次启动失败。告警走现有管理员视图/日志，本期不新增未经授权的邮件/聊天通知。

### 20.4 灰度与回滚

- 第 1 档：CLASSROOM_ENABLED=1 + 服务端课堂用户 allowlist，只给维护者测试账号；普通账号 capability.enabled=false。新增 `CLASSROOM_ALLOWED_USERS` 配置空为不限制，身份判断后再返回能力。
- 第 2 档：少量真实课程，至少覆盖两种学段、zh/en、云端/本地/文字三路、一个联网专题；满足 §19 后取消 allowlist。
- 内容/渲染发布失败时回退到该课上一 ready revision；不会覆盖正在上课的版本。
- 图片/研究供应商故障可以单独禁用；云端故障改 local/silent；播放器仍可读已发布课程与讲稿。
- 紧急关闭总闸：禁止新 job/run/合成，取消排队任务、停止新的后台外呼；允许已存在内容只读查看、导出、进度暂停保存和退出，避免开关导致用户无法保存离开。前端关闭“开始”，保留“查看讲稿”。
- 应用回滚不删新增目录，不降级改写 schema=1 数据；不认识该 schema 的旧版忽略课堂目录，旧聊天可继续。再次升级由恢复器处理已有 job。

## 21. 贯穿场景与完成检查表

### 21.1 标准示例路径

在“大学物理”工作区选择“动量守恒”→点“一键备课”→默认 15 分钟、概念精讲、学术清晰、教材为主、云端优先→获得 10 页左右的 HTML 课件和逐页讲稿→检查引用后点击开始→听到第 4 页时点“没听懂”→声音立即暂停、解释系统边界→点击继续原课从原段开头读→第 6 页提交随堂题→立即保留已受理答案，评价可后台完成→第 8 页退出→另一设备接管后从该页继续→结课回顾显示实际作答状态→保存私人课堂笔记→下载 HTML ZIP。

该路径中没有要求用户把 PPT 上传到另一平台、复制讲稿到另一个 TTS 页面、手工同步页码，或等待整课所有音频合成。后台功能复杂性不暴露成学生必须理解的技术选项。

### 21.2 无外部服务路径

有教材且没有搜索/图片/云端 key：仍可生成受教材支持的文字/公式/原创图形课件；已有 Melo 启用则本地授课，未启用则讲稿阅读。界面准确显示“仅教材”“本地语音”或“文字课堂”，不出现假图片、假引用、假播放进度。

用户明确要求“最新专题”但搜索不可用：任务停在明确 needs_input，提供“改为基础知识课程”或“配置/恢复联网后重试”；不能生成一份旧知识课却仍标最新。

### 21.3 发布前逐项签收

- [ ] 工作区入口自然、普通聊天路径保持可用，浅色/深色/窄屏有验收截图。
- [ ] 真正生成 HTML 展示文件；离线导出能打开，术语和公式渲染正确。
- [ ] 核心页讲稿明显补充因果、条件和例子；播放朗读讲稿而非 DOM innerText。
- [ ] 教学模板与视觉模板独立可选，修改/再生成是版本化操作。
- [ ] 教材授权、联网来源/日期、图片本地化和作者信息全链路可追溯。
- [ ] 云端优先、Melo 保留，速率/音量/字幕/回退真实可用。
- [ ] 打断、追问、恢复、跳页、刷新、后台暂停、多标签互斥均通过。
- [ ] 正式题答案不在课件/讲稿/音频预取泄漏，帮助后作答不会被当独立掌握。
- [ ] 观看行为不写学生能力结论；随堂答案复用唯一评价受理链。
- [ ] 重试/取消/重启/过期/并发/预算/磁盘满有确定可观察结果。
- [ ] 课程和工作区归档恢复、账号删除、孤儿清理无残留/晚写复活。
- [ ] 全量后端、前端 type/lint/build、课堂 E2E、现有关键回归和真实服务烟测有分别记录。
- [ ] DESIGN、部署、API、配置、许可证说明与实际完成的代码一致。

最终交付对象是可持续使用的一节课堂：可准备、可看、可听、可停、可问、可练、可恢复、可修改、可导出、可删除；各阶段的失败都不会破坏现有学习工作区。
