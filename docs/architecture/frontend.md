# frontend — Web 学习工作台（Next.js App Router）

面向学生的全部浏览器 UI：以对话为核心的「纸墨书院」学习空间，聚合后端 M0–M10 各模块的 REST/SSE/WebSocket API，并提供 GitHub Pages 只读静态演示形态。

## Purpose / Scope（职责与边界）

- 学生学习空间的页面与交互：落地页、登录/注册、对话工作台、课程（课堂）、笔记、总览、知识图谱、编排、测评、记忆、资料中心、图示库、归档、洞察、计划、画像、账户、设置、管理台、使用文档。
- 「纸墨书院」设计体系：三层设计令牌、浅深双主题、双语文案、证据四态色与页面三态规范的统一实现。
- 前端 API 层：`apiFetch`（JWT 注入、请求去重、超时护栏）+ SSE/WS 流式客户端 + 各模块投影客户端与类型定义。
- 只读静态演示形态：`NEXT_PUBLIC_DEMO_MODE=1` 时 `output: export` 导出由 synthetic fixtures 构建的快照站（ADR-0005）。
- 不负责：任何业务规则与数据持久化——写路径全部经后端 API；服务端运行数据全部在后端 `NEXT_TUTOR_DATA_DIR` 单根之下（ADR-0002），前端不拥有任何服务端存储。

## Owned code（拥有的代码路径）

`apps/web/`（pnpm workspace，包名 `frontend`）：

| 路径 | 职责 |
|------|------|
| `src/app/layout.tsx` + `globals.css` | 根布局、预水合主题/字号脚本（`edu-theme-init`）、设计令牌（`@theme inline`）与 `motion-*` 动效类 |
| `src/app/page.tsx` + `landing-strings.ts` | `/` 落地页（`components/landing/` Hero/Features/Marquee 等） |
| `src/app/login/`、`src/app/register/` | 认证页（M0，`components/auth/AuthShell`） |
| `src/app/(workspace)/` | 路由组：`layout.tsx`（鉴权门控 + AppShell）、`loading.tsx`（PageSkeleton）及各模块页（见 Public contracts 路由表） |
| `src/components/ui/` | 设计系统原语：`Button`/`Card`/`Badge`/`Modal`/`Drawer`/`Input`（含 `Field`/`Textarea`/`FIELD_CLS`）/`Hint`/`Pager`/`Tabs`/`Toast`/`Progress`/`Stat`/`EmptyState`/`AnchoredPopover` 等 |
| `src/components/shell/`、`sidebar/` | `AppShell`/`SideNav`/`TopBar`/`AccountMenu`；工作区 + 会话边栏（`SessionRow`/`WorkspaceFiles`/`InlineEdit`…） |
| `src/components/chat/` | 对话工作台组件群：`ChatInput`、`ChatMessage`（含 `StreamingMessage`）、`ChatMaterialsPanel`、`ToolCallCard`、`QuizCard`、`ThinkingBlock`、`LibraryPickerModal`、`VoiceCallLayer`、`markdown.tsx`（KaTeX/GFM 渲染） |
| `src/components/classroom/` | 课堂编辑与播放器（`LessonEditor`/`BlockEditor`/`SlideFrame`/`player/`、主题与呈现风格） |
| `src/components/pages/` | 各模块页组装（admin/assessment/dashboard/insights/knowledge/memory/notes/orchestration/plan/profile/resources/settings 子目录） |
| `src/components/assistant/`、`workspace/`、`auth/`、`charts/`、`landing/`、`guest/`、`learning-evaluation/`、`diagrams/`、`quiz/`、`shared/` | 站内助手面板（见 [site-assistant.md](./site-assistant.md)）、工作区设置弹窗、认证外壳与头像、SVG 图表（`Bars`/`Donut`/`Sparkline`）、落地页组件、访客学习、统一评价面板、图示素材库、题目插图、共用概念选择器 |
| `src/components/UIProvider.tsx`、`DemoReadOnlyDialog.tsx` | 全局 provider（Toast/主题/语言跨标签页同步）与演示只读拦截弹窗 |
| `src/lib/api-fetch.ts`、`api.ts` | `apiFetch` 统一请求层；`API_BASE` 单一事实源 + REST 客户端 + `streamChat` SSE AsyncGenerator |
| `src/lib/api-modules.ts`、`api-classroom.ts`、`api-notes.ts`、`api-diagrams.ts`、`api-diagram-materials.ts`、`api-illustrations.ts` | M2–M7 投影、M4 CAT、课堂（含 SSE 进度流解析）、笔记、图示库、插图 API 客户端 |
| `src/lib/types*.ts` | 前端类型契约：`types.ts`、`types-modules.ts`、`types-notes.ts`、`types-classroom.generated.ts` |
| `src/lib/store.ts`、`auth-store.ts`、`ws-settings.ts`、`store-notes.ts`、`assistant/store.ts` | zustand 状态（见 State & storage） |
| `src/lib/i18n.ts`、`i18n-page.ts`、`labels.ts`、`evaluation-labels.ts`、`format.ts` | 双层 i18n（全局词典 + `makePageT` 页面级词条）、标签与格式化 |
| `src/lib/nav.ts`、`cn.ts`、`markdown-toc.ts`、`use-unsaved-changes.ts`、`chat-drafts.ts`、`quiz-drafts.ts`、`guest-session.ts` | 导航配置（`NAV` 分组/模块徽章/adminOnly/classroomOnly）、类名合并、文档目录、未保存保护、草稿与访客会话 |
| `src/lib/voice/` | `useVoiceCall`（语音通话 WS 客户端 + 板书状态）、`browser-recognition`（课堂插问/助手朗读的 Web Speech 封装） |
| `src/lib/demo.ts`、`demo-fetch.ts`、`demo-routes.ts` | Pages 演示模式：`DEMO_MODE`/`siteUrl`/`guardDemoAction`、静态快照 fetch 替身、`demoRoutes()` 构建期路由枚举 |
| `next.config.ts` | basePath/`output: export`（demo）、`/api/*` 回退 rewrite、`/resources` 307 重定向、CSP 与安全响应头 |
| `playwright.config.ts` / `playwright.pages.config.ts`、`tests/{e2e,pages,unit}/`、`scripts/`（构建/渲染工具） | 单一产品 E2E、静态演示验收与用例、node 单测与构建脚本（见 Tests / acceptance） |

