# schemas — Pydantic 请求/响应模型（单一来源）

跨域共享的 Pydantic 请求/响应模型集中地：每个域一份契约文件，是「请求/响应模型单一来源」——路由不另建重复模型，前端类型可由 schema 再生。

## Owns

- `chat.py` — 对话 API 请求/响应模型（`ChatRequest`、`ClassroomRef` 课堂插问引用、`RenameRequest`、`SessionItem` 等）
- `assistant.py` — 站内助手契约唯一源（`extra="forbid"`；`scripts/contracts/generate_types.py` 据此生成 `packages/contracts/src/generated/assistant.ts`，`--check` 进回归强制同步）
- `classroom.py` — 课堂三层契约：内容契约（Brief/Spec，`extra="forbid"`、枚举闭合、长度上限在 schema 层强制）、服务端实体（只由 store/worker 写）、Public DTO 白名单投影；ID 规则 `les_/job_/run_` 等 + 24 位十六进制

## Does not own

- 路由逻辑与业务语义（`api/v1/` 各域路由文件）
- 领域内部数据类（各 agents 包的 state/schema 模块，如 `student_model/evaluation/schema.py`、`ux_intelligence/schema.py`）
- 存储 JSON 结构（持久化 blob 的形状归各存储模块）

## Design

各域契约在架构文档中的位置：运行时全景见
[docs/architecture/backend-runtime.md](../../../../docs/architecture/backend-runtime.md)；
对话请求/会话契约见 [docs/architecture/conversation.md](../../../../docs/architecture/conversation.md)；
助手契约与 typegen 链路见 [docs/architecture/site-assistant.md](../../../../docs/architecture/site-assistant.md)。

## Tests

- `services/api/tests/test_assistant_schema.py` — 助手契约与前端 typegen `--check` 同步
- `test_classroom_schema.py` — 课堂三层模型与 ID/上限强制
- chat 请求模型经对话内核回归覆盖（如 `test_supervisor_v2_compat.py`、`test_multimodal_uploads.py`）

## Key entry points

- `chat.py::ChatRequest` — `/chat/stream` 等端点的请求模型
- `assistant.py` — 助手全 API 面 + SSE 事件载荷的唯一契约源
- `classroom.py::_StrictModel` — 课堂全部模型的基础配置（未知字段拒绝）
