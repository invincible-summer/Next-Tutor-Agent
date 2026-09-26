# K04 灰度发布记录（plan.md §20.4）

日期：2026-09-26

## 已完成

- **第 1 档机制就绪**：`CLASSROOM_ENABLED`（默认 0）+ `CLASSROOM_ALLOWED_USERS`
  （空 = 不限制）双闸；`capabilities.user_allowed` 依次校验总闸 →
  allowlist → 游客策略（`CLASSROOM_ALLOW_GUEST` 默认 0）。非白名单账号
  `capability.enabled=false`，生成端点返回 classroom_disabled envelope。
  回归：`tests/test_classroom_api.py::test_rollout_allowlist_gates_capabilities`。
- **回滚语义**（实现于 A/J 阶段）：总闸关闭时禁止新 job/run/合成，已
  存在内容只读、可导出、进度暂停保存退出；应用回滚不删课堂目录、不
  改写 schema=1 数据。
- 紧急关闭/恢复路径与 §20.2 部署步骤、§20.3 告警阈值已写入
  `docs/The_Website_deployment_plan.md` §7.1 与 DESIGN P12.9/P12.11。

## 未验收（依赖真实部署）

- 真实 P50/P95（生成时长、首段可听、列表/详情延迟）需在配置真实 LLM/
  TTS 凭证的服务器上按 §19.6 基准环境测量，当前无凭证（同 K02 记录）。
- 「开放默认入口」（发布模板 `CLASSROOM_ENABLED=1`）须待 K02 真实
  provider 烟测与 §19.4 内容质量样本评审通过后由管理员执行。

## 建议发布顺序

1. 部署 main（含课堂代码，总闸默认关）→ 旧聊天回归；
2. `CLASSROOM_ENABLED=1` + `CLASSROOM_ALLOWED_USERS=<测试账号>` →
   按 §19.6 测量 P50/P95、跑烟测与内容样本；
3. 达到 §19.4 发布门后清空 allowlist 开放，记录本文件补齐。
