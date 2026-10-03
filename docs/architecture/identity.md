# identity — M0 身份基础设施

M0 回答三个问题：用户是谁、数据属于谁、如何安全访问；它是所有智能层（M1-M10）共用的唯一可信身份入口与按账号物理隔离的数据命名空间。

## Purpose / Scope（职责与边界）

- 账号生命周期：注册、登录、登出、账户资料、私有头像、自助注销（名下数据不可恢复清除）。
- 身份解析与信任边界：JWT 签发/校验、`resolve_student_id()` 作为唯一可信 student_id 来源、管理员角色（`User.role = admin`）与 `require_admin` 依赖、启动时管理员账号引导。
- 游客临时体验：管理员策略开关、不透明游客令牌（`X-Guest-Token`）、内存态游客运行时与到期/关闭回收。
- 数据治理：注销级联清除（`account_data.purge_account`）、孤儿数据扫描与清理（`orphan_cleanup`）、游客数据清理、登录/注册限流。
- 不负责：任何学习数据语义（画像、评价、记忆等由各智能层文档描述）；identity 只决定"这些数据属于谁、谁能读"。工作区/会话等具体业务存储的内部结构见 [conversation.md](./conversation.md)，存储根布局见 [backend-runtime.md](./backend-runtime.md)。

## Owned code（拥有的代码路径）

| 路径（`services/api/app/` 下） | 职责 |
|------|------|
| `identity/deps.py` | `resolve_student_id` / `require_user` / `optional_user` / `require_admin` 依赖 |
| `identity/access.py` | 总路由守卫 `require_api_access`（挂在整个 `/api/v1` 路由上）与鉴权错误封装 |
| `identity/models.py` | `User` 模型与 `to_public_dict`（绝不返回 `password_hash`） |
| `identity/security.py` | JWT 签发/解析、bcrypt 哈希 |
| `identity/config.py` | `AUTH_MODE` / `AUTH_JWT_SECRET` / `AUTH_BCRYPT_ROUNDS`、默认密钥拒启守卫、本机开发密钥生成 |
| `identity/store.py` | `users/accounts.json` 账户存储（原子写） |
| `identity/avatars.py` | 私有头像（256×256 PNG，无公开静态 URL） |
| `api/v1/auth.py` | `/auth/*` 端点 |
| `api/v1/user.py` | `/user/*` 端点（profile / avatar / account） |
| `api/v1/guest.py` | `/guest/*` 端点（session / textbooks / quiz/generate） |
| `api/v1/admin.py` | `/admin/*` 端点（账户、游客策略、数据治理、各策略面） |
| `core/guest_policy.py` | 游客策略读写（`chat_history/settings/guest_policy.json`） |
| `core/guest_runtime.py` | 单 worker 内存游客运行时（令牌、冻结题目、回收） |
| `core/guest_learning.py` | 游客态材料/题目快照（`KnowledgeStore(memory_only=True)`） |
| `core/guest_cleanup.py` | 旧游客文件清理与缓存失效 |
| `core/account_data.py` | `purge_account` 注销级联清除语义 |
| `core/orphan_cleanup.py` | 孤儿数据扫描/清理类别（每新增 per-user 存储根须同步登记） |
| `core/ratelimit.py` | 按 IP 固定窗口限流 + 登录失败按账号节流（与 [backend-runtime.md](./backend-runtime.md) 共享） |

前端耦合面在 `apps/web/src/`（`AuthShell` 品牌登录/注册页、TopBar 账户菜单、路由守卫、`apiFetch` 全局 token 注入），本文档只约束其依赖的契约。

## Public contracts（对外契约：API 端点/SSE/WS/数据结构）

前缀 `/api/v1`，除 `/guest/*` 外全部经 `require_api_access` 默认拒绝、白名单放行游客。

