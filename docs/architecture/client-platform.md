# Client Platform（共享客户端包）

> 决策记录：[ADR-0009](../adr/0009-root-monorepo-shared-packages.md)、
> [ADR-0008](../adr/0008-expo-react-native-mobile.md)。

Web 与 Expo/React Native 移动端不复制 API 代码：传输、契约、设计令牌与
跨端纯逻辑全部收敛到根 pnpm workspace 的 `packages/*`，平台只剩 adapter。

## 仓库结构

```text
.
├── apps/mobile/               # Expo Native（@next-tutor/mobile）
├── apps/web/                  # Next.js（@next-tutor/web）
├── packages/
│   ├── contracts/             # 服务端 Pydantic → TS 契约（生成物 + 协议类型）
│   ├── api-client/            # 平台无关 REST/SSE 客户端
│   ├── domain/                # 跨端纯逻辑（图谱布局、语义映射、电路求解）
│   ├── design-tokens/         # 设计令牌事实源（tokens.json）
│   └── i18n/                  # 跨端文案协议（骨架）
├── scripts/contracts/         # 契约生成与 lint（见该目录 README）
└── scripts/dev/generate_design_tokens.mjs
```

- 唯一 lockfile 在仓库根（pnpm 11，`allowBuilds` 显式决策）。
- 共享包 TS 全开 `noUncheckedIndexedAccess` / `exactOptionalPropertyTypes`；
  `apps/web` 按包暂缓（tsconfig 内有注释标注），后续统一。
- Node `>=22.13 <23`；共享包测试用 Node 内置 `node --test` 配合仓库 TypeScript loader，不引入 Vitest。

## packages/contracts

- 事实源是 `services/api/app/schemas/*` 的 `PUBLIC_TYPE_MODELS` 模型清单；
  `scripts/contracts/generate_types.py` 生成
  `src/generated/{classroom,assistant,illustration}.ts`（`--check` 防漂移）。
- 同名模型（ErrorBody 等）通过子路径导出隔离：
  `@next-tutor/contracts/classroom` / `/assistant` / `/illustration`。
- illustration 公开契约只含 scenario session/turn/job/revision 投影与公开
  stage；私有材料（DiagramSourceV2/V3、authoring_gold、contract_hash）不进包。
- `src/protocols/chat.ts`：chat SSE 事件 discriminated union（12 成员）。
- `scripts/contracts/export_openapi.py` 导出确定性 OpenAPI 快照；
  `check_contracts.py` 契约 lint（response_model 缺口棘轮，基线只允许收缩）。

## packages/api-client

`createApiClient(ApiClientConfig)` 注入一切平台相关物（`baseUrl`、
`fetchImpl`、`tokenProvider`、`guestTokenProvider`、`clientMetadataProvider`、
`requestIdFactory`、`onUnauthorized`）；包内不读环境变量、window、SecureStore。

传输管线（`src/transport.ts`）：

- 每个请求带 `X-Client-Platform/Version/Build` 与 `X-Request-ID`（重试间稳定）；
- GET/HEAD：网络错误与 429/502/503/504 有界重试（≤2 次，jitter，尊重
  Retry-After 上限）；写请求仅当 endpoint 显式 `retryWrites` 且携带
  `Idempotency-Key` 时重试；
- 401：`onUnauthorized` 单飞（并发 401 共享一次 refresh）；只有 token 变化才
  重试一次，否则立即抛 `UnauthorizedError`。Web adapter 的钩子实现为企业
  刷新轨：先用 HttpOnly `edu_refresh` cookie 静默换新 access token（成功即
  更新内存令牌、transport 因 token 变化自动重试），失败才降级派发
  `edu-auth-expired`/`edu-access-changed` 事件；
- 409：透传为 `ConflictError`；仅当调用方声明 `waitForConflict`（如
  `evaluation_pending`）时有界重轮询；
- 超时：默认读超时（`defaultReadTimeoutMs`，Web 30s）**只覆盖不带 signal 的
  GET/HEAD**——带 AbortSignal 的读与一切 POST 默认无限等待。因此对"服务端
  承诺毫秒级确定性返回"的域客户端必须逐调用声明 `timeoutMs`；超时会转为可重试的
  `NetworkError` 而不是 UI 里的永久转圈。长耗时端点（出题、配图）保持
  不设超时或按端点声明更长时限；
- 响应体解析支持 `json`/`text`/`bytes`/`none`——`bytes` 走
  `response.arrayBuffer()`（语音 WAV、课件音频剪辑、导出 zip、PDF 页快照、
  原件下载），平台层再把字节变成 Blob URL/文件。

错误信封（`src/errors.ts`）：识别 `detail.error.{code}`、扁平 `error.{code}`、
`detail` 字符串与 speech 端点的裸 `detail.code` 四种形状；未知形状降级
`status_<n>`，绝不静默吞错。classroom/assessment 错误码（`classroom_disabled`、
`revision_conflict`、`lease_conflict`、`evaluation_pending`、`material_*` 等）
经 `ApiError.code`/`ConflictError.code` 透传，域内不另造包装类。

