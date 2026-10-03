# site_assistant — 站内学习助手（悬浮面板）

全站右下角悬浮「学习助手」的智能侧：意图识别、受控导航深链、学习/教学报告卡、领域写动作与跨模块工作流；与教学链路完全隔离。

## Owns

- `intent.py` — 意图识别：精确别名零 LLM 快路 + 一次低预算结构化意图（九类闭集、确定性页码提取）
- `runtime.py` — 单 worker 进程内轮运行时（事件环、SSE 游标、订阅 tick）
- `tools.py` / `search.py` / `readers.py` — 轮内八工具集、站内实体检索净化与档位合并、目标候选解析
- `policy.py` / `presenters.py` — 导航目标裁决、确定性事实卡与报告呈现
- `actions.py` / `previews.py` / `undo.py` — 领域写动作状态机、操作目录确定性预览、撤销补偿
- `workflows.py` / `workflow_templates.py` — 持久工作流状态机与六个固定编排模板
- `notifications.py` / `reports.py` — 订阅调度、delivery ledger 与 `astr_` 报告生命周期
- `voice.py` — 面板内容句级 TTS 朗读任务（local 策略绝不送云）
- `catalog.py` / `guide.py` / `capabilities.py` + `product_catalog.json` — 功能事实源、导览与能力面（各开关如实反映）
- `service.py` / `ratelimit.py` — 服务编排与助手级限流
- 助手会话与任务状态的持久化访问（会话/草稿/工作流/订阅/报告的原子读写、容量上限与幂等）

## Does not own

- 教学链路一切：不写学习证据、不触发 `evaluate_turn`、不经 `/chat/stream`（隔离铁律）
- 动作的实际业务语义：笔记/编排/测评/工作区/资料库/教材/课程操作由各领域模块拥有，助手只做白名单编排与补偿
- Pydantic 契约（`app/schemas/assistant.py` 唯一源）与路由（`app/api/v1/assistant.py`）
- TTS 合成本身（复用统一 voice 服务）；数据投影（activity/evaluation/orchestration 只读消费）

## Design

轮次编排、操作目录与撤销纪律、工作流状态机、订阅调度与存储布局的完整设计见
[docs/architecture/site-assistant.md](../../../../../docs/architecture/site-assistant.md)。

## Tests

- `services/api/tests/test_assistant_*.py`（19 件：actions、b05–b11 系列、catalog、data_projection、handoff、notifications、orchestration、previews、runtime、schema 含 typegen `--check`、search、store、undo、workflows）
- 浏览器回归：`apps/web/e2e/assistant-panel.spec.ts`、`apps/web/e2e/assistant-navigation.spec.ts`

## Key entry points

- `runtime.py` — `AssistantRuntime`：轮运行时、订阅 tick 与服务编排入口
- `intent.py` — 每轮意图识别第一站；`service.py` — 领域服务编排
- `actions.py` / `workflows.py` — 写动作与工作流状态机；`api/v1/assistant.py` — 路由面