- 认证：`GET /auth/status`（含 `guest_allowed`、`using_default_secret` 仅管理员可见）、`POST /auth/register`、`POST /auth/login`、`POST /auth/logout`、`GET /auth/me`。
- 账户：`GET/PUT /user/profile`、`GET/PUT/DELETE /user/avatar`（仅认证本人）、`DELETE /user/account`（自助注销，需密码 + 输入「注销」二次确认）。
- 游客：`POST/DELETE /guest/session`（不透明令牌）、`GET /guest/textbooks`、`POST /guest/quiz/generate`（限流 20/min；仅服务器确认的公共教材 id）。
- 管理员（`require_admin`，401/403）：`GET /admin/users`、`POST /admin/users/{id}/clear-chat`、`DELETE /admin/users/{id}`（不可删 admin 含自己）、`GET/PUT /admin/guest-policy`、`GET /admin/guest-data` + `POST /admin/guest-data/purge`、`GET /admin/orphan-data` + `POST /admin/orphan-data/purge`；同文件还承载 `/admin/data-retention`、`/admin/public-trash*`、`/admin/classroom-health*`、`/admin/ocr-policy`、`/admin/textbook-pipeline`、`/admin/llm-policy`、`/admin/learner-evaluation-policy`、`/admin/prompt-memory-policy` 等策略面（语义属各领域模块）。
- 数据结构：`User`（id/email/password_hash/role/profile…），JWT 为 Bearer header；游客以 `X-Guest-Token` 头标识。

### 数据隔离边界（M0 定义、全系统遵守）

- 学习数据 `students/<student_id>.*` 全部按 id 物理分文件（画像/证据账本/教学日志/记忆/编排/UX/评估）。
- 会话历史只属本人；工作区创建打 `student_id` 戳、列表过滤、外人 404；资料库每用户一份（`chat_history/library/<sid>.json` + `data/<sid>/`）；题图产物 `illustrations/<sid>/` 隔离。"共享"始终指同一 owner 的多个对话间共享，而非跨用户。

### 前端契约（`apps/web/src/`）

`AuthShell` 品牌登录页 + 两步注册页（`?redirect=` 回跳互跳不丢；注册不采集学科）；TopBar「我的账户」菜单；路由守卫按实时游客策略与页面白名单重定向 `/login`；`apiFetch` 全局 token 注入（REST + SSE）；账户资料页与设置页自助注销（密码 + 输入「注销」双重确认）。

## State & storage（状态与存储布局，含 runtime data 路径）

全部位于单一数据根 `NEXT_TUTOR_DATA_DIR`（默认 `.runtime/data`，见 [backend-runtime.md](./backend-runtime.md) 与 ADR-0002）：

| 路径 | 内容 |
|------|------|
| `users/accounts.json` | 账户记录（bcrypt hash），原子写 |
| `users/avatars/<sid>/avatar.png` | 256×256 私有头像；注销清除与 orphan 扫描均覆盖 |
| `chat_history/settings/guest_policy.json` | 游客策略（管理员可写）；缺失或损坏时拒绝游客 |
| `.runtime/auth_jwt_secret`（数据根旁） | 未显式配置 `AUTH_JWT_SECRET` 时生成的本机开发密钥（0600） |
| `guest_<uuid>` 命名空间 | 游客数据落在各业务根（会话/转写/trace/上传等），无独立磁盘根 |

`students/<user_id>.*` 学习数据命名空间由 identity 的 id 语义派生，但文件本身归各智能层所有。

## Main flows（关键流程）

- **注册/登录**：注册两步（账号 → 学习信息）→ bcrypt（`AUTH_BCRYPT_ROUNDS`）落 `accounts.json` → 登录签发 JWT。限流双轨：按 IP 固定窗口 + 每账号失败 10 次/5 分钟；客户端 IP 只取 uvicorn 按可信代理解析后的 peer，应用层不解析 `X-Forwarded-For`。
- **身份解析（每请求）**：`require_api_access` 总守卫 → 有效 JWT 解出账号（`user_id == student_id`，自动获得独立 `students/<id>.*` 命名空间）；无 JWT 时须游客策略开启且带有效 `X-Guest-Token`，解析为独立 `guest_<uuid>`；无效/过期 JWT 返回 401，禁止降级游客。WebSocket（语音）在 accept 前验证登录票据。
- **管理员引导**：启动 lifespan 读 `ADMIN_EMAIL`/`ADMIN_PASSWORD`——不存在则创建；已存在则仅当密码通过该账号 bcrypt 校验才提升（防开放注册下抢注提权）。
- **游客临时学习**：前端仅放行 `/chat`、`/assessment` 独立游客页；令牌/聊天/题卡只存浏览器文档内存（不写 localStorage/URL）。后端 `guest_runtime` 单 worker 内存保持最多 512 位游客、每人 100 题、闲置 30 分钟回收；选择题确定性判分，不写证据账本/画像/图谱/记忆/计划。
- **注销（两入口同一语义）**：`DELETE /user/account` 与 `DELETE /admin/users/{id}` 都走 `purge_account`——会话/转写/trace/上传/工作区/资料库/回收站/笔记/学习档案/知识图谱逐层清空且不留空目录，账号记录最后删、中途失败可重试（幂等）；账号记录删除后 JWT 即失效。
- **孤儿数据治理**：`GET/POST /admin/orphan-data[/purge]` 扫描/清理测试残留、注销遗物、无引用 trace、失会话转写、空回收站目录；注册账号与 `public`/`student_default` 共享命名空间受保护。

