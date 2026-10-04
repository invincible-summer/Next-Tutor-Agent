# scripts/contracts

跨端契约生成链（见 docs/architecture/client-platform.md）。事实源是
`services/api/app/schemas/` 的 Pydantic 模型与 FastAPI 应用本身。

- `generate_types.py`：把 `PUBLIC_TYPE_MODELS`/`PUBLIC_TYPE_UNIONS` 确定性渲染为
  `packages/contracts/src/generated/*.ts`（唯一类型生成器；classroom/assistant
  旧脚本已合并删除）。`--check` 供 CI 防漂移。
- `export_openapi.py`：离线导出排序后的 OpenAPI 快照到
  `packages/contracts/openapi/next-tutor.openapi.json`，`--check` 防漂移。
- `check_contracts.py`：契约 lint。public endpoint 必须声明 response schema
  （response_model 或 204）；存量缺口登记在 `contract_gaps.json`，棘轮只允许
  收缩。`--update-baseline` 重新生成基线。

根命令：`pnpm contracts:generate` / `pnpm contracts:check`。
