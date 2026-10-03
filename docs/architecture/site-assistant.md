# site-assistant — 站内学习助手（悬浮面板）

全站右下角悬浮「学习助手」：功能导览、受控导航与实体深链、有依据的学习/教学报告、领域写动作（预览/审批/执行/撤销）、跨模块工作流、订阅提醒与语音朗读；与教学链路完全隔离——不写学习证据、不触发 `evaluate_turn`、不经 `/chat/stream`。

## Purpose / Scope（职责与边界）

- 导览与导航：`product_catalog.json` 驱动的功能导览；自然语言「带我去记忆中心 / 打开我的高数笔记第 23 页」解析为受控 `NavigationTarget` 深链，附带页面级到达回执。
- 报告：学习活动快照与学习报告（五段固定结构）、教学报告（样本不足只列现象），全部由确定性数据投影生成，模型只负责组织文字。
- 领域写动作：按操作目录白名单对笔记/编排/测评/工作区/资料库/教材/课程等执行「预览 →（审批）→ 执行 → 确认」，带幂等与 10 分钟撤销窗口。
- 跨模块工作流：最多 8 步（写 ≤5）的持久状态机，串接既有领域服务与长作业。
- 订阅与收件箱：四类默认关闭的主动提醒（一分钟粒度调度、静默时段、每日上限）与简报收件箱。
- 语音朗读：面板内容的句级 TTS 合成（local 策略绝不送云）。
- 不负责：任何教学决策与证据写回（隔离铁律）；动作的实际业务语义由各领域模块拥有，助手只做白名单编排与补偿。

## Owned code（拥有的代码路径）

后端 `services/api/app/`：

| 路径 | 职责 |
|------|------|
| `agents/site_assistant/runtime.py` | 单 worker 进程内轮运行时（running 落盘 → spawn asyncio task、事件环、SSE 游标、订阅 tick） |
| `agents/site_assistant/intent.py` | 精确别名零 LLM 快路 + 一次低预算结构化意图（九类闭集、时区窗口夏令时安全、确定性页码提取） |
| `agents/site_assistant/tools.py` | 轮内工具集（八工具；≤4 工具/≤2 模型/60s 墙钟/并发 ≤3）、`search_site_entities` 实体检索 |
| `agents/site_assistant/search.py` | 自然语言查询净化、双向匹配与档位合并（30s 进程级缓存，按 student_id+query 键） |
| `agents/site_assistant/readers.py` | `resolve_destination` 目录/实体候选解析与最佳档位裁决 |
| `agents/site_assistant/presenters.py` | 确定性事实卡与报告结构化呈现 |
| `agents/site_assistant/policy.py` | `automatic` / `user_click` 判定与导航目标唯一性裁决 |
| `agents/site_assistant/actions.py` | 动作状态机（proposed→executing→awaiting_ack→succeeded）、invocation 锁、ack_token |
| `agents/site_assistant/previews.py` | 操作目录确定性 `ActionPreview`（字段差异/影响/可逆性/parameter_hash/source_revisions）与执行策略单一来源 `policy_for()` |
| `agents/site_assistant/undo.py` | 撤销补偿（前值快照、只回滚自己的变更） |
| `agents/site_assistant/workflows.py` | 持久工作流状态机与领域长作业桥接 |
| `agents/site_assistant/workflow_templates.py` | 六个固定编排模板 |
| `agents/site_assistant/notifications.py` | 订阅调度、delivery ledger、静默时段与每日上限 |
| `agents/site_assistant/reports.py` | `astr_` 报告生成与 90 天生命周期 |
| `agents/site_assistant/voice.py` | 面板语音朗读任务（句切分、TTS 分发） |
| `agents/site_assistant/catalog.py` / `guide.py` / `capabilities.py` | `product_catalog.json` 读取、功能导览、能力面（含各开关如实反映） |
| `agents/site_assistant/service.py` / `ratelimit.py` | 服务编排与助手级限流 |
| `agents/site_assistant/product_catalog.json` | 功能事实源（`catalog_version`） |
| `core/assistant_store.py` | 会话/草稿/工作流/订阅/报告持久化（原子写、容量上限、幂等） |
| `schemas/assistant.py` | Pydantic 契约唯一源（`extra="forbid"`） |
| `api/v1/assistant.py` | `/api/v1/assistant/*` 路由 |

