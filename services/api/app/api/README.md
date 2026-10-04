# api — HTTP API 层

`/api/v1` 前缀下的全部 REST/SSE/WebSocket 端点：请求校验、鉴权边界（`require_api_access` / admin guard / JWT）与向业务层的分发。

## Owns

- `v1/router.py`：`/api/v1` 总路由，统一挂 `require_api_access` 并注册各域子路由。
- `v1/*.py` 各域 route 文件（以实际文件为准）：
  - 身份与账户：`auth.py`、`user.py`、`guest.py`、`admin.py`
  - 对话与工作区：`chat.py`（SSE）、`workspace.py`、`sidebar.py`
  - 测评交互：`quiz.py`、`assessment.py`、`assessment_grounding.py`、`assessment_illustration.py`、`learner_evaluation.py`
  - 学习数据投影：`student.py`、`memory.py`、`ux.py`、`orchestration.py`、`evaluation.py`、`trace.py`
  - 知识与资料：`knowledge.py`、`library.py`、`textbook.py`、`trash.py`、`notes.py`
  - 课堂与站内助手：`classroom.py`、`assistant.py`
  - 图示与语音：`diagram_library.py`、`diagram_materials.py`、`illustration_jobs.py`、`tool_illustration.py`、`voice.py`（WS）
  - 门面与健康：`health.py`、`compat.py`（OpenAI 兼容门面）、`docs.py`

## Does not own

- 业务逻辑：智能层在 `agents/`（见 [../agents/README.md](../agents/README.md)），领域实现与存储在 `core/` 及各领域包（`classroom/`、`diagrams/`、`illustration/`、`voice/`）。
- 身份判定本身：`identity/` 拥有 `resolve_student_id()`（唯一可信学生标识）与鉴权实现；route 层只装配 guard。

## Design

- API 面分组概览、鉴权矩阵、限流与生产基线：[docs/architecture/backend-runtime.md](../../../../docs/architecture/backend-runtime.md)
- `/chat/*` SSE 事件契约、帧序与会话绑定双保险：[docs/architecture/conversation.md](../../../../docs/architecture/conversation.md)

## Tests

- 各域 route 回归在 `services/api/tests/`，按 `test_<domain>*.py` 前缀组织，如 `test_chat_*.py`、`test_quiz_*.py`、`test_workspace_*.py`、`test_assessment*.py`、`test_classroom_*.py`、`test_guest_*.py`、`test_compat_api.py` 等。

## Key entry points

- `app/main.py`：`app.include_router(api_router)` 总挂载点。
- `v1/router.py`：`api_router` 装配（`/api/v1` 前缀 + `require_api_access` 依赖）。
- `v1/chat.py`：`event_stream` SSE 转发（`done` 帧 stamp `session_id` 后发 `history_saved`）。
