# ADR-0002: 所有运行数据统一进入 NEXT_TUTOR_DATA_DIR

- 状态：accepted
- 日期：2026-10（机制自 P11 运行可靠性整改收敛而来）

## Context

历史上运行数据散落在源码树多处（`backend/chat_history/`、`backend/students/`、`chroma_db/` 等），造成三类问题：测试可能污染真实数据、源码目录与数据目录互相污染导致 Git 卫生风险、账号注销与孤儿数据清理需要在多个根之间逐一排查。

## Decision

所有运行时数据（users、students、sessions、transcripts、traces、uploads、library、workspaces、classroom、assistant、trash、notes、knowledge、vector_db、illustrations、diagram_assets、auth secret、策略文件）统一收敛到单一数据根：

- 环境变量 `NEXT_TUTOR_DATA_DIR`，默认 `<repo>/.runtime/data`；
- 解析与重绑定机制在 `services/api/app/core/paths.py`：`bind_storage_path()` 在模块 import 时注册绑定，`set_runtime_root()` 一次调用即可重定向全部存储根（测试沙箱依赖此机制）；
- 新增 per-user 存储根时必须同时通过 `core/paths.py::bind_storage_path` 绑定并在 `core/orphan_cleanup.py` 的 scan categories 注册（双注册规则）。

## Consequences

- 源码与数据平面彻底分离：`.runtime/` 整体被 Git 忽略，仓库保持 source-only（与 ADR-0001 互补）。
- 测试通过 `tests` 的进程级沙箱把全部存储根重定向到临时目录，杜绝 synthetic ID 泄漏进真实数据根。
- 账号注销（`core/account_data.purge_account`）与孤儿数据清理（admin「数据清理」）按单根下的固定类别扫描，语义完整且不留空目录。
- 代价：所有持久化模块必须走统一绑定而不是自定路径；绕过绑定的直写路径属于缺陷。