前端 `apps/web/src/`：`components/assistant/`（`AssistantLauncher`、`AssistantPanel`、`AssistantComposer`、`AssistantMessages`、`AssistantTaskCenter`、`AssistantInbox`、`AssistantSettings`、`AssistantVoiceControls`、`AssistantHost`/`Provider` + `cards/` 报告/动作/选择/来源卡）、`lib/assistant/`（`store.ts`、`routes.ts` 导航白名单、`navigation.ts`/`page-context.ts` 页面适配器、`deep-link.ts`、`overlay.ts`、`actions.ts`、`api.ts`、`types.generated.ts`）。

类型生成：`scripts/dev/generate_assistant_types.py` 由 `schemas/assistant.py` 生成前端 `types.generated.ts`（`--check` 在 `test_assistant_schema.py` 中强制同步）。

## Public contracts（对外契约：API 端点/SSE/WS/数据结构）

前缀 `/api/v1/assistant`，全部需登录：

- 能力与导览：`GET /capabilities`（action_kinds / disabled_reasons 如实反映各开关）、`GET /search`、`POST /guide`。
- 会话与轮次：`POST /conversations`、`GET /conversations[/{id}]`、`DELETE /conversations/{id}`；`POST /conversations/{id}/turns`（202，`client_message_id`+body hash 幂等）、`GET /turns/{id}`、`POST /turns/{id}/cancel`、`GET /turns/{id}/events`（SSE，15s 心跳，`{turn_id}:{seq}` 游标，断线 1/2/4s 退避重连）。
- 偏好：`GET/PUT /preferences`（`prefs.assistant` 白名单 + `base_revision` 乐观并发）。
- 动作：`GET /actions/{aid}` 及 `preview` / `approve` / `execute` / `ack` / `undo`；approve 许可 10 分钟有效，参数或业务版本变化 → 409 `preview_stale`。
- 交接草稿：`GET /drafts/{id}`（读取不消耗）、`POST /drafts/{id}/consume`（同实体幂等/异实体 409）、`DELETE /drafts/{id}`。
- 工作流：`POST /workflows`、`GET /workflows[/{id}]`、`approve` / `start` / `cancel` / `steps/{sid}/retry` / `resume` / `events`（SSE）。
- 订阅与通知：`GET/POST/DELETE /subscriptions[/{id}]`、`GET /notifications`、`POST /notifications/{id}/read|dismiss`、`GET/DELETE /reports/{id}`。
- 语音：`GET /voice/capabilities`、`POST /voice/preview`、`POST /audio/jobs`（202）、`GET /audio/jobs/{id}`、`POST /audio/jobs/{id}/cancel`、`GET /audio/clips/{id}/content`（认证内容端点）。
- 数据结构：`schemas/assistant.py` 为唯一契约源；`AssistantSource.kind` 闭集含 `site_search`（站内检索来源可点击深链）。

## State & storage（状态与存储布局，含 runtime data 路径）

唯一根 `chat_history/assistant/<uid>/`（ADR-0002；已登记 storage sandbox、`orphan_cleanup`、`account_data.purge_account` 与 `.gitignore` 四处）：

| 路径 | 内容 |
|------|------|
| `conversations/`、`index`、`references`、`invalidations` | 助手会话与索引、跨模块引用与失效记录 |
| `drafts/` | 交接草稿（30 分钟 TTL，URL 只带随机 draft id；启动时与每小时各清一次过期草稿） |
| `workflows/<id>.json` | 工作流持久状态机（revision 乐观并发） |
| 订阅 / 通知 / delivery ledger | 订阅定义、通知与原子认领账本 |
| `astr_` 报告 | 90 天保留；删除只删助手副本，不删原业务证据 |

会话容量 200 条消息或 2 MiB 先到为准（受理新轮前预留 2 条消息空间）；全部写入经 `core/atomic.py`。

## Main flows（关键流程）

### 轮次编排（`runtime.py` → `intent` → `tools` → `presenters`）