## Public contracts（对外契约）

### 页面路由与深链

`(workspace)` 路由组内（经 `AppShell`）：`/chat/[[...sessionId]]`（catch-all 唯一事实源）、`/course`（课堂 Hub，受 `CLASSROOM_ENABLED` 隐藏）、`/notes/[[...noteId]]`、`/dashboard`、`/knowledge`、`/orchestration`、`/assessment`、`/memory`、`/resources/files` 与 `/resources/textbooks`（`/resources` 在 `next.config.ts` 307 落到 files，兼容 `?tab=textbooks` 旧深链）、`/diagram-library`、`/archive`、`/insights`、`/plan`、`/profile`、`/account`、`/settings`（`?section=` 分类深链）、`/admin`（仅 admin）、`/docs`（匿名可读的使用文档）、`/workspaces/[workspaceId]/classroom`（`/[lessonId]`、`/learn/[runId]`）。组外：`/` 落地页、`/login`、`/register`。

- 对话深链契约：`/chat?q=<问题>` 预填不发送；追加 `&send=1` 在裸 `/chat` 自动发送后 `history.replaceState` 清参（StrictMode 安全）。
- 工具助手：`/tools` 工具目录与 `/tools/illustration?session=<id>` 情景配图工作台均为静态路由；需要登录，会话在服务端恢复。产品合同见 [tool-assistant.md](./tool-assistant.md)。
- 概念/任务/复盘等跨模块跳转（图谱双 CTA、编排行动按钮、周复盘）均经此契约，消息携带概念上下文；助手 `NavigationTarget` 深链走 `lib/assistant/routes.ts` 白名单。

### API 消费面

