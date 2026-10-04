# workflows — Temporal durable workflow 层（ADR-0013）

后台长任务的 durable 执行层：教材解析/OCR/图谱构建、课堂生成、配图（quiz + scenario）、学习评价、定时维护任务。与 HTTP app 共用同一 `app.*` 领域包，只改变「任务在哪个进程执行」；领域检查点/预算/事实源不变（ADR-0013）。

完整设计（队列划分、workflow id 映射、双模式门控、worker 运行）见 [docs/architecture/backend-runtime.md](../../../../docs/architecture/backend-runtime.md) 与 [ADR-0013](../../../../docs/adr/0013-durable-workflows.md)，本 README 只做导航。

## Owns

- 门控与配置：`config.py`（`TEMPORAL_ADDRESS` / `TEMPORAL_NAMESPACE`，未配置即整层惰性——file 模式零行为变化）。
- 共享 runtime：`runtime.py`（Task queue 常量、惰性缓存 Client、workflow id 构造器 `join_workflow_id`）。
- 各域 workflow/activity 装配：按迁移批次落在 `textbook.py`、`classroom.py`、`illustration_*.py`、`evaluation.py`、`maintenance.py`（见 backend-runtime.md 的迁移状态表）。

## Does not own

- 领域事实源与检查点（job.json / journal / build_job / immutable artifact）→ 各领域包（`app/classroom`、`app/illustration`、`app/agents/…`）。
- worker 进程入口 → `services/api/worker.py`（命令行参数、队列选择、优雅停机）。
- API 路由与契约 → `app/api/v1/`（job 轮询 DTO 不因执行引擎改变）。
- 部署编排 → `deploy/local`（dev Temporal）、`deploy/self-hosted`（worker systemd unit）。

## Tests

`services/api/tests/workflows/`（workflow 确定性测试用 temporalio 内置 time-skipping test server；真实服务器集成测试 `integration.py` 由 `TEST_TEMPORAL_ADDRESS` 门控）。

## Key entry points

- `config.py::temporal_configured` — 双模式门控（API lifespan / 各域接缝统一判定）。
- `runtime.py::get_client` — 进程级缓存 Temporal client。
- `services/api/worker.py` — worker 进程入口（`--queues documents,classroom,…` 子集运行）。
