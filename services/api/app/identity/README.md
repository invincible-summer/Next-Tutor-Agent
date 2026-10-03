# identity — M0 身份基础设施

回答「用户是谁、数据属于谁、如何安全访问」：所有智能层共用的唯一可信身份入口与按账号物理隔离的数据命名空间——`resolve_student_id()` 是全系统唯一可信 student_id 来源。

## Owns

- `deps.py` — `resolve_student_id` / `require_user` / `optional_user` / `require_admin` FastAPI 依赖
- `access.py` — 总路由守卫 `require_api_access`（挂在整个 `/api/v1` 上，默认拒绝、白名单放行游客）
- `models.py` — `User` 模型与 `to_public_dict`（绝不返回 `password_hash`）
- `security.py` — JWT 签发/解析、bcrypt 哈希
- `config.py` — `AUTH_MODE` / `AUTH_JWT_SECRET` / `AUTH_BCRYPT_ROUNDS`、默认密钥拒启守卫
- `store.py` — `users/accounts.json` 账户存储（原子写）
- `avatars.py` — 私有头像（256×256 PNG，无公开静态 URL）

## Does not own

- 学习数据语义：画像/评价/记忆等归各智能层；identity 只决定「这些数据属于谁、谁能读」
- 具体业务存储结构（会话/工作区见 conversation 域）与存储根布局（`core/paths.py`）
- admin 路由里各领域策略面（`/admin/llm-policy`、`/admin/ocr-policy` 等）的业务语义
- 游客运行时与清理实现（`core/guest_runtime.py` 等 core 文件）；注销级联语义在 `core/account_data.py`、孤儿扫描在 `core/orphan_cleanup.py`

## Design

JWT 唯一事实源、游客白名单、管理员引导、注销级联与数据隔离边界的完整设计见
[docs/architecture/identity.md](../../../../docs/architecture/identity.md)。

## Tests

- `services/api/tests/test_security.py` / `test_security_hardening.py` — 注册/登录/JWT/限流/XFF/默认密钥拒启
- `test_delete_account.py` / `test_admin_account_data.py` — 注销级联与管理员清除
- `test_guest_access.py` / `test_guest_cleanup.py` — 游客策略、白名单与清理
- `test_user_avatar.py` — 头像私有性；`test_bootstrap_readiness.py` — 启动引导
- `test_orphan_cleanup.py` — 孤儿数据治理；`test_assessment_identity.py` / `test_submission_identity.py` / `test_session_isolation.py` — 伪造 student_id 与归属隔离回归；`test_admin_public.py` — public 命名空间写边界

## Key entry points

- `deps.py::resolve_student_id` — 全系统唯一可信学生标识（请求体里的 `student_id` 一律忽略）
- `access.py::require_api_access` — 每个请求的第一道门
- 路由面：`api/v1/auth.py`、`api/v1/user.py`、`api/v1/guest.py`、`api/v1/admin.py`