- 一切请求经 `apiFetch`（仓库规则）：JWT `Authorization` 注入、同飞行中请求去重、幂等 GET 30s 超时护栏、评价写 409 语义等待重试、`trustedRequestUrl` 目标校验。
- `API_BASE` 三形态单一事实源：`NEXT_PUBLIC_BACKEND_URL` 直连 / 同源相对 `/api/v1`（nginx 反代）/ demo basePath。
- 未设 `NEXT_PUBLIC_BACKEND_URL` 且请求经过 Next.js 时，`/api/*` 回退 rewrite 使用 `experimental.proxyTimeout=240_000`，覆盖普通出题最多 90 秒文字阶段、独立 120 秒配图阶段及传输余量；`next dev` 与 `next start` 均适用。CAT 补图使用异步任务，客户端任务观察时限独立为 150 秒。
- 流式：`api.ts::streamChat`（fetch reader 解析 SSE，原生 EventSource 不能带 Authorization）；课堂进度复用同一 SSE 帧解析器；语音通话为 WebSocket JSON 控制帧 + 二进制音频帧（协议见 [voice.md](./voice.md)、[conversation.md](./conversation.md)）。
- 后端契约文档：REST/SSE/WS 端点由各后端模块文档拥有（[backend-runtime.md](./backend-runtime.md) 及各域文档）；前端 `types*.ts` 与后端 schema 保持同步（assistant/classroom 为生成文件）。

### 设计系统约定（团队强制）

表单控件用 `ui/Input` 原语（`Input`/`Textarea`/`Field`/`FIELD_CLS`）；条目列表统一 `ui/Pager`（`paged()` 客户端切片，默认 5 条/页，页码可输入跳页）；浮层入场用 `motion-modal`/`motion-drawer`/`motion-pop` 类（reduced-motion 下停用）；长表单进 `Modal` 而非页内卡片。

## State & storage（状态与存储布局，含 runtime data 路径）

前端不拥有任何服务端存储；状态分三层：

- **zustand store**：`lib/store.ts`（`useUIStore` 学段/语言/主题/字号/侧栏、`useChatStore` 会话与消息、`useEvaluationCacheStore`）、`lib/auth-store.ts`（token/user/authRequired，水合并行；账户默认配图方式 `quiz_illustration_mode` 支持 `v1|v2|v3`，缺省 V1）、`lib/ws-settings.ts`（工作区设置弹窗目标 + `WS_CHANGED_EVENT`/`SESSION_CHANGED_EVENT` 广播）、`lib/store-notes.ts`、`lib/assistant/store.ts`。store 初始化器不读 localStorage（SSR 水合安全），mount 后 `hydrateClient()` 恢复。
- **浏览器持久化**：localStorage——`edu-agent-token`（demo 模式换用独立 `edu-agent-pages-demo-token`）、`edu-agent-lang`、`edu-agent-theme`、`edu-agent-fs`（字号倍率，经 `--fs-scale` 驱动根字号）、`edu-agent-grade`/`edu-agent-output-lang` 等偏好、对话与答题草稿（`chat-drafts`/`quiz-drafts`，登出清空）、课堂折叠分组等页面级 UI 状态；sessionStorage——对话草稿正文（登出/换账号时随 `clearAllDrafts` 清空）。头像经 apiFetch 认证后转临时 blob URL，换号/退出/卸载时撤销，不做本地持久化。
- **运行数据**：全部由后端落在 `NEXT_TUTOR_DATA_DIR` 单根（ADR-0002）；E2E 用隔离 scratch 目录，绝不落仓库。Pages 演示快照由后端导出流程在 storage sandbox 中读取 git 跟踪的 synthetic fixtures 生成（ADR-0001/0005），产物不入库。

## Main flows（关键流程）

### 鉴权水合与门控

`(workspace)/layout.tsx` 并行发起 `/auth/status` 与 `/auth/me`；`loaded` 与 `statusLoaded` 双落定才渲染，未登录不闪屏。`AUTH_MODE=1` 且未认证时跳 `/login?redirect=`；访客可用的页面仅 `/chat` 与 `/assessment`（`guestAllowed`），`/docs` 匿名可读。`/admin` 前端仅藏入口，硬门在后端 `require_admin`。

### 对话工作台