1. 受理：`client_message_id` + body hash 幂等；先落盘 running 再 spawn asyncio task（单 worker 进程内模型）。
2. 意图：精确别名命中走零 LLM 快路；否则一次低预算结构化意图（九类闭集）。`extract_page` 从原文确定性提取页码（中文数字含「二十三/一百零三」，1..5000 与预览渲染上限一致），`ParsedIntent.page` 与解析路径无关。
3. 工具：八工具每轮 ≤4 工具 / ≤2 模型 / 60s 墙钟 / 并发 ≤3；`search_site_entities` 消费合并检索。
4. 呈现：确定性事实卡；学习报告五段固定结构、教学报告样本 <5 只列现象；模型不可用或超预算回落确定性文字回答，模型故障不拒轮。
5. 事件：512 条内存环、终态保留 10 分钟；取消时同时取消该轮未 execute 的 proposed 动作；进程重启把在途轮标记 interrupted，不重放。

### 实体检索与深链

- 查询净化（`search.py`）：书名号《…》内文优先，否则剥离导航/查找动词、填充词、`第N页` 与疑问尾词；实体类词提供激进/保守两档力度，`expand_query` 双档同时检索并按档位合并去重（exact 优先）；`_norm` 两侧对称剥离中英标点；文件以去扩展名文件名作别名（「微积分讲义」精确命中「微积分讲义.pdf」）。
- 目标解析（`readers.py`）：目录未强命中时以合并检索解析具名实体（仅 exact/title 档）；最佳档位唯一者顶替目录弱命中，工作区/页面实体在场时不顶替（如实给 choices）；模块强命中（原始得分 ≥80 或净化词与模块名/别名精确相等）时跳过实体候选，防模糊标题劫持模块导航；file 候选消费 `page` 参数写入目标。
- 裁决（`policy.py`）：`resolve_destination` 唯一候选优先于意图阶段模块；choices 实体选项用 `entity_id` 保证 `option_id` 唯一。
- 导航白名单（`lib/assistant/routes.ts`）：`NavigationTarget` → URL 唯一白名单拼装，深链覆盖 `/memory?ws&tab&concept&source`、`/knowledge?concept&ws`、`/orchestration?task=`、`/insights?proposal=`、`/notes/{id}`、lesson/classroom_run/chat_session 等模块实体。

### 导航回执与资料页预览

- 成功条件 = 精确 URL 到达 且 当前页面适配器 `navigationStatus(target)` 确认实际内容已应用（查询参数/工作区/实体/分页/锚点不能仅由地址栏推定）；返回 null 表示等待加载，缺失/不可用/失败分别返回既有错误码；新命令使旧等待失效，到达后用户离开取消回执；客户端最长等待 12 秒，超时允许手动重试，不在服务端 15 秒 ack 窗口后继续声称成功。
- 资料文件页码复用 `/library/files/{id}/page/{page}` 的鉴权 PDF 原页渲染（不新增存储或 API）；预览经 `apiFetch` 下载 Blob、图片加载完成后发布就绪标记；非 PDF、原件缺失、越界或无权限不产生成功定位回执；execute/ack 回包同步会话版本，保证连续导航可继续对话。

### 领域写动作（B03–B11 操作目录）

- 链路：`previews.py` 按操作目录确定性构造 `ActionPreview` → `review_required` 操作须 10 分钟内 approve 才可 execute（参数或业务版本变化即失效）→ 执行 → awaiting_ack → succeeded；`automatic` 判定由 `policy.py` 给出，前端 turn_done 后仅活跃且可见的标签页可自动执行（epoch/可见性/面板收起时降级 `user_click`）。
- 并发与幂等：invocation 锁同 invocation 幂等、异 invocation 409；`ack_token` 绑定用户+动作+标签页，15s 未 ack 惰性推进为 needs_attention；创建类带 `client_request_id` 幂等（去重标记与创建共用一次原子写）；课堂恢复用 `action_id` 派生 Idempotency-Key 复用 `runs.create_run`；目标已变化返回 409 `target_changed`，不悄悄 restart。
- 操作目录：workspace.create/update_sources；chat.rename/move_workspace/archive；library.create_folder/rename_file/move_file；textbook.cancel/rebuild（经事件循环桥接提交构建队列）；archive.restore；note.append/replace/move/set_review/restore_revision；goal.create/update、task.create/update/complete、subtask.create、schedule.update；assessment.start/practice；evaluation.request_review/retry/synthesize；teaching.approve/apply/revoke（apply 校验 guidance 实际生效，部分失败标 needs_attention）；profile.update 白名单、memory.set_window、assistant.preferences；lesson.generate（含 start_mode）/retry/cancel/export 复用课堂 job 服务。公共教材写需 admin。
- 撤销（`undo.py`）：执行时记录前值快照，撤销窗口 10 分钟；补偿只回滚自己的变更——创建类要求目标未被编辑且无依赖（否则 409 指引原模块），重命名/移动校验仍处本动作结果后反向写，update_sources 以执行后 `updated_at` 版本门 + 增量反演，归档撤销调用真实 trash restore；`POST /actions/{aid}/undo` 按 `client_request_id` 幂等；撤销后 state 保持 succeeded 并记录 undo_result，UI 显示「已撤销」。
- 约束：AI 生成内容（如 plan.regenerate 两阶段「候选→验证→提交」）一律 review_required 预览；task.complete 仅响应明确自报（`completion_source=self_report`）；课堂动作的 composition 由模型在 schema 枚举内自选，助手参数不含构图字段。