## Dependencies（依赖与被依赖）

- 依赖：`core/atomic.py`（账户/策略原子写）、`core/paths.py`（存储根绑定）、`core/ratelimit.py`。
- 被依赖：全部 `/api/v1` 路由（`require_api_access` 挂在总路由上）、所有智能层（经 `resolve_student_id()` 获得命名空间）、WebSocket 语音、OpenAI 兼容门面（`COMPAT_API_KEY` 独立鉴权，不走 JWT）。

## Invariants / security boundaries（不变量与安全边界）

- **JWT 唯一事实源（铁律）**：任何端点的 student_id 只来自 `resolve_student_id()`；请求体/query 里的 `student_id` 字段仅为旧客户端兼容保留、一律忽略。
- 数据隔离：会话列表只返回本人；游客返回空列表，不能读取未盖身份戳的遗留会话；按 id 资源端点对外人 404（不泄露存在性）；工作区/资料库/题图产物均按 owner 物理分文件。
- 游客能力白名单：仅文字聊天、临时出题及本题批改；导航助手（含原公共 guide）、语音、上传、私有材料与完整学习模块均须登录。
- `to_public_dict` 绝不返回 `password_hash`；JWT secret 仅在 `identity/config.py` / `security.py` 使用。
- `AUTH_MODE=1` 下使用默认 JWT secret 拒绝启动；`/auth/status` 的 `using_default_secret` 只对已登录管理员如实披露。
- `users/`、`students/` 及全部运行数据被 `.gitignore` 覆盖（ADR-0001 source-only 仓库）。

## Configuration（环境变量与开关）

| 变量 | 默认 | 说明 |
|------|------|------|
| `AUTH_MODE` | `0` | 部署安全守卫；生产必须 `1`（默认 secret 拒启）。游客访问由管理员策略独立控制 |
| `AUTH_JWT_SECRET` | — | 未配置时测试/keyless 环境用固定默认值，本地部署生成 `.runtime/auth_jwt_secret` |
| `AUTH_BCRYPT_ROUNDS` | `12` | 密码哈希轮数 |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | — | 启动引导管理员账号 |

## Observability（trace/日志/指标）

- 鉴权失败、限流命中走标准 HTTP 错误码；不记录密码、token 明文。
- orphan-data / guest-data 清理接口返回实际删除量与部分失败明细，可重复执行。
- 账户与数据治理动作（clear-chat、purge）在管理端可见；无独立指标面。

## Tests / acceptance（测试索引）

`services/api/tests/` 下（扁平目录）：

- `test_security.py`、`test_security_hardening.py`（注册/登录/JWT/限流/XFF/默认密钥拒启）
- `test_delete_account.py`、`test_admin_account_data.py`（注销级联与管理员清除）
- `test_guest_access.py`、`test_guest_cleanup.py`（游客策略、白名单、内存运行时、清理）
- `test_user_avatar.py`（头像私有性与本人边界）
- `test_bootstrap_readiness.py`（启动引导）
- `test_orphan_cleanup.py`（孤儿数据扫描/清理/保护名单）
- `test_assessment_identity.py`、`test_submission_identity.py`（伪造他人/游客 student_id 无效的回归）
- `test_session_isolation.py`（会话归属隔离）
- `test_admin_public.py`（`public` 命名空间管理员写边界）

## Related ADRs

- ADR-0001 source-only 仓库（账户/运行数据不入库）
- ADR-0002 运行数据统一 `NEXT_TUTOR_DATA_DIR`