`/chat/[[...sessionId]]` catch-all 是会话 URL 唯一事实源；世代号守卫丢弃切换会话后迟到的上一会话流。流式渲染：本地累积 + 50ms 节流 flush + `React.memo` 消息组件 + pinned 滚动；`answer_delta`/`tool_start`/`tool_result` 等事件驱动 `StreamingMessage`、`ToolCallCard` 与 `QuizCard`（详见 [conversation.md](./conversation.md)）。资料右侧栏按「工作区公共资料 / 本对话引用教材 / 本对话上传文件」分组，仅消费后端 `material_sources` 结构化元数据，`knowledge_search` 卡先渲染结构化命中来源再展示片段，不从模型文本猜来源。

### 语音通话（沉浸式电话模式）

chat 页右上角电话按钮为唯一入口（`GET /voice/status` 决定显隐）；通话不弹对话卡，语音轮次实时写入消息流（转写即用户消息、`answer_delta` 节流进 pendingAnswer、`turn_end` 落定，与文字轮共用渲染路径与世代守卫）。`VoiceCallLayer` 顶部三块等分「板书」黑板只记块状公式段，与实际播放同步挂板；表格经 `board_table` 事件整版即时呈现并驻留；右上角手机模拟仅装饰。在途轮次挂断进入 drain 收尾，任何路径不允许聊天流悬空卡死。协议细节见 [voice.md](./voice.md)。

### 资料中心与列表分页

教材库/文件库双 Tab 路由段化；教材构建状态条件轮询（building 2s / ocr_waiting 15s，空闲零轮询），焦点与 `WS_CHANGED_EVENT` 驱动刷新。所有条目列表走 `ui/Pager`：页码为可输入框（Enter/失焦提交、自动钳位、Esc 取消），回源后条数变少先钳位页码再切片；情景记忆时间线按日分组、一页一天、「加载更多」向服务器取更早分组。

### 工具助手与情景配图

一级导航「工具助手」与测评、资料并列。情景配图工作台包含会话列表、聊天轮次和成果预览；V1/V2/V3 每轮选择，V2/V3 可打开素材 Modal 搜索、筛选及多选。未选时由模型提出需求、服务端检索。会话和素材列表使用共享 Pager，任务通过 `apiFetch` 观察，切会话/换账户防止迟到响应覆盖；成功图沿用规范化 SVG 展示，支持历史版本作为修改基础和下载 SVG。演示模式只显示只读入口，不生成图片。服务端合同由 [tool-assistant.md](./tool-assistant.md) 拥有。

### i18n 与主题

双层 i18n：`lib/i18n.ts` 全局词典（zh/en）+ 页面目录 `strings.ts` 经 `makePageT` 合并（WeakMap 缓存翻译函数引用）。`UIProvider` 恢复偏好、同步 `<html lang>`、监听 `storage` 事件实现跨标签页语言切换；学段等枚举只翻译显示标签，API token 保持原值。主题偏好 light/dark/system 持久化并跟随 `prefers-color-scheme`；预水合脚本避免闪白。

### Pages 演示形态

`NEXT_PUBLIC_DEMO_MODE=1` + `NEXT_PUBLIC_BASE_PATH` 时 Next `output: export`，动态路由由 `demoRoutes()` 构建期枚举；`apiFetch` 切换 `demo-fetch.ts` 读静态快照，仅 example 登录/退出允许 POST，修改与 AI 请求在发网前拒绝；UI 侧 `guardDemoAction`/`DemoReadOnlyDialog` 前置拦截，请求层只读校验是最终边界（ADR-0005；配置与发布见 [../operations/pages-demo.md](../operations/pages-demo.md)）。

### 加载性能

`start.sh` 默认 `FRONTEND_MODE=prod`（按需 `next build --webpack` + `next start`，源码/后端端口变化自动重建，`REBUILD=1` 强制；`./start.sh dev` 回热重载）。路由级分包：重页面 `next/dynamic` 懒加载 + `(workspace)/loading.tsx` 统一骨架；`GET /sidebar` 组合快照（ETag/304）替代侧栏 N+1；`GET /chat/sessions/{id}?tail=N` 渐进加载（首屏最近 40 条 + `.msg-cv` content-visibility）。