### 工作流（C01–C03）

- 状态机（`workflows.py`）：draft→awaiting_approval→queued→running→waiting_domain_job/paused_for_user→succeeded|partially_succeeded|failed|cancelled|interrupted；revision 乐观并发；步骤 ≤8（写 ≤5），读 ≤2 并发、写串行，依赖未成功不执行后续写；幂等键 workflow_id+step_id+approved_plan_hash（步骤动作 `astw_step:` 前缀复用动作链，重试不重复创建实体）；领域长作业 30 秒等待后持久化、20 分钟无终态转 paused_for_user，恢复按 DomainJobRef 回查原 job；必要步骤零完成判 failed、部分完成判 partially_succeeded；业务准备失败落盘 failed，不留悬挂 executing。
- 六模板（`workflow_templates.py`）：setup_learning_space / weekly_review_to_plan / weak_point_to_practice / material_to_course / organize_materials / continue_learning_session；输出绑定由模板代码完成；handoff 落点对齐 `NavigationTarget` 联合，不自动开课或发送。
- 办理事项（`AssistantTaskCenter`）：按「进行中 / 等待我处理 / 已完成」分组，进度只用「n/m 步骤」；计划预览（将创建/修改对象）→ 批准 → 启动 → 取消/失败步骤重试/恢复；对话侧 `start_workflow` 动作（`intent.WORKFLOW_TEMPLATE_PATTERNS` 单一事实源零 LLM 快路）execute 只建 draft，幂等复用 workflow_id。

### 订阅调度与收件箱（C04–C05）

- 四类订阅默认关闭、须用户逐项开启；AssistantRuntime 一分钟粒度 tick（每批 ≤20）；执行 key = subscription_id + 本地计划日期 + schedule_revision，delivery ledger 原子认领防重复投递；停机只补 48 小时内最新一次；quiet_hours 内只准备（pending_delivery 出窗释放）；每日主动上限 3；weekly_brief 用确定性事实模板（不调用模型）；`manage_subscription` 对话动作幂等（client_request_id=action_id）。
- 收件箱（`AssistantInbox`）：未读点、已读/忽略、mute 未完成课程提醒、简报固定统计窗口；设置页 `AssistantSettings` 提供偏好白名单（base_revision 乐观并发）与订阅管理、最近投递入口。

### 面板语音（B11）

`voice.py` 对面板回答做句切分（100–250 字符、单条 ≤40 片、24h TTL、10 次/分钟、内容 hash 去重、local 策略绝不送云）；`POST /audio/jobs` → 轮询 → clip 认证内容端点播放；`AssistantVoiceControls` 接入既有 audio-focus。

## Dependencies（依赖与被依赖）

- 依赖：身份层（须登录）；LLM 单通道（低预算结构化意图与文字组织）；数据投影 `activity_aggregator.learning_activity_snapshot`（metric_version=2）、`evaluation.window.teaching_window_report`、`learning_orchestration.saved_tasks_snapshot`（只读不物化）；M9 计划服务（plan.regenerate 两阶段）；笔记中心、trash/归档、资料库与教材构建队列、课堂 job 服务（lesson.* 动作）；统一 TTS service（见 [voice.md](./voice.md)）；`core/atomic.py`。
- 被依赖：无——助手是纯旁路消费者与白名单编排者，各领域模块不感知助手。
- 与课堂/笔记/计划的联动全部经上述公开契约（动作链、handoff 草稿、NavigationTarget 深链），见 [classroom.md](./classroom.md) 与 [conversation.md](./conversation.md)。

