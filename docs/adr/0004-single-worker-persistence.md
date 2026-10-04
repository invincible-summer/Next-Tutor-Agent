# ADR-0004: JSON/JSONL 文件持久层采用 single-worker 不变量

- 状态：superseded by [ADR-0010](./0010-enterprise-persistence.md)（仅多 worker 禁令被取代；文件模式内本 ADR 结论继续有效）
- 日期：2026-10

## Context

当前持久层是 JSON/JSONL 文件（每用户/每会话/每 job 一份），经 `core/atomic.py` 原子写。文件持久层没有跨进程锁；若部署多个后端 worker，同一用户的并发写会互相覆盖，job 调度与内存态（游客运行时、课堂 worker、评价 worker）也会失去一致性。

## Decision

生产部署固定**单 uvicorn worker**（`deploy/edu-backend.service` 与启动脚本均按此配置）。文件持久层的一切一致性设计（原子写、job 认领、内存缓存）都以 single-worker 为前提不变量。

## Consequences

- 纵向扩展靠进程内并发（asyncio + 线程池），不需要分布式锁，运维简单。
- 多 worker/多副本部署在当前持久层下**不受支持**；若未来需要水平扩展，必须先引入真正的数据库与跨进程协调，届时以 superseding ADR 记录。
- 部署模板与运维文档必须持续传达这一约束，防止误配 `--workers N`。