## Dependencies（依赖与被依赖）

- 后端 FastAPI `/api/v1`（REST/SSE/WS）是唯一数据来源；语音 WS 经后端 voice 端点；站内助手面板消费 assistant API（[site-assistant.md](./site-assistant.md)）。
- 运行时库：next 16 / react 19 / zustand 5 / tailwindcss v4 / lucide-react / react-markdown + remark-gfm + remark-math + rehype-katex + katex / @fontsource/playfair-display（落地页英文品牌行）。
- 构建与编排：pnpm、`scripts/dev/start.sh`（端口回退与构建决策）、课堂静态资产 `pnpm build:classroom`（`tsconfig.classroom.json` + `scripts/build-classroom-assets.mjs`）。
- 部署形态：同源 nginx 反代（`/api/*`）或 `NEXT_PUBLIC_BACKEND_URL` 直连；CSP `connect-src` 按后端 origin 收敛。
- 被依赖：无——前端是纯消费者，最终交付物由 `deploy/` 模板与 Pages 演示导出流程使用。

## Invariants / security boundaries（不变量与安全边界）

- **一切请求经 `apiFetch`**：JWT 只注入可信目标——`trustedRequestUrl` 只对相对路径、页面 origin 与显式配置的后端 origin 附加凭证，防响应中的绝对 URL 把 Bearer token 外送到攻击者主机；协议相对 `//host` 按外域处理。
- token 存 localStorage（与 CSP 纵深组合）；demo token 与正式 token 使用不同存储键。前端代码只读 `NEXT_PUBLIC_*` 变量，不含任何密钥。
- `next.config.ts` 全站安全头：CSP（外域脚本/样式/连接一律拒绝、dev 才放开 `unsafe-eval`、`microphone=(self)`）、`frame-ancestors 'self'` + XFO、`object-src 'none'`、`base-uri`/`form-action 'self'`。
- 鉴权门控不闪屏：`statusLoaded` 与 token 校验双落定才渲染工作区；访客面收敛到 `/chat`、`/assessment`；`/admin` 后端硬门。
- 水合安全：store 初始化器禁止读 localStorage；主题/字号经预水合脚本先行落定。
- 会话隔离：chat 世代号守卫防串会话；深链自动发送后立即清参防刷新重发。
- 演示只读：修改/AI 操作 UI 前置拦截 + 请求层最终边界双层校验。
- 组件契约：列表必用 `Pager`、表单必用 `Input` 原语、浮层必用 `motion-*` 类（仓库规则，见 [../../AGENTS.md](../../AGENTS.md)）。

## Configuration（环境变量与开关）

| 变量 | 默认 | 说明 |
|------|------|------|
| `NEXT_PUBLIC_BACKEND_URL` | 未设 | API origin：直连后端；未设时同源相对 `/api/v1`（rewrite/反代兜底） |
| `NEXT_PUBLIC_DEMO_MODE` | 未设 | `1` 开启 Pages 静态演示：`output: export`、禁用 rewrites/redirects/headers |
| `NEXT_PUBLIC_BASE_PATH` | 空 | demo 子路径（当前为 `/Next-Tutor-Agent`，与仓库名一致；权威值见 `.github/workflows/pages.yml`），`siteUrl()` 统一拼装 |
| `BACKEND_URL` | `http://127.0.0.1:8000` | dev rewrite `/api/*` 回退目标（不进客户端包） |
| `NODE_ENV` | — | development 时 CSP 追加 `unsafe-eval`（react-refresh） |
| `FRONTEND_MODE` / `REBUILD`（start.sh 侧） | `prod` / 未设 | `prod`=一次构建 + `next start`；`dev`=热重载；`REBUILD=1` 强制重建 |

后端能力开关（`CLASSROOM_ENABLED`、voice、assistant 等）经各能力 API 如实投影到前端显隐，前端不自行猜测。

## Observability（trace/日志/指标）