## Invariants / security boundaries（不变量与安全边界）

- **教学链路隔离铁律**：不写学习证据、不触发 `evaluate_turn`、不经 `/chat/stream`；播放/导航等助手行为不产生学习事件。
- 契约单一事实源：`schemas/assistant.py`（`extra="forbid"`）唯一契约源，前端类型必须再生成（`--check` 进回归）；`product_catalog.json` 是功能事实源。
- 数据投影 empty ≠ error：来源模块关闭时返回 disabled，不伪造空数据；学习历史覆盖不足时如实呈现 `history_incomplete`/`known_minimum`（DailyTask 经 `task_instance_id`/`workspace_id` 与 `task_status_changed` outbox 原子写入、按 event_id 去重）。
- 动作安全：全部经操作目录白名单与确定性预览；AI 生成内容 review_required；撤销只回滚自己的变更；`ack_token` 防跨标签页误确认；公共教材写需 admin。
- 语音隐私：local 策略的合成请求绝不送云。
- 交接草稿只预填：备课/聊天/课堂插问的 handoff 均不自动提交，consume 后才落位。
- 已知边界：「继续刚才的」类指代消解依赖模型可用（不可用时如实降级）；`get_concept_explanation` 不派发（无工作区账号会产生 notice 污染）。

## Configuration（环境变量与开关）

| 变量 | 默认 | 说明 |
|------|------|------|
| `SITE_ASSISTANT_ENABLED` | `0` | 总开关；关闭时 lifespan 不启动 runtime 与草稿清理循环，端点返回 disabled |
| `SITE_ASSISTANT_ACTIONS_ENABLED` | `1` | 领域写动作（提案/预览/执行）单独门控；`capabilities.action_kinds` 同步反映 |
| `SITE_ASSISTANT_WORKFLOWS_ENABLED` | `1` | 工作流创建/执行单独门控 |
| `SITE_ASSISTANT_PROACTIVE_ENABLED` | `0` | 主动订阅调度；默认关且四类订阅仍须用户逐项开启 |

语音能力无独立 env 开关，随 TTS provider 配置（见 [voice.md](./voice.md)）如实反映在 `/voice/capabilities`。

## Observability（trace/日志/指标）

- `GET /capabilities` 的 `action_kinds` / `disabled_reasons` 是各开关与服务可用性的如实投影。
- 轮事件环 + SSE 游标使前端可断线重连补齐；在途轮崩溃后标记 interrupted 可见。
- delivery ledger 与通知已读/忽略状态可在收件箱与设置页审计；`astr_` 报告 90 天自动过期。

## Tests / acceptance（测试索引）

后端 `services/api/tests/`：`test_assistant_actions.py`、`test_assistant_b05_actions.py`、`test_assistant_b06_actions.py`、`test_assistant_b07_actions.py`、`test_assistant_b09_actions.py`、`test_assistant_b10_actions.py`、`test_assistant_b11_voice.py`、`test_assistant_catalog.py`、`test_assistant_data_projection.py`、`test_assistant_handoff.py`、`test_assistant_notifications.py`、`test_assistant_orchestration.py`、`test_assistant_previews.py`、`test_assistant_runtime.py`、`test_assistant_schema.py`（含 typegen `--check`）、`test_assistant_search.py`、`test_assistant_store.py`、`test_assistant_undo.py`、`test_assistant_workflows.py`（共 19 件；站内检索的进程级缓存由 `tests/storage_sandbox.reset_shared_caches` 跨用例清理）。

浏览器：`apps/web/e2e/assistant-panel.spec.ts`（自动导航 ack 闭环 / 学习报告 / 教学报告 / 实体检索深链）、`apps/web/e2e/assistant-navigation.spec.ts`、`apps/web/scripts/test-assistant-navigation.mjs`（页面适配器与回执）。

## Related ADRs

- ADR-0002 运行数据统一 `NEXT_TUTOR_DATA_DIR`（`chat_history/assistant/` 单一根，四处登记）
- ADR-0004 single-worker（轮运行时与订阅 tick 为进程内 asyncio 任务，重启 interrupted 不重放）
