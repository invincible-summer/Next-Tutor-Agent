# services/api — FastAPI 后端服务

教学 Agent 后端：对外提供全部 REST/SSE/WebSocket API，对内承载教学智能层（M1–M10）、教材处理管线与学习数据的持久化。

## Owns

- `app/`：FastAPI 应用与教学智能层的 Python 包（导航见 [app/README.md](./app/README.md)）。
- `tests/`：后端 unittest 套件，含存储沙箱基类与 prompt_eval。
- 依赖清单：`requirements.txt`（主依赖）+ `constraints.txt`（版本约束），另有 `requirements-test.txt`、`requirements-vector.txt`（可选向量轨）。
- `serve.py`、`cli.py`、`scripts/`：本地服务入口与辅助脚本。

## Does not own

- `apps/web/`：Next.js 前端，唯一消费方，经 `NEXT_PUBLIC_BACKEND_URL` 直连。
- `services/voice/`：MeloTTS 语音 sidecar，独立 venv 进程，后端经 HTTP 调用。
- 教材及其派生数据资产：仓库只分发源码与 synthetic fixtures（ADR-0001）；运行时数据统一落 `NEXT_TUTOR_DATA_DIR` 单根，不入库。

## Design

- 运行时形态（应用工厂/lifespan、CORS、限流、trace）、统一存储布局、API 面分组概览与智能层开关体系：[docs/architecture/backend-runtime.md](../../docs/architecture/backend-runtime.md)。
- 系统总览：产品定位、M0 + M1–M10 模块地图与领域模块索引：[docs/architecture/README.md](../../docs/architecture/README.md)。

## Tests

- 测试入口：`cd services/api && python -m tests`（全量）或 `cd services/api && python -m tests tests.test_<module>`（聚焦单个模块）。
- 测试目录 `tests/`（README 见 tests/README.md）；所有测试经存储沙箱重定向运行时数据根，绝不写生产存储。

## Key entry points

- `app/main.py`：应用工厂、lifespan（bootstrap、图谱 reaper、定时清理）与 `/api/v1` 总路由挂载。
- `app/api/v1/router.py`：v1 路由装配点（见 [app/api/README.md](./app/api/README.md)）。
- `app/agents/chat_agent.py` / `app/agents/supervisor.py`：一轮对话的编排入口（见 [app/agents/README.md](./app/agents/README.md)）。