- 后端每响应带 `X-Process-Time` 头（>1s 终端告警），前端开发期可直接观察慢端点；上传失败经 `uploadFailures` 转成明确用户提示。
- 用户反馈统一走 `ToastProvider`：成功 3s / 失败 6s 自动关闭，连续操作替换当前通知。
- E2E 默认 `trace: retain-on-failure` + `screenshot: only-on-failure`；后端就绪契约 `/api/v1/ready` 为浏览器流启动条件，避免把 bootstrap 期误判为产品故障。

## Tests / acceptance（测试索引）

apps/web 下的 Playwright E2E 与 node 单测（环境搭建、浏览器回归与 CI 所有权见 [../../docs/development/testing.md](../../docs/development/testing.md)）：

- `pnpm check`：`tsc --noEmit` + eslint + node 单测（`tests/unit/test-classroom-player.mjs`、`test-i18n.mjs`（词典键/插值/翻译函数契约）、`test-assistant-navigation.mjs`、`test-demo-fetch.mjs`、`test-illustration-enrichment.mjs`、`test-e2e-runtime.mjs`（构建缓存和端口隔离））。
- `pnpm test:e2e`：唯一产品浏览器入口，生产 frontend + 当前 backend 源码 + fake LLM，固定单 worker、零重试。`run-e2e.mjs` 管理服务进程组、独立临时数据根和运行锁；退出/中断先停服务再清理数据。`.next-e2e` 构建缓存校验源码、资源、配置、依赖锁及构建环境，不复用开发 `.next`。用 CLI 选择 spec/grep/`--headed`；CI 使用同一入口指定关键文件。`pnpm test:pages`（`tests/pages/`）另验静态发布产物。真实模型管线验收见 [测试维护](../development/testing.md)。
- `pnpm build`（`next build --webpack`）作为生产构建验证；课堂播放器另有 `pnpm test:player` 与 `build:classroom` 资产管线。
- 用例目录 `tests/e2e/` 覆盖：鉴权隔离、课堂全流程（创建/编辑/播放/恢复/安全/导出/插问）、课程 Hub、笔记、图示库与素材、BM25 检索、题目卡契约与插图、开放作答、语音 smoke、访客接入、严格 QA。

## Related ADRs

- ADR-0001 source-only——仓库不分发教材及派生数据；前端演示内容只能来自 synthetic fixtures。
- ADR-0002 运行数据单根——前端不拥有服务端存储，一切运行数据由后端落在统一数据根。
- ADR-0005 Pages demo 仅 synthetic——静态演示站由 synthetic fixtures 快照构建，只读边界在请求层。

图示素材创作 Modal 支持声明式参数规范 JSON、模板控件及独立预览值，AI 草稿可返回同一规范；测评配置支持 V1/V2/V3 单次覆盖、出题提示词、工作区无概念自动检索和临时出题。V1 为旧版模型自行绘图，V2 为素材库参数组合，V3「素材辅助创作」允许模型选择、改造和组合 SVG 素材，并补画素材库中缺少的元素。账户设置可保存三种版本；测评内切换其他偏好不会重置本次选择。V1 题图复核开关保留，V2/V3 则显示自动图文审查说明并隐藏该开关；两者始终对实际图面进行一次合并审核。公开图片兼容 schema 1/2/3，V3 题图沿用 schema 3。保存与题图实例化规则由 [diagrams-illustration.md](./diagrams-illustration.md) 拥有。

测评配图使用按账户/题号/revision 共享的请求，POST 最多 120 秒、整个任务观察最多 150 秒；轮询 GET 不超过剩余时间。进行中缓存不会被淘汰，换号或迟到请求不能覆盖新状态。答题卡与作答反馈共用阶段和失败状态展示，读取公开失败码，不展示私有审核诊断。必要题图未完成前阻止作答，补充题图允许文字题先答。服务端 `retryable=false` 保持不可重试；可重试失败先读取原任务，已经 ready 直接恢复冻结图，仍运行则继续观察，真正 failed 才发起新运行，避免网络超时后重复生图。反馈页仅在本题仍为进行中测评的当前题时提供重试；测评结束后继续观察已启动任务，但不启动或重试生成。总结和历史题目只读取冻结图及现有任务，不提供生成入口。
