# @next-tutor/contracts

跨网络边界的数据形状与协议事件契约：OpenAPI 快照、生成的 TypeScript DTO、
SSE/WS 协议 discriminated union 与稳定 ID 类型。

- 事实源是 `services/api/app/schemas/` 的 Pydantic 模型；生成文件在
  `src/generated/`，禁止手改。
- 生成链与漂移检查：`scripts/contracts/generate_types.py`（根
  `pnpm contracts:generate` / `pnpm contracts:check`）。
- 本包禁止 React、fetch、localStorage 与任何平台 API（无 DOM lib）。

架构文档：`docs/architecture/client-platform.md`。