SSE（`src/sse/decoder.ts`）：跨 chunk 行边界、UTF-8 多字节边界、多行 data、
heartbeat 注释、无结尾空行的尾帧 flush；reader lock 总是释放。chat 事件映射在
`src/sse/events.ts`（`event:` 名优先，裸 data 默认 `message`）。

域方法（`client.<domain>`）：`auth` / `chat`（流 + 会话投影）/ `workspace` /
`guest`（访客会话）/ `illustration`（V1/V2/V3 题图读模型，中断映射
`run_interrupted`）/ `tools.illustration`（情景配图，见下）/
`assessment`（CAT + 单题提交 + `/quiz` 会话内练习；`next()` 默认接
`evaluation_pending` 有界重轮询）/ `capabilities`（产品能力探针，点号键
`illustration.quiz` 等按 wire 原样保留）/ `classroom`（课件创作 + 播放 run/
lease/进度/音频 + checkpoint；类型 type-only 引
`@next-tutor/contracts/classroom`；`jobEvents` 为 SSE AsyncGenerator，断线重连
归调用方）/ `notes`（vault CRUD + 409 乐观并发 + agent SSE 流 + 字节导出）/
`library`（资料库 + 嵌套 `textbooks` 教材库）/ `learning`（orchestration
计划/任务/复习）/ `diagrams`（自有 SVG 素材 + 只读公共图示目录）/
`voice`（ADR-0012 服务端语音：能力/转写/合成）。契约类型一律 type-only 引入
`@next-tutor/contracts`（未生成的域用包内结构化类型 + `<T = 默认形状>` 泛型
逃生口）。共享包的 Node 测试通过仓库内 TypeScript loader 转译 `.ts`，不依赖
具体 Node 构建是否带原生 type-stripping。

multipart 约定：平台负责构造 FormData 实例并 append 文件部件（浏览器
`File`/RN `{uri,name,type}`），wire 字段名归共享层注入——`voice.transcribe`
补 `file`/`duration_ms`/`language`，`library.textbooks.upload` 补
level/scope/volume_overrides 等标量字段；纯 `files` 上传（workspace/
library/notes/classroom asset）由调用方整表传入。带 `Idempotency-Key` 的
classroom 写操作收显式 key 参数（只带头、不自动重试）；checkpoint submit 的
幂等键在 body。共享层还提供 `assistant`（typed action、handoff、workflow、通知与偏好）、
`evaluation`、`memory`、`profile`、`ux` 和 `archive`。`auth` 包含 access/refresh
轮换、principal、session 列表与撤销；`classroom.ensureQaSession` 为课堂插问提供
稳定的会话创建入口。admin 和浏览器 `/voice/ws` 保持 Web-only。

### 情景配图 observer 语义（tools/illustration）

- submit turn 必带稳定 `request_id` + `base_revision`（可选 `source_revision`）；
  V1 不提交素材，V2/V3 只提交 `{asset_id, version}`；
- POST 响应丢失时 `submitTurnWithRecovery` 经 session 投影按 `request_id`
  恢复同一逻辑轮次，绝不重复生成；
- `pollJob` 只把服务端终态（ready/failed）当事实：客户端超时产出
  `observation_stopped` 事件并停止观察，不本地伪造 failed；
- `shouldPause` 支持 App 退后台暂停轮询，回前台后下一个 tick 回源
  （`getJob`/`getSession` 即回源原语）；
- 409 busy/revision conflict、素材缺失/版本冲突、source revision missing 等
  统一映射为 `IllustrationApiError`（typed code 见 `src/errors.ts`）。

## packages/design-tokens

`data/tokens.json` 是唯一事实源（light/dark 色板、阴影、字体、4pt 间距、
radius、motion 时长、窗口断点类）；`scripts/dev/generate_design_tokens.mjs`
生成 `apps/web/src/styles/tokens.generated.css`（`--check` 防漂移）。移动端
直接 import TS 令牌，不生成 CSS。

## packages/domain 与 packages/i18n

- `domain` 只放跨端纯逻辑：知识图谱 DAG 布局算法、学习状态语义映射和电路实验室确定性求解；不含
  数据库业务、prompt、导航或平台 UI state；
- `i18n` 首版是 `Lang`/`Translator` 协议骨架；页面级 `strings.ts` 不整体搬迁，
  功能进入移动端时才把真正跨端的 key 迁入。

## Web 平台 adapter（渐进迁移）

`apps/web` 通过 `@next-tutor/*` workspace 依赖消费共享包
（`transpilePackages` 直接编译包内 TS 源）。chat/auth/workspace 与
illustration/tool-assistant 域迁移到 `@next-tutor/api-client`，其余域
（admin/trash/quiz/UX 等）保留 Web 本地实现并按域渐进迁移；
`apps/web/src/platform/` 持有浏览器 adapter（token 存取、demo 模式、
arbitrary-URL fetch 的凭据守卫）。移动端 adapter（`expo/fetch`、SecureStore）
已接入 `apps/mobile`，详见 [mobile-app.md](./mobile-app.md)。
