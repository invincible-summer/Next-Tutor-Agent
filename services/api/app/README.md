# app — FastAPI 应用与教学智能层包

FastAPI 应用与教学智能层的 Python 包：应用装配、API 路由、M1–M10 智能层、领域模块与共享 primitive 的宿主。

## Owns

- `main.py`：应用工厂 + lifespan + 总路由挂载。
- 各子包（每行职责，详情见各自 README）：
  - `api/`：HTTP API 层（REST/SSE/WS 端点、鉴权边界、请求校验）→ [api/README.md](./api/README.md)
  - `agents/`：对话回合编排与 M1–M10 智能层子包 → [agents/README.md](./agents/README.md)
  - `classroom/`：课堂模式（课件生成、试听、运行、作文）→ [classroom/README.md](./classroom/README.md)
  - `core/`：跨领域 primitive（config/paths、atomic/json、LLM runtime、context、工具协议）→ [core/README.md](./core/README.md)
  - `diagrams/`：教学 SVG 图示库（目录、适配器、编译）→ [diagrams/README.md](./diagrams/README.md)
  - `identity/`：M0 身份基础设施（账户、JWT、`resolve_student_id()`）→ [identity/README.md](./identity/README.md)
  - `illustration/`：题图装配、布局与渲染管线 → [illustration/README.md](./illustration/README.md)
  - `prompts/`：Prompt 定义与版本化注册表（`registry.py`）→ [prompts/README.md](./prompts/README.md)
  - `schemas/`：API 请求/响应 Pydantic 模型 → [schemas/README.md](./schemas/README.md)
  - `tools/`：Agent 工具实现（knowledge_search、quiz、recall_history 等）→ [tools/README.md](./tools/README.md)
  - `voice/`：电话式语音对话（WS）与 TTS 后端侧接入 → [voice/README.md](./voice/README.md)

## Does not own

- `services/voice/`：MeloTTS sidecar 是独立进程；`app/voice/` 只持有后端侧接入逻辑。
- `apps/web/`：前端不属本包。
- `services/api/tests/`：测试在包外，经 `cd services/api && python -m tests` 运行。

## Design

- 模块索引与依赖方向（api → 领域模块 → core；`identity/` 是所有请求的身份入口；领域模块不得反向依赖 `api/`）：[docs/architecture/README.md](../../docs/architecture/README.md)

## Tests

- 统一入口见上级 [README.md](../README.md) 的 Tests 行；各域测试前缀在对应子包 README 的 Tests 行。

## Key entry points

- `main.py`：应用工厂（CORS、`X-Process-Time`、lifespan、`include_router(api_router)`）。
