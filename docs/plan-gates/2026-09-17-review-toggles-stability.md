# 2026-09-17 执行计划：出题草稿根治 + 配图稳定化 + 审查开关

分支：`fix/quiz-review-toggles-stability-20260917`（自 `main@b0da8e2` 切出）

## 0. 审计结论（已核实）

两阶段（文字 27s + enrichment）架构已在 main 落地；剩余根因：

1. **草稿频出**：`generator._lift` 把"critic 未通过/未审核/critic 自身失败"一律打 `q_draft_`
   前缀，前端草稿卡无提交按钮=死路。critic 12s 超时或漏掉某题即触发。
   判分侧并不拒绝未审核题——阻塞只在前端前缀。
2. **配图总失败**：`quiz_illustration_enrichment` 生成调用超时 7s / max_tokens 3200，
   真实模型出 2-6KiB SVG 常超 7s；审计调用同样偏紧；审图任一失败=整体 failed。
3. **sanitizer 仍偏严**：`opacity/fill-opacity/stroke-opacity/font-weight/font-style/
   font-family/text 的 dx,dy/dominant-baseline/数值带 px 单位/letter-spacing` 等常见
   写法会被拒，触发不必要的修复轮。
4. **无审查开关**：critic 由全局 env `QUIZ_VERIFY_MODE` 控制；审图无开关且强制执行。

## 1. 后端改动

### 1.1 用户 prefs（`identity` + `user.py` API）
- prefs 校验器接受布尔：`quiz_critic_enabled`（默认 true）、
  `quiz_illustration_review_enabled`（默认 true）。
- `quiz_illustration_policy.py` 新增：
  - `account_allows_quiz_critic(sid) -> bool`（读 prefs；异常默认 true）
  - `account_allows_illustration_review(sid) -> bool`
  - `effective_quiz_verify_mode(sid) -> "critic"|"basic"|"off"`
    （env=off/basic 时维持 env；env=critic 且用户关闭→basic）

### 1.2 草稿根治（`agents/assessment/generator.py`）
- `_lift` 语义分级：
  - critic ok + 单题 passed → `q_` + answer_verified=true（不变）；
  - critic ok + 单题 unreviewed、或 critic error（fail-open，与 quiz_verify
    现行语义一致）、或 effective mode=basic（用户/环境关闭 critic）→
    **`q_` 正常题** + verification.status="unreviewed"（不冒充已审核）；
  - critic 明确 rejected/revision_required → 不 lift（走既有修订/重试）；
  - 仍保留最终 `_self_check` 保底（生成+修订+重试全失败时）。
- `generate_question` 把 effective verify mode 传入 `generate_verified_questions`
  （新增 `verify_mode` 参数，默认 None=读 settings；仅 CAT 路径传用户态）。

### 1.3 配图预算重平衡（`quiz_illustration_enrichment.py`）
- 文字题先展示、enrichment 异步化后，18s 硬预算不再对应任何用户阻塞；
  调整为：deadline 30s、生成调用超时 18s、审计超时 10s、终审预留 4s、
  修复启动门槛 remaining≥8s，逻辑调用仍 ≤3。
- 审图开关：`account_allows_illustration_review(sid)=false` 时跳过审计 LLM 调用，
  sanitizer（确定性）仍强制执行；required 语义不变（必须有图）。
- 审计 max_tokens 收紧（纯判定 JSON ~600；allow_repair 时 2000）。

### 1.4 sanitizer v2.1（`quiz_illustration.py`）
新增（全部仍走白名单重建，安全边界不放松——继续拒绝 script/foreignObject/
style 元素/image/use/动画/事件/外链/任意 CSS/DTD）：
- presentation 属性：`opacity/fill-opacity/stroke-opacity`（0..1）、
  `font-weight`（normal|bold|100..900）、`font-style`（normal|italic|oblique）、
  `stroke-miterlimit`（≥1）、`letter-spacing/word-spacing`（|v|≤100）、
  `font-family`（归一化为 sans-serif|serif|monospace）；
- `text` 支持 `dx,dy`（≤4 个数值）；`dominant-baseline` 受限枚举；
- 数值属性容忍并剥离 `px` 后缀（如 font-size="18px"）；
- `STYLE_PRESENTATION` 同步扩展（transform 除外）。
- prompt 语法说明（`illustration_grammar()`）自动包含新白名单。

## 2. 前端改动

- `ConfigCard`（出题中心=设置页）新增两个开关：
  - 「生成后审查题图」→ prefs.quiz_illustration_review_enabled
  - 「生成后审核题目质量」→ prefs.quiz_critic_enabled
  复用既有 updateUserProfile 保存链路与错误恢复；i18n 中英双语。
- 草稿提示文案不变（仅剩保底场景）。

## 3. 测试与验收

- 后端单测：sanitizer 新属性正/负例；enrichment 跳过审计；critic 关闭时
  正常 `q_` 前缀；prefs 校验；既有套件不回归（backend-core 相关文件）。
- 前端：tsc / lint / build 全绿。
- 本地起服（backend 8123 + frontend 3001，conda edu_agent），浏览器
  （Browser Use 主 agent 亲自操作）真实注册/登录 → 出题中心 → 勾选
  「必须配图」→ 开始测评：
  1. 文字题立刻可答、显示"正在生成配图"；
  2. 插图最终出现（`question-illustration` testid + 真实渲染检查）；
  3. **连续 3 次**稳定出图；每次失败必须定位根因修复后重测（不允许靠
     反复重试凑次数）；
  4. 观察后端日志的 generation_elapsed_ms/calls 校准超时参数。
- 真实 LLM（.env DEEPSEEK）验证 required 稳定出图后结束。

## 4. 文档与交付

- 更新 `docs/ASSESSMENT_ILLUSTRATION_PIPELINE.md` 与 `plan.md` §5/§9：
  新预算 30s、审查开关、草稿语义分级。
- 分支提交、push 到 origin（不直接推 main）。

## 5. 风险与边界

- 不改聊天 generate_quiz/fit_quiz 同步题图链；
- 审查开关只降级不升级（env=off 时用户不能打开 critic）；
- sanitizer 只加"可安全约束"的属性，主动内容/外链/任意 CSS 仍拒绝；
- single-flight、缓存、active-current 成本门不动。
