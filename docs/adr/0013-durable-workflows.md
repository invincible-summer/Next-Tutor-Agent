# ADR-0013: Durable workflows（Temporal）与后台任务所有权分离

- 状态：accepted（分域分批落地，见实施状态表）
- 日期：2026-10
- 取代：ADR-0004 的「后台任务由 API 进程 lifespan/create_task 持有」部分（single-worker 文件模式部分仍有效）

## Context

ADR-0004 以来，所有后台长任务（教材解析/OCR/图谱构建、课堂生成、配图、学习评价、定时维护）都由 API 进程内的 `asyncio.create_task`/worker 循环持有。ADR-0010 解除了多实例的持久化障碍（`WEB_CONCURRENCY>1` 企业模式放行），但任务所有权仍在进程内：实例崩溃即任务丢失（靠各域自研的启动扫描/lease/`run_interrupted` 标记补救），多实例部署会出现同一队列被多个进程消费的竞态。各域自研恢复机制形态各异（classroom startup-scan、evaluation lease 认领、textbook intent 幂等重放、illustration 惰性标失败），维护成本高且无法横向扩展执行容量。

## Decision

引入 Temporal（Python SDK `temporalio`，与 HTTP app 同一代码库），把 job 化长任务的**执行所有权**从 API 进程迁到独立 worker 进程：

- **双模式门控**（与 ADR-0010/0012 的降级范式一致）：`TEMPORAL_ADDRESS` 未设 → 各域保持现有进程内执行，行为零变化（file/self-hosted 部署不需要 Temporal）；已设 → API lifespan 不再启动该域 in-process worker，job 由 `services/api/worker.py` 进程经 Temporal 执行。**同一域绝不允许双 worker 同时消费**——门控是进程级互斥的，不是叠加的。
- **不拆业务代码**（wrap-as-activity）：现有域 runner（检查点、预算、emit、协作取消）整体作为一个 activity 执行；**域持久化仍是唯一事实源**（job.json / journal / build_job / immutable artifact），Temporal 只做 durable orchestration。域检查点仍是崩溃恢复粒度——Temporal 保证「activity 因机器崩溃后继续」，不接管业务状态。
- **调度策略优先复用域调度器**：各域现有队列/选择/并发策略（textbook per-owner FIFO + 停滞看门狗、classroom 并发/轮转、evaluation claim/outbox）是经过测试的行为契约——迁移后它们随 activity 原样在 worker 进程内运行（进程角色标记 `app/workflows/config.is_worker_process` 使 activity 内的 enqueue/claim 走进程内路径、不递归派发 workflow），而不是在 workflow 里重写一份调度器。代价：每条队列保持单 worker 实例消费（与文件模式同一约束），按队列扩容而非按副本扩容；未来需要副本级扩容时再为对应域引入调度 workflow。
- **五个 task queue**：`documents` / `classroom` / `evaluation` / `media` / `maintenance`，同一 worker 入口按 `--queues` 子集运行，可独立扩容。
- **workflow id 与域 job id 稳定映射**：`classroom-job:{job_id}`、`quiz-illustration:{job_id}`、`scenario-illustration:{job_id}`、`textbook-queue:{owner}` 等；cancel/retry = 域 CAS 打标（事实源）+ workflow cancel / 同 id 新 run。id 只在 API/activity 侧铸造，workflow 代码保持确定性纪律（无直接 IO/时钟/随机）。
- **配图双 workflow**：`QuizIllustrationWorkflow` 与 `ScenarioIllustrationWorkflow` 是两个独立业务 workflow（各自的审核输入与发布条件不共享），共享装配在 `app/workflows/illustration_common.py`，run activity 整体包住域 `orchestrator._run` / `scenario._run`（wrap-as-activity，不拆成细粒度 activity）。
- **配图崩溃结算（as-built）**：run activity 不重试（`maximum_attempts=1`）——生成按次消耗模型预算，崩溃自动重跑等于隐性双倍扣费；非取消失败后由同一 workflow 的 settle activity 兜底把磁盘记录结算为 `failed/run_interrupted`（retryable，用户显式重试，与文件模式「任务消失 → 读路径标中断」同语义）。API 读路径在 durable 模式不再做 liveness RPC；worker 启动对账一次性结算 cutover 残留（磁盘 queued/running 且无存活 workflow）。
- **owner epoch 磁盘化（as-built）**：`illustrations/.epochs/<owner>.json` 单调标记跨进程共享（内存 dict 在 API+worker 双进程下失效）；purge 只 bump 不清标记（tombstone，防迟到写复活已删目录），标记损坏时 fail-closed。`persistence.write` 落盘后复验 epoch 并补偿删除，关掉跨进程删除竞态窗口。
- **不迁移清单**（保持进程内，属请求生命周期或可重建缓存）：站内助手 turns/站内多步 workflow/音频 job、语音 WS、课堂 TTS lane 与 voices 预热、向量索引单槽、file summary/workspace memory 等 fire-and-forget、guest sweep。

## Consequences

- 每迁一个域即删掉该域在 API lifespan 的任务启动（temporal 模式下）；cutover 残留（无 workflow 的 running 记录）由一次性对账处理，语义沿用现有 startup-scan。
- 对外契约零变化：job 轮询 DTO、SSE、`run_interrupted` 兼容 code 全部保留；中断判定从「进程里是否有 task」改为「workflow 是否存活」。
- 会话/账号删除：发 workflow cancel + 现有 epoch/generation 校验保留，迟到 activity 结果在 commit 前再验。
- `WEB_CONCURRENCY>1`（企业模式）在任务域迁完后才真正做到多实例安全；file 模式 fail-fast 不变（ADR-0010）。
- 测试形态：workflow 确定性测试用 temporalio 内置 time-skipping test server（无外部依赖）；真实服务器集成测试 `tests/workflows/integration.py` 由 `TEST_TEMPORAL_ADDRESS` 门控，CI temporal lane 提供 service container。
- OTel 对 worker 进程的深度 instrumentation（interceptor）延后到观测加固批次；`domain_span` 在未初始化时本就是 no-op。

## 实施状态（每迁一域更新）

| 域 | 队列 | 状态 |
|---|---|---|
| 基建（config/runtime/worker.py/CI/依赖） | — | done |
| textbook（构建 intent + 手动刷新；恢复移 worker 启动） | documents | done |
| classroom（生成 job；监督 workflow + adopt 收养） | classroom | done |
| illustration（quiz + scenario；epoch 磁盘化 + settle 兜底） | media | done |
| learner evaluation（worker + daily planner 监督，as-built 为 supervisor 模式——每日关窗与 worker 同进程，notify 唤醒链保持） | evaluation | done |
| maintenance（briefing/trash/draft Schedules + 账号 purge workflow） | maintenance | done |
