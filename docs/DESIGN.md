# DESIGN.md — Next Tutor Agent 架构设计文档

> 本文档描述系统**当前版本**的完整架构。只记录现状事实：模块职责、数据流、接口契约、存储布局、开关与护栏。不保留任何历史演进叙事。
> 阅读顺序建议：§1 全景 → §2 身份层 → §3-§5 对话内核 → §6-§7 RAG 与工具 → §9-§10 资料库与工作区 → §11-§18 各智能层 → §19 Skill Runtime → §20 前端 → §21-§24 运维。

---

## 1. 系统全景

### 1.1 产品定位

面向小学 / 初中 / 高中 / 本科阶段学生的智能学习 Agent。不是问答机器人，而是构建「学习目标 → 知识理解 → 练习训练 → 能力评估 → 调整」完整学习闭环的长期陪伴式私人学习智能体。产品形态是**学生学习空间（Learning Workspace）**：以对话为核心，辅以总览、知识图谱、学习计划、测评、记忆、资料、画像等模块页。

### 1.2 模块地图（M0 + M1-M10）

| 层 | 名称 | 一句话职责 | 包路径 |
|----|------|-----------|--------|
| M0 | 身份基础设施 | 用户是谁、数据属于谁、如何安全访问（注册/登录/注销 + 数据隔离） | `app/identity/` + `api/v1/auth.py` `api/v1/user.py` |
| M1 | 任务智能（Supervisor） | 这一轮对话怎么完成：理解 → 规划 → 工具执行 → 状态更新 | `app/agents/supervisor.py` 等编排模块 |
| M2 | 学生模型 | 这个学生会什么：画像 + 统一学习证据账本（证据式评价） | `app/agents/student_model/` |
| M3 | 教学引擎 | 这个学生现在该怎么教：六模式状态机 + 跨轮教学记忆 | `app/agents/teaching_engine/` |
| M4 | 测评智能 | 学生真的学会了吗：三级评分 + 约束出题 + CAT 自适应测试 | `app/agents/assessment/` |
| M5 | 知识智能 | 系统知道哪些知识：教材知识图谱（公用+自有）+ 概念检索 | `app/agents/knowledge/` |
| M6 | 记忆智能 | 有界 prompt 画像 + 策略聚合；旧情景/语义兼容只读 | `app/agents/memory/` |
| M7 | 评估改进智能 | 教师自己是否越来越好：TurnTrace 诊断 + 改进建议（人工确认） | `app/agents/evaluation/` |
| M8 | 交互体验智能 | 怎么表达最适合这个学生：UX 画像 + 输出适配（不改内容只改表达） | `app/agents/ux_intelligence/` |
| M9 | 学习编排智能 | 未来几周到几个月怎么学：多目标 → 周任务 → 今日任务 + SM-2 | `app/agents/learning_orchestration/` |
| M10 | 学习能力运行时与证据门 | Agent 能调用什么、为什么调用、契约是否满足、学习证据能否写回 | `app/agents/skill_runtime/` |

层次关系：M5 是横向**输入**基础设施（位于智能层之下，提供知识）；M8 是横向**输出**适配层（位于智能层之上，塑形输出）；M9 是纵向编排层（横跨 M1-M4 做长期规划）；M10 是横向**能力控制层**，统一可执行 Skill 契约、旁路决策与学习证据门。M0 不属于 M1-M10，是所有 Agent 的身份入口。

### 1.3 物理拓扑

```
浏览器 ──> Next.js 前端 (:3000) ──REST/SSE──> FastAPI 后端 (:8000, /api/v1)
                                                  │
   前端所有 API 调用由 NEXT_PUBLIC_BACKEND_URL 单一决定（见 §21.4）
                                                  │
        后端 ──> LLM（OpenAI 兼容 Chat Completions，必配）
             ──> 本地自备向量模型接口 / Embedding API（可选，RAG 向量轨）
             ──> 多模态视觉 API（可选，拍照识题；缺省回退本地 tesseract OCR）
```

SSE 为前端直连后端的流式通道（`POST /chat/stream`、`POST /quiz/grade`），生产同源部署时经 nginx 反代（需 `proxy_buffering off`）。

### 1.4 智能层正交开关

每层一个环境变量开关，默认全开（`1`），关闭任一层则上层自动降级、下层行为不受影响：

| 开关 | 默认 | 关闭后行为 |
|------|------|-----------|
| `AUTH_MODE` | `0` | `0`=游客宽容（未登录共享 `student_default`，登录后 JWT 始终绑定独立命名空间）；`1`=登录必选 |
| `SUPERVISOR_MODE` | `v2` | `legacy` 走 V1 `chat_turn` 单函数路径；v2 运行时异常默认显式返回错误；仅当 `SUPERVISOR_LEGACY_FALLBACK=1` 时回退 legacy |
| `STUDENT_MODEL_MODE` | `1` | `0` 关闭 M2：无画像/学习评价/策略注入（评价层按 §10.3 显式 disabled，不静默假装已评价） |
| `TEACHING_ENGINE_MODE` | `1` | `0` 退回 M2 内置轻量 adapt 路径 |
| `ASSESSMENT_ENGINE_MODE` | `1` | `0` 关闭 M4：评分退回二元、无 CAT |
| `KNOWLEDGE_INTELLIGENCE_MODE` | `1` | `0` 关闭 M5：SkillGraph 用自有种子，无知识指令 |
| `MEMORY_INTELLIGENCE_MODE` | `1` | `0` 关闭 M6：精简画像指令与写侧聚合 no-op |
| `EVALUATION_INTELLIGENCE_MODE` | `1` | `0` 关闭 M7：评估指令返回空、捕获 no-op |
| `UX_INTELLIGENCE_MODE` | `1` | `0` 关闭 M8：交互指令返回空、记录 no-op |
| `ORCHESTRATION_MODE` | `1` | `0` 关闭 M9：编排指令返回空、记录 no-op |
| `SKILL_RUNTIME_MODE` | 裸 uvicorn=`shadow`；`start.sh`=`gated` | `shadow` 只记录；`gated` 强制前置条件、澄清门、PlanStep 逐步工具暴露；`off` 关闭旁路诊断与 Skill Card |
| `STRUCTURED_ASSESSMENT_MODE`（W3） | `off` | `off` 旧三级文本批改；`shadow` 旁路计算量规条目分析并落盘对照（不改变判定/不写能力）；`active` 有冻结量规的开放题以结构化分析为权威判定（分数由服务端按量规权重本地计算） |
| `TEACHING_DECISION_MODE`（W3） | `rules` | `rules` 纯规则策略；`shadow` 触发门命中时旁路调用并记录 trace 对照；`active` 校验通过的教学决策以受限方式调整策略（单一行动/枚举白名单/显式约束优先） |

统一护栏原则：每层的读写钩子都包在 try/except 内，任何失败只记 trace，绝不影响对话流。

### 1.5 教材库与学段（P1-P6 改造后）

- **学段去僵化（P1）**：学段不再是硬编码默认「高中」。`grade=""`（空串，前端 token「自动」）即「自动」——不预置学段语境，由模型按提问内容/资料自适应深度与语言；显式学段（小学/初中/高中/本科）才注入七维度强约束。三级解析：会话级选择 > 全局默认偏好 > 自动。`stage_profile.is_auto/normalize_grade` 是唯一判定源，preamble/出题/M3 策略全走它。**产品默认学段为本科**：前端 store/注册页默认选中本科，后端各兜底回退（账户资料、Session/Context 缺省、stage_profile 未知档、quiz/assessment API 字段缺省、`COMPAT_GRADE`）一律本科；「自动」仍可手动选择，空串自动语义不变。
- **教材库（P2/P6）**：`TEXTBOOK_GRAPH_ENABLED`（默认 `1`；`=0` 时上传只解析+索引，跳过图谱构建直接 ready）。`TEXTBOOK_GRAPH_MAX_CHAPTERS=30` / `TEXTBOOK_GRAPH_MAX_CONCEPTS=400` 控制教材图谱规模（`custom_graph.spec_to_graph` 形参化）。教材 = Library 文件（`kind:"textbook"`）+ Textbook 注册记录 + M5.7 图谱，三者以 `file_id`/`topic_key` 双向链接；上传后 fire-and-forget 后台构建（分钟级），对话零等待。**上传必选学段**（小学/初中/高中/本科/其他，用户选择优先于骨架 LLM 推断，图谱节点按所选 stamp，知识谱系按学段分组）。**公用教材库（P6-B）**：`scope=public` 的教材落保留命名空间 `public`（文件/记录/图谱同构），所有账号可选用，仅管理员可写；`graph_for` 合并视图 = seed(空) ∪ learned ∪ 公用图谱 ∪ 自有图谱。**P6-A2：考纲 seed 包已全部删除**，知识只来自教材；手动构建图谱端点（custom/build 等）已移除。
- **教材组（多卷合一图谱）**：一套教材的上下册/分册（力学/光学/电磁学各一个 PDF）可编为**教材组**（记录 `kind:"group"` + 有序 `file_ids`，自定义组名）。构建 = 逐卷走与单教材相同的「OCR+切片+抽取」得 spec，再把各卷章节列表（章名加卷前缀 `卷名·章名`）合并成**一个 spec 单次 `spec_to_graph`**——`name_to_id` 全局按名去重使跨卷同名概念合并为一个节点，按名前置引用跨卷成边（确定性，无额外 LLM）。概念预索引按章属卷限定检索域、条目 chunk_ids 跨卷混合（消费端零改动）。上传 `group` 参数成组、`group_id` 追加卷（自动重建，已 OCR 卷零重 OCR）；`DELETE /textbooks/{gid}/volumes/{fid}` 删卷（剩余卷自动重建，删空删组）；删组级联全部卷文件+图谱。公用组同 scope=public 规则（仅管理员可写）。
- **扫描/混合 PDF OCR（P5a 逐页择优）**：`PDF_OCR_MODE`（auto/on/off，默认 auto 按**逐页**稀疏判定触发：存在任一稀疏页即逐页择优 OCR，文本层达标页原样保留；on 强制整本）、`PDF_OCR_MAX_PAGES=1024`（教材库后台 OCR 页数上限）、`PDF_OCR_SYNC_MAX_PAGES=20`（对话/资料库同步段 OCR 页数上限，保护响应性）、`PDF_OCR_DPI=200`、`PDF_OCR_CONCURRENCY=5`（后台 OCR 并行批次大小，1=串行；账户资料 `prefs.ocr_parallel` 逐人覆盖开关）。OCR 合并结果空页占位、页码与物理页对齐；写回 `.txt` 走原子写并同步 library 元数据；OCR 覆盖按当前文本稠密页推导（rebuild 不重复 OCR，调高上限自动续扩）。视觉模型复用 `MULTIMODAL_*`（默认关闭思考/最低强度，`MULTIMODAL_DISABLE_THINKING=1`；单页调用按 `MULTIMODAL_OCR_RETRIES=3` 对异常/空 content 退避重试，耗尽才回退）；未配走本地 tesseract。**启动收割**：构建是进程内 asyncio 任务，服务启动时残留 `building` 记录由 lifespan reaper 置 `graph_failed`（可重建）。
- **记忆收敛（P6-D）**：`CROSS_SESSION_MEMORY` 仍控制 transcript/详细跨会话召回；用户级 prompt memory 另由 `students/<id>.prompt_memory.json` 管理，普通对话与工作区对话全局统一计数，默认最近 15 个会话、可选 5–30，只有总体水平/学习概况/语气/讲解偏好进入 prompt。工作区 public_memory 始终只在同一工作区可见。
- **管理员（P6-B）**：`ADMIN_EMAIL`/`ADMIN_PASSWORD` 启动引导（lifespan 确保账号存在且 role=admin）；`require_admin` 依赖；`GET /admin/users`、`POST /admin/users/{id}/clear-chat`、`DELETE /admin/users/{id}`（不可删 admin）、`GET/POST /admin/orphan-data[/purge]`（孤儿数据清理，`core/orphan_cleanup.py`）。

---

## 2. M0 身份基础设施

### 2.1 核心模型

- `user_id == student_id`：注册用户的 user_id 直接作为 student_id，自动获得独立的 M2-M9 数据命名空间（`students/<user_id>.*`），无需显式「创建学生」。
- 未登录身份为游客，共享 `student_default` 命名空间。
- `resolve_student_id()`（`identity/deps.py`）：FastAPI 依赖，从 `Authorization: Bearer` JWT 解出 user_id；**任何 AUTH_MODE 下 JWT 均生效**（AUTH_MODE=0 时登录同样获得隔离空间），无 token 回退游客。所有投影 API 经 `Depends(resolve_student_id)` 注入。

### 2.2 数据隔离边界

- 学习数据：`students/<student_id>.*` 全部按 id 物理分文件（画像/学习证据账本/教学日志/记忆/编排/UX/评估）。
- 会话历史：`GET /chat/sessions` 只返回当前身份的会话（无 student_id 戳的遗留会话归游客）。新账号从空白历史开始。
- 工作区：创建打 `student_id` 戳、列表过滤、按 id 端点对外人 404（不泄露存在性），移入会话也校验归属。「共享」指同一 owner 的多个对话间共享，而非跨用户。
- 资料库：每用户一份（`chat_history/library/<student_id>.json` + `data/<student_id>/`），互不可见。
- **JWT 唯一事实源（铁律）**：任何端点的 student_id 只来自 `resolve_student_id()`，请求体/query 里的 student_id 字段仅为旧客户端兼容保留、一律忽略。回归实案：CAT 测评 5 个端点曾信任 body 的 `req.student_id`，登录用户的测评数据错落到游客命名空间且可跨用户读写——已修复并加 `test_assessment_identity` 回归（伪造他人 id 无效、游客伪造同样无效）。

### 2.3 账户 API 与安全

- 端点：`GET /auth/status`、`POST /auth/register`、`POST /auth/login`、`POST /auth/logout`、`GET /auth/me`、`GET/PUT /user/profile`、`DELETE /user/account`（自助注销，需密码二次确认；名下全部数据随账号不可恢复清除——`account_data.purge_account` 语义：会话/转写/trace/上传/工作区/资料库/回收站/笔记/学习档案/知识图谱逐层清空且不留空目录，账号记录最后删、中途失败可重试；记录删除后 JWT 即失效）。
- **管理员（P6-B）**：`User.role` 含 `admin`；`.env` 的 `ADMIN_EMAIL`/`ADMIN_PASSWORD` 在启动 lifespan 引导（不存在则创建、存在则提升）。`require_admin` 依赖（401/403）。管理端点：`GET /admin/users`（账号列表，公开字段）、`POST /admin/users/{id}/clear-chat`、`DELETE /admin/users/{id}`（注销，不可删 admin 含自己）、`GET /admin/orphan-data` + `POST /admin/orphan-data/purge`（孤儿数据扫描/清理：测试残留、注销遗物、无引用 trace、失会话转写、空回收站目录；注册账号与 `public`/`student_default` 共享命名空间受保护）。公用教材库写操作（上传 scope=public / PATCH / DELETE / rebuild）全部仅 admin。
- 密码 bcrypt 哈希（`AUTH_BCRYPT_ROUNDS`），`to_public_dict` 绝不返回 `password_hash`。
- JWT secret 仅在 `identity/config.py` / `security.py` 使用；默认值仅开发用，前端会警告，生产必须替换（`AUTH_JWT_SECRET`）。
- 登录接口限流（`core/ratelimit.py`）。
- `users/`、`students/` 均被 `.gitignore` 覆盖。

### 2.4 前端耦合

品牌认证页（`AuthShell` 分栏：品牌栏 + 表单栏，中英双语）：登录页 + 两步注册页（账号 → 学习信息，`?redirect=` 回跳且登录↔注册互跳不丢）；注册**不采集学科**（学科之后经学习行为沉淀）。TopBar 全模式可见登录入口与用户名/登出；路由守卫（AUTH_MODE=1 时重定向 `/login`）；`apiFetch` 全局 token 注入（REST + SSE）；我的画像页账户卡（登录态查看/编辑 UserProfile）与危险区自助注销（密码 + 输入「注销」双重确认）。

---

## 3. 对话内核（M1 · Supervisor 编排层）

### 3.1 八步管线

`agents/supervisor.py::run` 是一轮对话的生成器（逐事件 yield SSE）：

1. **understand**：`task_understanding` 结构化任务理解（intent/subject/concept/requires_tools + response_format/allow_followup_assessment + `structured_quiz_request`）；`quiz_intent_system@1.0.0` 与理解器同一次低预算 LLM 调用识别“给我来个题/考我一下/想练练/quiz me”等模糊新题语义。模型失败或自相矛盾时，`new_question_request_score()` 组合请求短语、动作词+题目对象和已有题否定信号兜底；“一句话/简短/不要出题”等显式输出契约仍由确定性规则覆盖 LLM 猜测。
2. **snapshot**：`derive_snapshot(session)` 派生轻量 StudentSnapshot（grade/资料/quiz_count），不臆造数值化掌握度。
3. **skill decision**：M10 构建 TaskFrame，确定性检查 Skill 前置条件并把候选/拒绝原因写入 Trace；`gated` 模式随后对计划执行硬门。
4. **plan + strategy alignment + gate**：`planner.make_plan` 产出 TaskPlan + goal，步骤携带 `skill_ids`；M3 教学策略若声明 `next_check`，M10 将可执行的 `generate_quiz` 收尾检测连同结构化参数加入计划；随后 gated 模式移除不满足前置条件的 Skill、收窄 generate/fit 歧义，缺参考题时改为最小澄清计划。
5. **context assembly**：GSSC 组装（见 §4），含 preamble（`[当前教材]` 块 + 学段细则/自动轻约束 + 语言 + 资料清单）、各层软指令与本轮 Skill Card。
6. **execute**：`executor.execute` ReAct 循环（原生 function-calling），逐事件转发并记录 Skill 后置条件。
7. **persist + update**：写 assistant 消息、更新 TaskState、持久化、更新工作区公共记忆，然后依次跑写侧钩子 6b-6g。
8. **done**：产出最终事件（含 trace_id）。

### 3.2 任务理解（task_understanding）

四层回退，越快越省：

0. **待答上下文匹配（W3/D02）**：`_session_context(session)` 先投影有界会话上下文（最新一套的待答题型/题干前缀/选项键值、正在教学概念、最近判定）——显式选项点选（"B"/"选C。"/选项原文）确定性解析为 `goal=answer_pending`（判分仍在题卡 API，本轮规划为空计划不出新题）；「继续/下一题」存在待答题或正在教学概念时保留指代，不再落寒暄快道。
1. 规则短路：问候/寒暄直答（`requires_tools=False`），不进 ReAct。
2. 关键词 + 正则：学科/概念/意图（讲解/做题/复习/规划等）确定性匹配。
3. LLM 兜底（300 token 小调用）：结构化 JSON 输出 + 【会话上下文】块（understand_system@1.3.0：短消息指代待答题目、引述非学生自述），解析失败回退规则结果。

### 3.3 规划器（planner · hybrid）

- 规则 fast path：明确意图（如「出几道题」）直接给确定性步骤序列。
- LLM planner：复杂任务生成计划，validator 校验，失败回退规则计划。
- 跨轮 TaskState（`state.py`）：current_goal/completed/remaining，持久化到 `session.supervisor_state`，多轮任务不丢进度。

### 3.4 能力路由（router）

M10 Registry 将 Agent Skill 投影为能力 → 工具子集，收窄 LLM 每轮可见工具；PlanStep 有 `skill_ids` 时优先按 Skill 绑定路由，旧 `suggested_tools` 仅作兼容：

| 能力 | 工具 |
|------|------|
| knowledge | `knowledge_search` |
| assessment | `generate_quiz` + `fit_quiz` |
| memory | `recall_history` |
| teaching | （无工具，纯讲解） |

### 3.5 执行器（executor · ReAct 内核 + 护栏全集）

- 单派发循环 + `MAX_STEPS=6`（`AGENT_MAX_STEPS`）硬顶防死循环；重复调用防护（同工具同参数第二次直接告警）。`shadow/off` 保持整计划工具并集，`gated` 只暴露当前 PlanStep 工具，后置条件通过后推进下一步，计划结束后工具集清空并生成最终回复。
- 参数校验（R1）：工具入参 schema 校验，非法即返回错误 + 恢复建议。
- 结果反射器（R2）：0 结果/超短解析自动附告警，提示 LLM 换策略。
- 熔断器（R3）：per-tool 连续失败 3 次禁用，60s 半开恢复。
- 输出截断（R4）：工具结果 >2000 字截断，溢出全文持久化到磁盘（tool_spill），LLM 可见截断提示。
- 错误恢复（R13）：每个错误码对应恢复建议注入工具结果。
- `_lite_tool_calls`：done 事件里回传瘦身版工具调用（去大 payload）。
- **M10 后置条件**：ToolResult 映射回 Skill，验证资料定界/题目集合/历史定界等确定性成功标准并写 `skill_postconditions` Trace。
- **策略—Skill 对齐与必执行兜底**：`PlanStep` 可携带 `tool_args/auto_invoke`。只有教学策略明确要求、参数已由系统确定且工具在当前会话真实安装时才启用；模型正常函数调用优先，若只用文字模拟新题而漏调工具，Executor 发出 `skill_plan_auto_invoke` 并按授权参数补执行。`structured_quiz_request` 会把模糊的新题请求规范化为 generate/fit 的确定性题卡计划；已有题求解仍不自动出新卡。
- **推理预算防饿死**：`EXECUTOR_TOOL_THINKING=1`（默认）时工具步保留模型 LOW 思考（预算充足时提升工具使用质量并为 real_summary 提供真实推理材料）；`budget_forces_direct` 在输出预算被压到答案保留区以下时仍强制关闭 thinking；若 provider 以 `finish_reason=length` 返回空或半截答案，执行器记录 `incomplete_answer_recovery`，保留已流式输出的前缀并用关闭 thinking 的第二次调用续写——续写指令显式禁止复述此前轮次内容（思考型模型会把整轮预算耗在隐藏推理后，于重试时复读上一轮答案）；最终回答与工具前讲解会合并写入会话历史，不再只显示深度思考或丢失前半段。工具步输出信封由 `EXECUTOR_TOOL_MAX_OUTPUT_TOKENS` 控制（默认 6000，旧硬顶 4000 常被思考吃光触发恢复，重试本身比放大信封更费 token）。`=0` 回到旧行为（有可见工具即关闭执行阶段 thinking）。
- **R10 确定性预检索（反幻觉关键）**：`agents/preresearch.py` 是 legacy/Executor 两条路径的统一判定源。**本轮上传文件/图片、引用资料中心教材，或明确说“根据教材/附件/这份资料”时必须在回答前检索**；工作区仅仅存在资料不会让问候等无关轮次强制检索，内容型问题仍可由 Planner/M10 自主安排。Executor 用完整工具表查找 `knowledge_search`（不受 router 收窄影响），当前轮附件携带 file_id 时只检索这些文件，避免引用 A 却命中 B；结果命中则注入“严格基于原文”，未命中则注入“如实告知、禁止凭文件名编造”。Trace 记录 `grounding_reason/grounding_file_ids`。
- **R10 预检索查询精炼（search_queries）**：understand_system（v1.2.0）让任务分析 LLM 同轮产出 `search_queries`（1–3 条概念/篇目/课文名精炼检索词，`task_understanding._clean_search_queries` 清洗：仅短字符串、≤20 字、≤3 条，整句口语丢弃）。Executor 预检索以 `search_queries[0]` 为主查询、其余经内部参数 `focus_queries`（不在 LLM 工具 schema 中）传给 `KnowledgeSearchTool._query_variants(focus=...)`：精炼词优先占据变体槽位，原句的确定性压缩词核/问句词核兜底占用剩余槽位保持召回广度。`search_queries` 为空（规则路径/LLM 失败）时保持旧的原句直查回落。Trace 在 `tool_result(reason=auto_preresearch)` 上记录 `query_source=llm_focus|raw_message`。V1 legacy 路径不接入（降级链路保持原行为）。

### 3.6 智能层读写钩子（集成点总表）

所有钩子 try/except 包裹，失败只记 trace。读侧产出「软指令」文本追加到 `adaptation_recap`，作为 system 消息注入（advisory，不强制 LLM）；写侧消费本轮信号更新各层状态。

| 钩子 | 层 | 函数 | 作用 |
|------|----|------|------|
| 3b | M2 | `_adapt_for_turn` | 学生画像+统一评价投影 → `[学生智能·…]` 教学策略软指令 |
| 3c | M3 | `_plan_learning_path` | intent=plan 时注入学习路径建议 |
| 3d | M5 | `_knowledge_directive_for_turn` | `[知识智能·…]` 概念定位/前置/易错点/教材引用 |
| 3e | M6 | `_memory_directive_for_turn` | `[提示词记忆·精简画像]`：总体学习情况/水平/语气/讲解偏好 |
| 3f | M7 | `_evaluation_directive_for_turn` | `[评估智能·…]` 历史失败模式提醒 |
| 3g | M8 | `_ux_directive_for_turn` | `[交互智能·…]` 表达适配指令 |
| 3h | M9 | `_orchestration_directive_for_turn` | `[编排智能·…]` 长期计划+今日任务 |
| 6b | M2 | `_collect_turn_events` | 学习观察来源（对话/题卡）经统一受理写入学习证据账本 |
| 6c | M3 | teaching_log 记录 | 持久化 (mode, outcome) 支撑跨轮推进 |
| 6d | M6 | `_memory_consolidate_turn` | 最近会话 prompt contribution + 策略/习惯聚合；不再追加详细 episodic/semantic |
| 6e | M7 | `_evaluation_record_turn` | TurnTrace 捕获 + 规则诊断 + advisor 门 |
| 6f | M8 | `_ux_record_turn` | 反馈分类 + 长度折叠 + 表达质量评估回画像 |
| 6g | M9 | `_orchestration_record_turn` | SRS 曝光登记 + 习惯/进度检查点 + 事件转发 M6（W4/A08：不再窥探当轮工具 verdict——判分都在 /quiz/* 轮外发生，recall 质量只经 `record_quiz_evidence` 提交事件流入口） |

---

## 4. 上下文工程（GSSC）

### 4.1 双存储分离

- **session.json**（`chat_history/<id>.session.json`）：压缩工作集，参与上下文组装。
- **transcript.jsonl**（`chat_history/<id>.transcript.jsonl`）：append-only 全量黑匣，永不裁剪，是 `recall_history` 的检索源。

### 4.2 三层上下文骨架

- **L1 系统红线**（不可压缩）：不臆造、不替考等教学红线。
- **L2 preamble**（半稳定）：`[当前教材]` 块（P3，本回合可见教材反查）→ 学段适配（P1：显式学段注入七维度细则，自动学段注入轻约束）→ 回答语言 → 文件清单 → 附件提醒 → 工作区公共记忆块。教材与学段两块共存时教材在前（教材决定回答依据，学段只调表达深度）。
- **L3 历史**（可压缩）：摘要（如有）+ 最近 N 轮 + todo_recap 置顶。

### 4.3 压缩（compaction）

- `estimate_tokens` 仍作为快速估算（CJK 1 字≈1 token，拉丁 4 字符≈1 token），但 `core/context_budget.py` 按完整协议消息估算 system/preamble/history/directives/Skill Card/tool schema，以及原生 Tool Message 的 call id/参数/result；每次 Provider 调用前重新核算，而不是只在回合开始估一次。
- `LLM_CONTEXT_WINDOW`、`LLM_MAX_OUTPUT_TOKENS`、`LLM_CONTEXT_SAFETY_MARGIN` 和软/硬比例共同计算输入预算；默认按 64K 平衡档、8000 输出上限、2500 安全区、0.72/0.88 阈值规划，L3 历史默认封顶 24000（兼顾长周期辅导/多资料 RAG 的深度与单轮输入 token 成本，窗口更大的模型不必再调高）。若工具结果加入后可用输出空间缩小，单次 `max_tokens` 会自动下调；当下调侵占答案保留区时优先关闭 thinking，避免用硬窗口换取不可见思考。
- 压缩按完整 user-assistant 回合保留最近 `CONTEXT_RECENT_FULL_TURNS`，切点始终位于保留区首个 user 之前，不产生孤立 assistant/tool 结果；LLM 压缩调用显式 `disable_thinking=True`。二次压缩会把既有结构化摘要作为输入合并，避免旧信息被静默删除；若摘要 answer 为空则保留全部原始历史、不执行裁剪。压缩输入在对话原文前注入 `quiz_digest_for_session` 确定性出题/作答摘要（题目 payload 从不进入消息文本，「练习与错题」字段此前无米下锅），且在 20000 字头截之后才拼接，保证结构化作答事实永不丢失。压缩状态持久化 `session.compaction`，完整 transcript 仍可由 `recall_history` JIT 找回。
- Supervisor 发出适度详细的公开 `reasoning_summary`（任务重点、执行步骤、教学策略和检测安排）；Executor 跨 ReAct 步骤累积 Provider 原始 `reasoning_content` 进 done 事件（尾部 6000 字封顶）作为内部材料。**实时思考流（2026-09 起）**：`REASONING_LIVE_MAX_CHARS`（`agents/reasoning_live.py` 的 `LiveThinkingGate`，executor 与 V1 chat_agent 共用）默认 `-1` = 把 `reasoning_content` 逐段以 `thinking` 事件（`is_delta: true, summary: false`）实时流给前端「深度思考」区——显示流不进入 LLM 上下文、不写会话历史、不进语音 TTS；`0` = 恢复旧的隐藏 CoT 行为（仅模板摘要，且资料轮恢复强制关思考）；`>0` = 只实时展示前 N 字。live 开启时资料轮不再强制关思考（`budget_forces_direct` 与 `incomplete_answer_recovery` 仍是护栏），`real_summary` 仅在本轮没有任何 live 思考时触发（避免二次提炼与直播重复）。**持久化边界不变**：session.messages 与 done.thinking 仍存公开摘要（模板 + 可选 reflection），原始 CoT 全文绝不持久化、绝不进语音链路（voice WS 无 thinking 分支，显式丢弃）。
- `core/llm_runtime/` 提供无密钥的 `ProviderCapabilities` 与阶段化 `ReasoningPolicy`：工具阶段 LOW（`EXECUTOR_TOOL_THINKING=1` 默认保留模型思考，预算守卫与续写兜底 starving；`=0` 回到旧的 NONE 硬关闭）、普通讲解 LOW、复杂任务 MEDIUM；`adapter` 只在能力档案明确支持时下发 `reasoning_effort` 或 reasoning budget，Provider 拒绝可选字段时去参重试并写 `provider_capability_fallback`。多数兼容端点仍把 reasoning 与 answer 计入同一个 completion envelope，因此 Executor 同时记录两个通道的实际/估算 token；出现 `finish_reason=length` 或 answer 为空时，第二次调用关闭 thinking 并从可见前缀继续，确保学生最终拿到答案。`shadow` 只观测策略，不实际下发控制字段。
- `agents/reasoning_narrator.py` 将过程体验升级为多阶段摘要：understanding 说明任务判断，planning 说明 Skill 路线与教学策略，tool_result 说明结果整合或安全降级；`adaptive` 对 solve/diagnose/plan 使用 detailed，其余使用 standard。`response_format` 与 `allow_followup_assessment` 作为独立控制平面在 plan gate 后再校验一次，并在 Prompt 尾部重申，避免 M3 remediation/next_check 覆盖学生的“一句话/不要出题”要求。
- `core/session_learning_card.py` 提供 SessionLearningCard/OpenLoop：只投影当前目标、知识点、教学模式、当前 Skill、未完成检测、问题 ID 和最近 verdict；真相仍在 M2/M4/M6/M9、quiz_history 与 transcript。卡片在回合前注入有界只读摘要，回合后按计划与 ToolResult 增量更新；答题卡评分写回时立即关闭已完成 OpenLoop。
- `core/tool_context.py` 按工具语义生成模型上下文投影：quiz/fit 只给题目数、ID、考点和“前端已渲染”边界，检索/历史结果按长度截断并保留 spill 引用；完整 ToolResult 仍通过 SSE、quiz_history 和业务存储流转。默认 `on` 用于模型上下文，但完整 SSE/quiz_history/业务存储不变；可一键退回 shadow。
- `core/message_protocol.py` 生成 assistant tool_call + tool result 的内部标准消息影子，保留 call ID、工具名和参数；`TOOL_MESSAGE_MODE=native` 已切换当前回合为 assistant tool_calls + tool result；Provider 返回 400 时自动将本轮消息转换回 legacy 并重试。历史会话仍以普通教学回答 + SessionLearningCard 恢复，不重放旧工具 payload。
- `core/context_telemetry.py` 按当前用户 Session 关联的 trace_id 聚合 context_budget、llm_usage、reasoning/answer 通道、compaction、tool projection 与 fallback，只返回统计不返回 Prompt、用户正文、工具原文或隐藏 reasoning；`GET /evaluation/context-budget` 投影到现有系统洞察页。
- `start.sh` 默认启动完整运行时：Supervisor v2、Skill gated、LLM adapter、Tool Context on、Tool Message native、Reasoning Summary adaptive；读取显式 shell 或 `.env` 中对应非敏感模式作为覆盖，并优先使用 Edu 专用端口 8123/3001 和 pnpm。stop 只终止记录为本项目的 PID，不再扫描杀死 Paper_Agent 端口。
- `recall_history` 工具提供 JIT 检索：对 transcript 做 BM25，找回被压缩省略的细节——有损压缩因此可恢复。**P4 起支持跨会话**：工具携带 student_id，检索索引连带该生名下最近 8 个其它会话的 transcript 尾部（各 600 行），命中条目标注来源会话（`会话《标题》(MM-DD)`），「之前/上次讲过什么」不再丢；仍全程定界包裹。transcript 除师生对话外还包含两类系统记录（`core/quiz_attempts.py`）：出题成功时的【出题记录】（题干/答案/考点摘要，模型经 tool 投影与历史剥离后仍能找回自己出的题）与每次批改后的【作答记录】（题目/学生作答/判定），作答不再只存在于不可检索的 quiz API 黑洞里。
- 工具结果 truncate-then-persist（§3.5 R4），避免大 payload 反复进上下文。

### 4.4 会话生命周期

- 急切 id：首轮即分配 `chat_<datetime>_<slug>`，同一窗口跨轮追加同一 transcript；重命名只改 title 不改 id——「一个窗口 = 一条历史」。
- 轮数口径：`round_count` = assistant 消息数（一次 agent 回复算一轮，纯上传不计），侧边栏徽标与 `context_summary` 同一口径。
- 路径防护：`load/delete/rename/transcript_path` 均经 `_resolve` 剥目录防路径遍历。
- `add_trace_id`：轻量立即持久化 trace_id 关联。

---

## 5. SSE 契约

### 5.1 事件类型（`POST /chat/stream`）

| 事件 | 时机 | 载荷要点 |
|------|------|---------|
| `step` | 管线阶段推进 | planning / executing 等 |
| `thinking` | 过程思考增量 | `summary:true` = 模板/工具过程摘要；`summary:false` = Provider `reasoning_content` 实时直播（`REASONING_LIVE_MAX_CHARS` 门控，显示流不落盘不 TTS） |
| `answer` | 回答增量 | token delta |
| `tool_start` / `tool_result` | 工具调用与返回 | name / result（quiz payload 在此） |
| `tool_warning` | 反射器/熔断告警 | message |
| `tool_progress` | 工具内进度 | 如 OCR |
| `retry` | 退避重试 | attempt / wait |
| `history_saved` | 历史落盘备份信号 | session_id |
| `done` | 轮次结束 | answer/thinking 全文 + lite tool_calls + trace_id，**stamp session_id** |
| `error` | 失败 | 错误码 + 恢复建议 |
| `heartbeat` | 保活 | 15s 间隔 |

### 5.2 会话绑定契约（双保险）

后端 `chat.py event_stream` 在转发 `done` 前 stamp `session_id`（前端 done-handler 立即绑定），再发 `history_saved` 备份；前端 `done` 分支不 early-return（`break`）确保两条路径都被消费。违背此契约会导致每轮新建会话、多轮记忆失效。

### 5.3 韧性

- 429/超时/连接错误：指数退避重试（`_retry_max=4`，base 2s）。
- `asyncio.Semaphore(1)` 并发限流防 429 风暴。
- 前端渲染：token 本地累积 + 50ms 节流 flush，消息组件 `React.memo` + pinned-to-bottom 滚动，流式期间不整树重渲染；abort 保留已收部分。

---

## 6. 文件处理与 RAG

### 6.1 上传管线（`core/file_parser.py` + `core/multimodal_parser.py`）

- 大小限制：普通文件 256MB（P6-A1 放宽，整本扫描教材），图片 20MB；超限前后端都给出明确警告。
- 解析器：
  - **PDF**：PyMuPDF 按页取文本（页边界 `\f`）。**扫描/混合 PDF 逐页择优 OCR 回退**（`core/pdf_ocr.py`，P5a）：页字符 < 20 的稀疏页才渲染 pixmap → OCR，文本层达标页原样保留；空页占位保证物理页码不漂移。教材后台与聊天/工作区上传都走 `ocr.ocr_page_image` 的视觉模型优先→tesseract 回退；聊天/工作区受 `PDF_OCR_SYNC_MAX_PAGES` 昂贵页数上限保护并使用 async 入口，不阻塞事件循环。`PDF_OCR_MODE=off` 禁用，`=on` 在上限内强制 OCR。
  - **DOCX**：段落 + 表格按文档顺序展开；存在 embedded media 时有界提取图片并 OCR，纯文字文档不调用 OCR。
  - **PPTX**：递归提取组合形状/表格/备注页；嵌入图片走同一视觉 OCR，文本 slide 保留原文。
  - **TXT/MD**：多编码回退（utf-8 → gbk 等）。
- 图片：OCR 双通道——默认本地 tesseract（chi_sim+eng）；配置 `MULTIMODAL_*` 走视觉模型。聊天图片在选择时直接通过 `/chat/upload` 写入当前 session KnowledgeStore（原件 + OCR 文本 + chunks），同时返回预览；发送问题时作为 current-turn attachment 强制触发 R10，而不是只把 OCR 文本临时拼入 Prompt。

### 6.2 结构化切块（`core/retriever.py::chunk_text`）

`\f` 页硬块 → 段落打包（≤500 字）→ 句子边界吸附 → 80 字重叠；`chunk_id = file#idx`，带 file_id/页码元数据。检索结果稳定输出 file_id/filename/page/chunk/section/chapter/source_scope/location_label/retrieval_strategy；PDF/PPTX 显示页/slide，无法确定时明确“未标页”。

### 6.3 混合检索

- **BM25 轨**（常驻确定性，`core/retriever.py`）：CJK 感知分词（bigram）。
- **向量轨**（可选）：`EMBEDDING_PROVIDER=off|local|openai`，默认 off。local 是模型无关的离线接口（部署方自备模型与推理运行时；CPU、懒加载、单槽 worker、归一化向量，仓库不内置/不固定任何本地模型），openai 保留兼容 Embedding API；任一故障自动回退 BM25。Chroma collection 按模型/维度/chunk schema/归一化/RAG revision 指纹隔离。公共教材以可校验 NPZ 分片提交 Git，再导入部署本地 Chroma；绝不提交或复制混合私有数据的 `knowledge/vector_db`。`RAG_HYBRID=0` 强制关闭。
- **融合**：RRF（k=60）。
- **多查询与跨文件覆盖**：原始问题 + 确定性关键词/中英术语扩展独立召回后 RRF 去重；最终结果先按文件分桶保证相关教材卷覆盖，再按总相关度补齐，避免一个大卷垄断 top-k（不相关卷不会被硬塞）。
- **小库直通**：chunks ≤ max(top_k, 8) 时跳过打分全量返回——否则小文件（一句话笔记）对任何转述式提问零命中，会诱发「凭文件名编造」。

### 6.4 文件级摘要

上传后 fire-and-forget 生成 ≤150 字摘要 + 主题标签，随文件元数据持久化，注入 planner/preamble——LLM 检索前即知每份资料覆盖什么。

### 6.4b 教材管线（P2）

教材是一等公民。上传教材 PDF → 同步解析+切块+BM25 立即可检索（文本提取经 `asyncio.to_thread` 不阻塞事件循环）→ 后台（`asyncio.create_task`，分钟级）构建专属知识图谱：
0. **OCR 阶段（按需，P5a）**：文本层为空或存在稀疏页时先逐页择优 OCR（auto）或整本 OCR（on），合并结果空页占位（页码不漂移），原子写回 `.txt` 并同步 library 元数据（char_count/chunk_count/chunks）后再进入切片。
1. **章节切片**（`textbook_builder.extract_chapters`，四段回退）：PDF 书签目录（`fitz.get_toc()` 页码范围切片，**优先「章粒度」层级**——某层 ≥2 条匹配中文/阿拉伯/全角数字的第N章或 Chapter 才选它，避免「篇」容器级；**切片对象是构建时的合并文本**，扫描书因此能用书签切 OCR 文本，超范围条目跳过/截断）→ LLM 目录提取（文本开头 8000 字产章节名）→ 开头目录短行的确定性识别 → 整书单章。`locate_chapters` 在 NFKC+去空白索引上容错匹配普通空格、U+3000、换行、制表符和全角数字，但始终映射回原文切片；仍优先标题**第二次**出现位置规避目录页陷阱。长教材若只能落到“全书”单章，会写 warning + `needs_reextract`，不把降级 cache 当健康缓存；短讲义仍允许单章。切片携带页码区间（Tier 1），供概念预索引限定检索域。
2. **骨架 LLM 调用 ×1**（`disable_thinking=True`）：推断 subject/level（**仅补缺**——用户上传时已选学段/学科优先）。
3. **逐章概念抽取 LLM ×N**：每章 1 次（≤20000 字全文走单次 `custom_graph.generate_spec` 快速路径；章文本预算 24000 字）。超过预算的长章按章首/章中/章末分层抽样，避免只取前 24000 字而稳定遗漏章末总结知识点。
4. **确定性合并**：复用 `custom_graph.spec_to_graph`（id 命名空间 `custom.tb-<id>.*` / 两遍扫描前向引用 / PREREQUISITE DAG 守卫 / 严格锚定主图 RELATED），上限参数化（`TEXTBOOK_GRAPH_MAX_CHAPTERS/MAX_CONCEPTS`），level 取上传所选（四学段或「其他」）。
5. **写入 M5.7 store**（`knowledge/custom/<sid>/tb-<id>.json`），`KnowledgeService.graph_for` 合并视图（自有 ∪ 公用）让教材图谱自动进入 `/knowledge/graph`、概念检索、supervisor 知识指令。
6. **概念预索引（P6-C2）**：构建末确定性生成概念→chunk_ids 倒排（概念 name/aliases 在其章节页码范围内的 chunks 子串预过滤，每概念 ≤50），存 `knowledge/custom/<sid>/<topic_key>.chunks.json`（随图谱删除联动）；`knowledge_search` 命中教材图谱概念时优先在该概念章节检索域内检索前置返回。

per-student 构建锁 + 全局 Semaphore(2) 防 429；任何异常落 `status=graph_failed`（教材仍可检索），可经 `rebuild_graph` 重试（归档+原子替换）。`TEXTBOOK_GRAPH_ENABLED=0` 时跳过图谱构建直接 ready。**启动收割**：构建任务是进程内 asyncio 任务，进程死亡即孤儿——FastAPI lifespan 启动时 `reap_stale_builds()` 把残留 `building` 记录置 `graph_failed`（错误信息提示可重建）。

### 6.5 反幻觉组合（实测修复的链路）

1. R10 预检索硬约束（§3.5）。
2. 小库直通（§6.3）。
3. `knowledge_search` 未命中时明确禁止编造。
4. 工作区公共记忆注入声明「与检索原文冲突时以检索原文为准」。
5. 会话恢复时从 `uploads/<id>.txt` 重建 KnowledgeStore（`from_dict`），历史对话仍可检索已上传资料。
6. attachment reminder：有附件的轮次 preamble 提醒 LLM 资料存在。

---

## 7. 工具系统与测评交互

### 7.1 四工具（统一 `core/tool_protocol.py` 协议）

| 工具 | 作用 |
|------|------|
| `knowledge_search` | 检索上传资料原文（§6），命中标注页码 |
| `generate_quiz` | 按学段/难度生成结构化练习题（题干+选项+答案+分步解析） |
| `fit_quiz` | 拟合出题：从参考题拆解知识点，五变式策略（情境迁移/结构反转/条件增减等）生成同考点变式题，非换数字 |
| `recall_history` | JIT 检索本会话 transcript（§4.3） |

### 7.2 出题交互闭环

**当前题卡受理与恢复（2026-09-16 更新）**：以当前代码的 JSON 202
受理协议为准，以下历史段落中的 `/quiz/grade` SSE 描述已不适用。
聊天题卡的选择/填空/简答均经 `/quiz/record` 统一受理；获得
`attempt_id` 即锁定，`verdict=null` 不代表未提交。会话缓存保存完整答案，
按 `question_id + question_revision` 写入 quiz_history 和 toolCalls。
`GET /quiz/submission?question_id=...&question_revision=...` 从本人学习证据
journal 纯读恢复受理、当前本题判分和学习反馈（未提交为 null；他人题
404，版本不匹配 409），不触发模型或追加受理。前端按返回的 `pending`
有限轮询，独立于长期评价状态，支持无学习区的 task-only 判分；失败或
未判定仍保留只读答案。旧会话丢失 result 缓存也可由题目身份恢复。
SVG 题图代码已接入；自动化测试记录和剩余人工验收见根目录 `plan.md` 第 17 节。默认部署开关为 `1`，运维可设置为 `0` 立即关闭所有新图生成。

**结构化题目 SVG（2026-09-16）**：沿用三条出题路径，题图与题面同次生成、同次审核、先冻结再交付。`QUIZ_SVG_ENABLED` 是默认 `1` 的运维总闸，设为 `0` 时所有入口 fail-closed；登录账户 `profile.prefs.quiz_svg_enabled` 缺省 true，读取失败或无可信账户则禁止新图。`/user/profile` 的 GET/PUT 响应增加 `quiz_svg_available`，PUT 在账户锁内浅合并偏好并严格校验新偏好为 bool。`illustration_request=auto|none|required` 是本次意图：关闭总闸时 auto/none 为 off、required 返回 `illustration_disabled`；开启后 auto 按题必要性选择，required 每题必须带图。Chat provider 绑定可信账户与当前用户意图，模型工具参数不能自行开启开关或伪造强制要求。CAT start 接收该枚举并在实例持久化，next/恢复复用，409 不终止已有实例。

`core/quiz_illustration.py` 以 defusedxml 解析后按闭合白名单重建黑白 SVG（最多 24KiB、180 节点、深度 10、800 路径段）。禁止脚本、HTML、CSS、外链、引用、动画和 DTD/实体；不把非法图删掉后原样交付依图题。规范化对象 `{kind,schema_version,sanitizer_version,svg,alt,caption,width,height,content_hash}` 沿 `Question → TaskSnapshot → QuestionPublic` 保存与投影，并进入本题评分上下文。模型提供的审核/内部字段不受信任；即使 `QUIZ_VERIFY_MODE=off/basic`，带图题也必须独立审题并得到 `illustration_check=passed`。Chat/Fit 等普通生成入口继续使用蓝图、生成、审核共享 7 次逻辑调用/180 秒预算、最多一次整题修订；CAT 首题使用 `get_llm("quiz")` 快速模型通道，跳过非必要蓝图轮但保留相同 SVG 校验与 critic，默认最多两次逻辑尝试、共享 6 次调用、90 秒截止。`QUIZ_MODEL` 优先，其次 `DEEPSEEK_MODEL_LIGHT`，SDK 重试默认关闭，避免 provider 与应用层重试相乘；provider 内部网络重试仍受统一期限约束。

题组注册在账户锁与 journal 锁内复查图生成权限、检查同题同 revision 材料不可变，再一次事务写入整组。账户开关变化不修改历史图；重练同一冻结题图仍可复用。journal 重放使用 `JournalTransaction.from_persisted_json`，先校验磁盘原始 envelope 再解析新增默认字段，避免将历史记录误判为 checksum 损坏；不批量迁移或重写旧行。

前端共用 `QuestionIllustration`：仅接收规范化版本对象，以 `img` 的 SVG data URI 图片上下文展示，不启用 Markdown raw HTML，不用 DOM 内联 SVG/object/iframe。题图自适应至 720px，深色模式反转黑白，支持放大、Esc 与焦点返回；失败显示图意说明。出题中心把账户开关和“本次必须配图”直接放在习题生成 `ConfigCard` 内（无学习区/加载态也显示），不再单独创建插图模块；“本次必须配图”仅在有效开关开启时可选。聊天结构化题卡、测评答题/反馈、报告回看与证据详情均复用该组件，保证题图始终位于结构化卡片内。源码摘要仅保存短 alt，trace 仅新增 hash/大小/版本/调用统计，不回灌完整 SVG。

- 前端渲染可交互卡片：MC 可点选（选中即高亮，揭晓后正确绿/错误红）、填空/简答可作答；先答后揭晓；出题后正文不复述题干。
- **MC**：本地判对错，揭晓即 `POST /quiz/record` 回传 → 统一受理（MC 确定性判定随受理事务落 journal；修复了 MC 不回传的闭环缺口）。
- **填空/简答**：`POST /quiz/grade`（SSE）LLM 批改，判等价 + 给思路，输出三级判定 + ≤120 字讲解。
- **评分入口信任边界（W1，updatePlan.md A01/A02）**：quiz 两端点的判分依据收归服务端。① 归属校验：任何非空 `session_id` 在 LLM/评分/持久化之前过 `_load_owned_session`（与 chat 路由同款 404「不可见」语义；legacy 未盖章会话归游客），`_write_back_answer` 内部再作 owner 纵深防御，整个 load-modify-write 临界区持 `file_lock`。② 服务端权威题目：请求里的 `correct_answer` 仅为旧客户端兼容字段——权威答案/选项/解析/知识点从本人会话 quiz_history 快照解析（先精确题干、后 60 字前缀且要求无歧义）；解析失败降级为「未验证练习」：`/quiz/record` 返回 `{status: "unverified_practice", code: "question_unresolved"}`、`/quiz/grade` 仍给反馈但 done 事件标 `unverified: true`，两者均零评价写入。③ 同提交幂等：quiz_history 中该题已有同作答 `result` 视为重复提交，直接重放已记录判定，不再二次评分（重试不产生额外模型费用）。④ 变式证据分级：来自 fit_quiz 题套的作答携带 `origin_question_ref` 同族关系进入统一评价，不再伪装独立观测。
- **作答落点**（`core/quiz_attempts.py`，fail-open）：quiz 端点在判定后统一写 ① session quiz_history 的 `result{verdict, student_answer}`（下轮经「近期作答」注入）；② transcript【作答记录】（当前会话/显式 recall 可检索）；③统一学习证据 journal（TaskSnapshot + SourceReceipt + 判分/语义解释，删来源对话仍保留）。`unknown` 判定（未真正评分）一律不落盘。MC 判分分支按声明题型（`q_type`）而非 options 是否存在路由——/quiz/record 早期不带 options 曾导致 `is_multiple_choice` 为假、判定静默退化为 unknown（选项现已纳入 RecordRequest 防御性回传）。对话轮保存前经 `merge_quiz_results_from_disk` 合并盘上作答结果，防止流式回合整文件覆写答题卡写回。**attempt 链**：每次受理由服务端生成 `attempt_id`（`att_` 前缀），贯穿 journal 来源、M9 归因与会话写回（quiz_history `result.attempt_id`）；答案指纹幂等使同一作答的重放/双写只有一次效果；出题侧题目 id 每套唯一前缀 `q_<uid>_<i>`，跨套不共用裸序号。generate_quiz / fit_quiz 成功均入 quiz_history（此前 fit_quiz 卡片作答无处写回），并写 transcript【出题记录】。已受理判定喂 M9 `record_quiz_evidence`（SRS 复习质量更新 + 任务归因，attempt_id 幂等；携带会话 task_binding 时只完成绑定任务）。
- **最近答题卡摘要**（`latest_quiz_digest`）：工具投影的题目摘要只存在于出题当轮，历史消息剥离 tool 载荷后模型就看不到自己出的题（「仔细讲解一下上一题」曾失败）。每轮 status recap 注入最新一套题的摘要——题干/答案/解析给足全文量级（300/60/260 字）+ 学生作答与判定，Agent 可逐题完整讲解点评；supervisor 与 legacy 路径一致；批改讲解遵循 LaTeX 排版规则（grade prompt 内置公式约束）。
- **跨会话最近习题**（journal 投影）：`GET /quiz/recent` 从学习证据 journal 生成最近习题分页（旧 `students/<id>.quiz_recent.json` 独立真相已随迁移退役），点击回到出题会话。
- **错题本**（`core/error_notebook.py`，P3）：按需实时聚合该生各会话 quiz_history 中 verdict ∈ {wrong, partial} 的题目（题干/学生答/正解/解析/来源会话，60 字题干前缀去重，新→旧，上限 200），`GET /student/error-notebook` 供测评中心「错题本」卡分页展示；「重练」走既有 `?q=&send=1` 深链让教练用 fit_quiz 出变式（复用答题卡/批改闭环，不在测评页复制作答 UI）。
- **答题卡交互呈现**：工具/答题卡按输出顺序渲染在正式回答**之后**（讲解在前、做题在后）；学生揭晓/提交作答后，前端自动在对话流底部追加一条 agent 点评消息——MC 点评经 `/quiz/grade`（`record=false`，只读不写评价，避免与 `/quiz/record` 双重记录）由 LLM 生成个性化讲解，主观题点评即 LLM 批改讲解；卡片自身只保留判定与折叠静态解析，不再与 agent 反馈重复。作答后卡片永久锁定（无重做入口），判定结果同步进消息 toolCalls 载荷，刷新/重开后恢复已答状态、禁止重复作答。
- 解析详细化：分步推导（知识点 → 公式数据 → 结论+易错点）。
- **生成可靠性**：generate_quiz / fit_quiz 内部由 `stream()` 改为 `complete(disable_thinking=True)`——推理模型此前把整个 max_tokens 预算耗在思考链上，answer 通道为空 → 解析 0 题 → partial 无卡片（模型只能在「深度思考」里文字拟题）。主观题评分 `_grade_open_llm` 同理加固。tutor_system@2.5.2 保留「思考中不拟完整题目，直接调工具」引导，并新增数学环境中文须 `\text{}` 包裹的 LaTeX 规则（前端渲染层另有 CJK 自动转 `\text{}` 兜底，中文下标正常显示）。
- **一题不二出与题型多样**：tutor_system@2.5.2 规定已有出题计划时讲解正文不得再写自测题/文字题目（此前「教学过程」的自测问题与收尾检测卡会同时出现，学生看到两道不同的题）；出题 prompt 要求题型多样——count≥2 至少一道 fill_blank/short_answer，单题按知识点特点选型（计算/推导/步骤/代码类优先填空简答），学生要求「别的类型」时必须换题型。
- **相关性与去重**：`generate_quiz` 构造时注入本会话最近 3 套已出题干（`avoid_stems`，prompt 声明禁止重复/仅换数字）；策略收尾检测的 auto_invoke 参数携带轮次 `focus`（用户原句侧重，如「滴定步骤」），收尾题检测本轮所讲而非宽泛概念——修复了逐轮重出同一道典型题的问题。
- **出题质量门**（`core/quiz_verify.py`，generator-critic 模式，`QUIZ_VERIFY_MODE=critic|basic|off`）：三条生成路径（两工具 + M4 约束出题/CAT）统一过两层校验——确定性结构校验（MC 答案字母须在选项内、选项非空去重、题干/解析非空，不合格直接丢弃）+ 一次独立重解 critic 调用（`complete(disable_thinking=True)`，逐题独立求解后核对拟定答案，判 incorrect 的题丢弃）；全部被判死时自动重生成一次，仍为空走既有 partial 路径。critic 自身故障 fail-open 放行并记入 `verification` 审计元数据（attempts/dropped/critic 状态），随工具结果进 SSE、quiz_history 与 Trace；M10 manifest 声明 `questions_answer_verified` 后置条件（工具内强制，runtime 侧 advisory）。错题在投递前被丢弃，错误答案不再进入学习证据，也不再锚定带偏主观题批改。
- **出题两轮化**（`core/quiz_design.py`，`QUIZ_DESIGN_MODE=two_pass|single`，默认 two_pass）：单轮直出（设计+写题+解析一步完成）是题目偏基础、偏记忆层的结构性根源。two_pass 模式下生成前先跑一轮「命题蓝图」设计调用（`complete(disable_thinking=True)`，prompt 注册为 `quiz_blueprint`）：第一步盘清该知识点的可考查角度（概念本质/机理推导/应用迁移/综合联系/陷阱反例），第二步为每道题定角度、目标布鲁姆层级（hard 必须落 analyze/evaluate/create，禁止纯记忆题充当）、题型、陷阱与区分度设计；蓝图渲染为 `[命题蓝图]` 块注入第二轮生成 prompt（generate_quiz 与 M4 约束单题共用），生成必须逐题落实。蓝图轮失败/解析失败自动回退单轮（fail-open），工具结果 `verification.design` 记录 two_pass/single/fallback。fit_quiz 不接蓝图轮——其 prompt 已内置「拆题→五层变式策略」的单轮两段式设计。
- **critic 深度拦截**：critic 在独立重解之外增加 `too_shallow` 判定——题目本身没错、但相对目标难度明显降档（纯记忆复述/定义默写/一步套公式冒充 medium/hard）时丢弃；easy 或未给目标难度不做此判定，拿不准一律判 correct（与 incorrect 同款「宁可放过」哲学）。丢弃计数进 `verification.dropped_shallow`。
- **量规与结构化反馈（W3）**：题卡/测评题携带冻结量规（`rubric`，D04）；开放题 active 模式判分后 done 事件与 `/quiz/record` 结果携带 `structured` 块（首个实质错误/此前做对部分/≤2 错因假设/下一步追问），QuizCard 判定块下渲染 F01 明细并提供「就此练习」按钮（下一步追问追加进对话流，学生在对话回应时 D02 上下文保持指代）；提交前可点「看提示」（F02，量规派生关键步骤，服务端记录 assistance）。概念抽屉（F05）显示证据区：v2 维度状态/支持作答/未测维度，异议入口跳测评中心重练。

### 7.3 三级评分（M4，详见 §14）

`[对]/[部分对]/[错]`：MC 确定性字母比对（零 LLM）；主观题 LLM 三级。所有题型进统一学习证据账本。

---

## 8. 回答语言策略

- `output_language`：`auto`（默认）/ `zh` / `en`，持久化到 `session.output_language`，恢复对话保留。
- `auto`：不注入语言指令，默认跟随 LLM 智能（中文语境答中文）；例外：翻译练习按目标语言产出译文。
- `zh`/`en`：preamble 注入强制指令（`forced=True`），确定性覆盖。

---

## 9. 资料库（Library · M7 资料底座）

核心原则：**资料默认私有**——上传到资料库的文件不属于任何对话，只有被学习区显式选入才参与检索。

### 9.1 存储模型

- 按账号隔离：`chat_history/library/<student_id>.json`（元数据）+ `chat_history/library/data/<student_id>/<file_id>.txt`（解析文本）+ `<file_id>.orig<ext>`（原件）。
- **chunks 惰性构建（P2 性能契约）**：`load_library` 只读元数据，**绝不**在加载时重切块——`Library.chunks_for(file_id)` 按需构建并落进程级缓存（`(namespace, file_id) -> (mtime, chunks)`，文本未变直接复用；`remove_file` 主动失效，内容变更经 mtime 自动失效）。急切重切块曾是列表/详情端点秒级延迟与启动窗口 CPU 饱和的根因（公共教材库 13 卷 V2 切块实测 ~7s/次）。检索/建图/向量路径一律经 `chunks_for()`，不得直接读 `chunks_by_file`（写路径生成后可直接赋值，读取优先内存）。
- 教材注册记录：`chat_history/library/<student_id>.textbooks.json`（P2，原子写+文件锁，记录状态机/进度/章节概念数/warnings）。教材 = 有 Textbook 记录的 Library 文件（meta 附带 `kind:"textbook"`）；资料中心「教材库/文件库」是同一存储的两个视图，零迁移。
- 两级组织：文件夹 + 文件，支持未归档区；文件夹行内增删改，文件可移动。
- **原件保留与下载**：上传同时存解析文本与原件，下载字节一致、保留原文件名与后缀（RFC5987 编码；无原名时 `file<ext>` 兜底）。原件功能上线前的老文件无原件：下载 API 404，前端按 `has_original` 隐藏按钮，提取文本仍参与检索。

### 9.2 工作区专属夹

每个工作区自动拥有同名专属夹（Badge 标识、不可手动改删），随创建/改名/删除联动；工作区内上传的资料进专属夹，随工作区删除级联清理。

### 9.3 API

`GET /library`、`POST /library/folders`、`PATCH/DELETE /library/folders/{id}`（专属夹 400）、`POST /library/upload`（`folder_id?`，存原件 + fire-and-forget 摘要）、`POST /library/files/{id}/move`、`DELETE /library/files/{id}`（文本+原件+摘要同步清除 + 级联清理孤儿 Textbook 记录）、`GET /library/files/{id}/download`。

教材库（P2，§6.4b）：`POST /textbooks/upload`（multipart，建 Textbook 记录 + fire-and-forget 图谱构建，限流 10/min；`group` 组名成组/`group_id` 追加卷）、`GET /textbooks`（join Library 文件信息，懒过滤孤儿）、`GET /textbooks/{id}`（记录 + 章节大纲 outline，前端 2s 轮询 building 进度）、`PATCH /textbooks/{id}`（title/subject/level）、`POST /textbooks/{id}/rebuild_graph`、`DELETE /textbooks/{id}`（级联：图谱归档删除 + Library 文件 + 向量 + 记录）、`DELETE /textbooks/{gid}/volumes/{fid}`（删卷：剩余卷重建/删空删组）+ `GET /textbooks/{gid}/volumes/{fid}/download`（卷原件）。

### 9.4 前端资料中心页

左侧来源树（全部文件 / 未归档 / 我的文件夹 / 学习区专属夹 / 各会话附件只读组），右侧文件卡片（下载 / 移动到… / 删除），拖拽或点击上传到当前选中来源。教材库/文件库双 Tab 由路由段承载：`/resources/files`（默认）与 `/resources/textbooks`（教材重组件 `next/dynamic` 懒加载分包）；`/resources` 落点重定向，旧深链 `/resources?tab=textbooks` 兼容跳转。

---

## 10. 工作区（Workspace 系统）

类似 ChatGPT Projects 的文件夹系统，支持跨对话共享知识与公共记忆；知识底座是按账号隔离的资料库（§9）。

### 10.1 数据结构与来源选择（scope）

- id：`ws_<ts>_<slug>`；字段含 `library_folder_id`（专属夹）、`selected_file_ids`（外部教材）、`workspace_file_ids`（工作区拥有的共享上传）、`public_memory`；`selected_folder_ids` 只作旧格式兼容，不再是授权入口。
- **外部来源只保留教材**：`selected_file_ids` 仅接受已注册教材（自有或公用，经 `resolve_textbook_file` 统一解析），未选中的资料中心文件/教材零可见。教材组前端展开为有序卷级 `file_ids`，后端仍存扁平 file_id。
- **工作区自有共享资料**：`POST /workspaces/{id}/upload` 落专属 Library folder，并把文件 ID 写入 `workspace_file_ids`；它不是全局教材，也不会自动进入其他工作区，但同区所有会话均可检索。旧工作区内嵌 knowledge_files 懒迁移后按该规则继续作为 workspace-owned source。

### 10.2 共享知识检索

- 工作区 `readable_stores()`（workspace-owned uploads + 选中的自有/公用教材）运行时拼装检索 overlay；该学习区下所有对话均可 `knowledge_search` 检索。
- `merged_knowledge_files()` 统一合并 session + workspace 文件元数据，确保 agent 管线（snapshot/planner/router/preamble）都知道共享资料存在。`turn_start` trace 带 `visible_file_ids`（P6-C1 诊断面）。
- `material_sources()` 生成稳定只读来源清单，标明 `source_scope=session/library/workspace/workspace_textbook` 与 visibility，供会话详情和右侧栏渲染，不返回原文。

### 10.3 公共记忆（public_memory）

- 跨对话结构化摘要（7 字段：学习领域/已讲概念/资料/错题/学生偏好/进展/待办），每轮对话后自动更新（LLM 摘要）。
- 注入为 L2 preamble 一部分，不占 session 上下文预算；窗口 = `SOFT_BUDGET_TOKENS × 4` 字符，超出自我压缩。
- 更新两路触发：轮次结束 + 文件上传；**重载合并防竞态**——LLM 摘要完成后重新加载 workspace 再合并写回，不用旧快照整文件覆写（消除「上传成功但对话读不到」的竞态）。
- 注入时声明优先级：与检索原文冲突时以检索原文为准。

### 10.4 对话绑定（双通道）

工作区内新对话自动绑定 `workspace_id`，从第一轮就有公共记忆与共享知识。两条首交互路径都覆盖：先发消息（`/chat/stream` 带 `workspace_id`）与先传附件（`/chat/upload` 同样接受，新建 session eager assign id 并即时绑定）——修复了「先传附件再发问」产生孤儿 session 的 bug。

### 10.5 对话内上传 vs 跨对话可见性（重要边界）

- **对话内上传**：仅属于该会话，不跨对话、不进资料库/工作区共享源；图片也按此规则保存原件+OCR+chunks。以附件形式显示在对应消息下方（元数据随用户消息持久化，resume 可见）；删除会话时级联删除提取文本/原件/向量。
- **引用资料中心**：对话输入框「引用资料」多选资料库文件，**复制**进当前会话供本轮起 RAG（`POST /chat/sessions/{sid}/attach_library`，`{file_ids}`；`sid=new` 先建会话，可带 `workspace_id`）；不改动资料库、不跨对话。新对话先暂存、首条消息发出时自动绑定。
- **跨对话可见的文件只有两类**：工作区直接上传的 workspace-owned 文件，以及工作区显式选中的教材。

### 10.6 共享文件管理

- 工作区展开区逐文件展示专属夹共享资料（类型图标/文件名/分段数 + 下载）；仅专属夹文件可在边栏物理删除。
- `DELETE /workspaces/{id}/files/{fid}` 三分支语义：专属夹文件删库 / 散选文件取消选入 / 他区夹内文件 400。
- 上传带进度、OCR 状态与逐文件失败反馈；workspace-owned 文件不依赖文件夹勾选，因此不存在“上传成功但未选中而不可检索”的状态。

### 10.7 API（11 端点）

`GET/POST /workspaces`、`GET/PATCH/DELETE /workspaces/{id}`（PATCH：改名 + 调整来源，`{name?, folder_ids?, file_ids?}`）、`POST/DELETE /workspaces/{id}/sessions[/{sid}]`（移入/移出对话，移入时自动从对话历史提取生成公共记忆）、`POST /workspaces/{id}/upload`、`DELETE /workspaces/{id}/files/{fid}`。

### 10.8 前端

左侧边栏两段（工作区文件夹树 + 会话列表，可折叠）；会话/工作区/详情以原子快照一次提交，动态路由切换复用模块级快照并去重并发请求，禁止先把工作区会话渲染为“其他对话”再迁移；创建/修改在**全局设置弹窗**（名称 + 教材来源勾选，全局唯一入口，教材组一次切换全部卷）；拖拽 session 入夹；资料中心页（§9.4）；对话内「引用资料」弹窗（pendingLibraryRefs 暂存，flush 双路径：stream/upload）。聊天右侧另有“当前资料”面板，分组展示工作区共享 / 本对话引用 / 本对话上传，桌面固定可折叠、窄屏抽屉。

---

## 11. M2 学生模型（Student Model）

回答「这个学生会什么」。包：`agents/student_model/`（profile / style_inference / evaluation）。旧 BKT 掌握度、概念状态记忆、事件黑盒与能力投影已随统一评价整体移除（历史迁移见 `scripts/migrate_learning_evidence.py`，plan §16）。

- **StudentProfile**：身份/学段/学科活动/可变偏好与自述目标的长期画像（无任何能力数值字段；weak/strong 与评价性 concept memory 已删除）。存 `students/<id>.json`。**学习风格写入路径（P0 激活）**：`student_model/style_inference.py` 是 `learning_style` 的唯一生产写入方——把 M8 已采集的反馈窗口（recent_feedback，近 12 条规则分类）折叠成风格翻转：「太长」≥2→basic、「太短」≥2→deep、矛盾不动、「太难」≥2→step_by_step。
- **统一学习证据 journal（唯一事实源，plan §6）**：`students/<id>.learning_evidence.jsonl` 每学生一个追加式事务日志——每行一个完整事务（`generation+seq` watermark、canonical checksum），操作是封闭判别联合（`question_registered / source_registered / assistance_recorded / job_* / result_committed / review_* / interpretation_revoked / scope_* / synthesis_committed`）。两类且仅两类学习观察来源：`dialogue`（自然对话/题卡）与 `assessment`（测评中心/CAT）；每份来源一份 `SourceReceipt`（真实 `observed_at`、当时 workspace 归属、帮助事件、`provenance=live|migration|demo_fixture`、答案指纹幂等）。
- **任务材料与冻结量规**：`TaskSnapshot`（题干/选项/答案/等价解/量规）在出题时注册，`question_revision + rubric_hash` 不变即冻结；量规指纹由服务端从冻结内容计算，改量规必须换 revision。
- **判分与语义评价分离**：MC 字母比对零 LLM 确定性判定，随受理事务落盘；开放题语义评价是异步 `EvaluationJob`（queued/running/retry_wait/succeeded/abstained/failed/cancelled，§10.3 优先级），P3 情景组合 + P4 六维观测输出经相同 validator 写回 `result_committed`——本题分数（TaskResult）与概念主张（ConceptJudgment，observed/not_observed 证据式）分开，unknown/indeterminate 不变成负分或假部分正确。同一来源版本只有一个当前有效解释；重复投递只有一次效果。
- **scope 与读侧投影**：评价只在学习区（workspace 绑定教材/选卷/概念 revision）内进行；`learner_views`/index 是可删除重建的投影（`projections.py`），消费者（M3/M5/M9/前端）全部读统一评价，不再各存一套掌握度。
- **持久化纪律**：短临界区按 journal key `file_lock`，锁内禁止 await；中部损坏 `journal_corrupt` 阻止受影响写入，不当空档案；永久删除走 generation 重写。

---

## 12. M3 自适应教学引擎（Teaching Engine）

回答「这个学生现在该怎么教」。包：`agents/teaching_engine/`。Import-clean 不变量：运行时零 `student_model` import，数据经只读 TeachingContext 注入。

- **教学模式状态机**：六模式 INTRODUCTION / EXPLANATION / REMEDIATION / PRACTICE / REVIEW / CHALLENGE，纯规则确定性。已有误解强制 REMEDIATION（纠错根因优先）；按统一评价投影的概念证据状态（observed/not_observed/冲突需复核）选基础模式；显式意图可钉死（review/practice）。
- **跨轮教学记忆（承重件）**：`students/<id>.teaching.json` 持久化每概念 (mode, outcome) 历史，支撑 INTRODUCTION→EXPLANATION→PRACTICE→CHALLENGE 跨轮推进。
- **per-mode 教学配方**：每模式挂 focus/avoid（INTRODUCTION「先讲直觉、禁公式堆积」；REMEDIATION「先定位错在哪一步、重建直觉模型、不只给答案」；CHALLENGE「综合题+迁移题」）。**INTRODUCTION 按学段三档分**（P1 细化，原为高中/本科一档 vs 小初一档）：本科=动机切入+严格定义/定理/证明思路+知识脉络与应用；高中=直觉例子切入+完整定义/公式/推导+轮廓与适用条件；小学=生活场景/实物/图画+小步即练+段末互动提问、禁长段抽象；初中保持通用配方。depth 在高中/本科 INTRODUCTION 不落 basic（避免「减少抽象推导」误伤高学段严谨讲解）。**学段细则单一事实源**：`teaching_engine/stage_profile.py`（四学段×七维度：语言/抽象/例题/结构/鼓励/难度锚点/典型错因），由 grade_preamble 整块注入讲解上下文（tutor_system@2.6.0 只留注入约定），并以难度锚点/例题风格/典型错因行注入出题、拟合、M4 出题与批改 prompt——替代原「{grade} 干标签靠模型自由发挥」。**讲解深度下限**（tutor_system@2.8.0）：「教学过程」由六步提纲式骨架扩为全链路要求（知识定位→直观动机→精确定义→分步推导→例题精讲→易错辨析→联系延伸→小结），并新增「深度下限」条款——默认讲解必须成篇讲透、禁止只给概要/提纲收尾，「输出纪律」明确"思考要短"只约束内部思考不约束最终回答；stage_profile 四档 abstraction/structure 同步操作化（本科档：定义条件逐条解读、证明给关键引理与核心步骤、反例构造、概念脉络联系）。
- **错误诊断（misconception）**：四分类（概念/步骤/计算/推理），批改反馈 note 规则诊断后存入学生记忆，下轮策略针对根因。
- **动态难度**：1-5 内部模型，最近 5 题准确率 ≥80% 升 / ≤40% 降（钳位），保持最近发展区；映射到 quiz 工具 easy/medium/hard，外部接口零变更。**信号闭环**：teaching_log 读写统一用图谱归一 key（`TeachingContext.concept_key`/`strategy.target_skill_id`，此前写读 key 不一致导致拨盘永久空转）；答题卡判分（`/quiz/*` 端点）经 `record_quiz_attempt` 注入 assessed outcomes，卡片作答真正驱动升/降档。**学段地板**：高中/本科新概念（无作答证据）从 medium 起步，小初仍 easy 起步；一旦有 assessed outcomes 拨盘全权（连错可降回 easy）。**情绪弱信号（P4）**：学生最新一条反馈被 M8 规则分类为「太难了/看不懂」时，supervisor 合成层把收尾检测建议难度降一档（hard→medium→easy 触底不降）——这是对 strategy 的输入叠加，不改 M3/M8 边界（M8 仍不触碰教学计划）。**出题侧**：generate_quiz prompt 内置三档难度可执行定义（easy 一步应用 / medium 一次转化 / hard 多步变式）+ 套内递进规则；策略注入的难度是建议下限，学生当轮明确要求更难/更简单时 executor 可覆盖 tool_args。
- **学习路径**：intent=规划时从图谱算「下一步学什么」（前置已满足、按难度排序）+「该复习什么」（掌握中等且久未触碰）。
- **刻意不做 LLM 教学规划器**（会让策略不稳定）。
- **LLM 教学决策适配（W3/D08，`TEACHING_DECISION_MODE=rules|shadow|active`，默认 rules）**：`decision_adapter.py` 在规则策略（select_strategy+compose，保留为可审计降级配方）之上加一次有界调用——触发门为「评估完成/学生新约束/连续困惑」，其余轮次零调用。输出必须过 `validate_decision`（action/target/assistance 枚举白名单、target 限候选概念集、`allow_followup_assessment=false` 禁 quiz、单一主要行动）才可生效；active 以**受限调整**落到 TeachingStrategy（mode/assistance/plan_hints/decision_id，不换 review_first、不发明工具），shadow 只记 trace 对照，rules 零改动；任何失败保持规则策略。TeachingStrategy 增 `assistance`（F02 帮助阶梯 full_demo|key_hints|independent）与 `decision_id` 审计字段。
- **课堂小结与恢复锚点（W3/D11，`session_summary.py`）**：每轮结束确定性重建 `session.learning_summary`（只引用 quiz_history 已接受判定；无测评→「已讲解，待验证」；含待答计数/开放问题/恢复锚点；active 且本轮有作答判定时经 `session_summary@1.0.0` 以骨架为唯一输入润色两行文案）。学生离开 ≥30 分钟后回来，supervisor 在 preamble 注入一行「[上次学习小结]…恢复点：…」（短间隔连续提问不打断）。

---

## 13. M4 智能测评（Assessment）

回答「学生真的学会了吗」。包：`agents/assessment/`（manager / adaptive_test）+ `student_model/evaluation`（受理、判分、语义作业）。所有作答经同一受理入口 `evaluate_submission`（§11.4 契约）：完整答案指纹判重（同题同答案重放同 attempt、同题不同答案 409）、MC 确定性判定随受理事务落盘、开放题语义作业进同一 job 体系；帮助事件在判分前入账，assistance_floor 决定该次表现能否计独立。

- **三级评分反馈**：`/quiz/grade` 输出 `[对]/[部分对]/[错]` 批改讲解；判定与题目局部分数（TaskResult）随来源落 journal，「部分对」是 M3 REMEDIATION 的根因信号、CAT 难度步进的输入。
- **量规冻结**：出题（三条生成路径共用 `quiz_verify.RUBRIC_REQUIREMENT` 契约）随题输出 `rubric_criteria[{id,description,weight,critical}]` + `equivalent_solutions`；题目通过校验、获得稳定题号后以 `rubric_hash` 冻结随题落盘（出题时学生未作答，天然满足「看学生作答前冻结」）。改量规必须新 `question_revision`，指纹由服务端复核。
- **提示与异议**：`GET /quiz/hint`（归属校验同 chat 路由）从冻结量规派生关键步骤提示（不含答案/判定，先答后揭晓不破），服务端在题快照落 `hint_requested`——判分自动携带 assistance 事件（帮助后表现不伪装独立）；`POST /quiz/dispute` 创建复核请求（ReviewRequest），复核走 `review_resolved` 生命周期，可撤销错误解释而不抹除原始证据；客户端自报帮助状态一律不采信。
- **跨会话最近习题**：`GET /quiz/recent` 是 journal 投影（替换旧 `.quiz_recent.json` 独立真相）；错题本同样从 journal 生成，可一键出变式重练（`origin_question_ref` 记录同族关系）。
- **CAT 自适应测试**：不固定题数，按作答动态调难度（1–5 内部档）。停止结论是 `stop_code` 枚举（`sufficient_for_current_claim|needs_clarification|max_questions|max_time|user_stopped|generation_failed`），是**诊断性**的（前端 SummaryCard 映射为中性表述，原始枚举不直达学生）。实例（题目引用/难度轨迹/停止状态）持久化于 journal `assessments`，不再有单槽 `assessment.json`；`next` 在当前题未答时幂等重发该题；`answer` 触发的停止随作答同一次落盘。并发防护：per-student 生命周期锁 + journal `file_lock`。题目载荷（start/next/active）剥离 answer/explanation——判分全在服务端。
- **API**：`POST /assessment/{start,answer,next,abandon}` + `GET /assessment/{report,active}`；`active` 三态（进行中当前题公开内容 / 终态 stop_reason+summary / 无会话 none），前端挂载即恢复。`AnswerRequest.raw_grade` 与 `StartRequest.mastery` 已删除——客户端无法注入批改或起点状态。
- **评价状态映射（§10.3/§11.5）**：作答回执 `evaluation.status ∈ ready|pending|unavailable|disabled`——语义解释就绪 ready；作业在途（queued/running/retry_wait）pending；硬故障 unavailable（不冒充 pending）；`next` 的 409 `evaluation_pending` 只表示判断仍在途；报告区分 pending 与题目局部结果（MC 确定性结果先行可见）。

---

## 14. M5 知识智能（Knowledge Intelligence）

回答「系统知道哪些知识、知识之间什么关系」。包：`agents/knowledge/`。

### 14.1 本体与单一真相源

- **KnowledgeGraph（承重件）**：节点 + 多类型边（PREREQUISITE/RELATED/APPLICATION/MISCONCEPTION/PART_OF）+ 每次写入做可达性环检测（一个环会腐蚀 M2/M3/M4 的学习顺序推理）。
- **P6-A2：考纲 seed 包已全部删除**——主图初始为空，知识只来自教材图谱（公用 + 自有）。M2 SkillGraph 的 legacy 21 节点自有种子保留为 M5 关闭时的回退（独立模块 `student_model/skill_graph_seed.py`，与考纲包无关）。
- M2 SkillGraph 降级为 M5 PREREQUISITE 边的 per-student 读投影；M5 关时回退自有种子，**永不并存**（避免双真相源）。
- SkillGraphBridge：M5 → SkillGraph plain-data 投影；`match_concept(level=...)` 学段感知（分数相近偏好学生学段节点）。

### 14.2 图谱来源（教材驱动）

考纲包、构建管线与校验器（seed_packs/validate.py、register_packs.py）已随 P6-A2 删除；`knowledge/graph.json` 的 reasoner 学习边已清空。图谱全部来自教材构建管线（§6.4b）：上传教材 → OCR/解析 → 章节切片 → 逐章抽取 → 确定性合并。**检索学段感知**不变（`retrieve/match_concept(level=)` 同窗段优先，未指定学段不偏好）。

### 14.3 检索与内容解析

- **ConceptRetriever**：BM25 over concept search_text（复用 `core/retriever`）+ KG 遍历扩展 + 分数融合。确定性、零 LLM。
- **ContentResolver**：概念 → 教学内容，级联永不阻塞：seed content → 上传材料 BM25 回退（复用 `knowledge_search` 同一库，把讲解锚定到学生实际教材）。
- **KnowledgeContextBuilder**：组装 `[知识智能·…]` 软指令块（概念定位/前置补缺/易错点/教学示例/教材引用/相关概念）。本体外概念返回空（M5 隐形不噪音）。前置补缺读统一评价投影——仅提示评价状态为 fragile/conflicting/emerging 概念的前置；未观察 ≠ 未掌握。
- `/knowledge/graph` 概念着色 = 统一评价投影（observed/not_observed/冲突）∪ 教学日志的并集。

### 14.4 Dependency Reasoner（M5 唯一用 LLM 的组件）

为未 seed 的新概念自动补 prerequisite 边：候选检索 → 规则过滤（剪枝跨学科/高难度）→ LLM 校验器（结构化 `{relation,confidence}`）→ 阈值门（≥0.65）→ DAG 安全写入（环检测）+ 持久化（`knowledge/graph.json`）。仅在扩展新节点，绝不在教学关键路径；坏输出无法腐蚀 DAG。

### 14.5 教材知识图谱（原自定义图谱，P6 重构）

知识谱系只来自教材（P6-A4 手动构建端点已移除；P6-A2 考纲 seed 已删）：

- **唯一性铁律**：每 (student, topic_key) 仅一个 active 图谱（`knowledge/custom/<student>/<topic_key>.json`，原子写）；教材重建直接质量校验后原子替换，不生成隐藏历史快照；删除教材先进入统一回收站 bundle，再移除活动图谱。运行时读路径零 LLM。
- **构建管线**：`textbook_builder`（§6.4b）——三级章节切片 + 骨架 + 逐章抽取 + `spec_to_graph` 确定性合并（id 命名空间 `custom.tb-<id>.*`、DAG 守卫、上限 30 章/400 概念、level 取上传所选学段）。
- **合并视图**：`graph_for/retriever_for(student_id)` = seed(空) ∪ learned ∪ **公用图谱** ∪ 该生教材图谱（双命名空间 mtime stamp 缓存，写后失效；stamp 与 listing 均忽略 `*.chunks.json`——chunks 是检索域数据，其重写不应使全量合并缓存失效）。`/knowledge/graph`、概念详情、supervisor 指令全走合并视图；图谱页 chips 按学段分组（小学/初中/高中/本科/其他，遗留「自定义」归入「其他」）。
- **概念预索引（P6-C2）**：每图谱随带 `<topic_key>.chunks.json`（概念→chunk_ids，按章节页码范围限定），`knowledge_search` 命中教材概念时优先章节内检索。

### 14.7 图谱遍历索引与冷合并契约（2026-08-23 性能修复）

公用教材图谱约 16K 节点 / 30K 边、25MB JSON，合并进每个学生的视图。曾经的冷合并让首个触达请求冻结整个服务数分钟（`/student/learning-path` 实测 434s，async `/orchestration/today` 在事件循环上同步冷构建冻结全部请求）。契约：

- **KnowledgeGraph 增量索引**：`edges` 列表仍是序列化真相源；`add_edge` 去重走 `_edge_keys` 集合（O(1)，不得回退线性扫描），`_reaches`/`prerequisites_of`/`descendants_of`/`neighborhood` 走 `_adj_out/_adj_in` 邻接索引（O(可达)，不得回退逐步全量扫边）。语义等价由 `tests/test_knowledge_graph_index.py` 用朴素参考实现钉死。
- **合并互斥**：`graph_for` 每学生一把构建锁 + 锁内双检；基础图懒初始化同样加锁。冷合并（解析+索引）实测 ~0.75s；进程启动 lifespan 用 daemon 线程预热默认学生模型，避免首请求承担。
- **执行位置纪律**：async 端点内的同步重活必须 `asyncio.to_thread`（M9 `today_tasks/_compose_today_safe` 全链路已迁移）；M9 读侧 helper 必须传真实 `student_id`，否则登录用户读到 guest 命名空间并额外冷构建一份。
- **`GET /orchestration/today` 确定性优先**：读路径缺当日任务时用确定性生成器即时返回；LLM 组合（`compose_llm=True`）只发生在显式写动作（POST /goal、/regenerate 后的 kickoff）。
- **SkillGraph 热路径缓存**：`prerequisites_of` 祖先 memo + `descendants_of` 反向邻接惰性缓存（prereq 构造后不可变）；M5 合并插入新节点后必须 `invalidate_traversal_cache()`。

### 14.6 个性化学习路径（/knowledge 底部条）

四级优先组装 `_personalized_next`（确定性、零 LLM）：① M9 本周计划未掌握概念 → ② 承接最近教学日志概念的可学后继 → ③ M9 目标学科内可学节点 → ④ 学生学段基础补全；每条带 `reason` 徽标（学习计划/承接「X」/目标学科/学段基础）。有作答记录的节点归入复习列表而非重复推荐。

---

## 15. M6 记忆智能（Memory Intelligence）

- **活动 prompt memory**：`students/<id>.prompt_memory.json`。只允许总体学习情况、当前水平、语气偏好、讲解偏好；普通对话与工作区对话按用户全局统一计数，默认最近 15 个会话、用户可选 5–30。
- **最近窗口与 core profile**：窗口内贡献按 `session_id` 独立可撤销；滑出窗口后合入一个不可按单会话拆分的 core profile，并执行整体 LLM 压缩与硬字符上限。
- **删除选择**：归档对话时可选“永久遗忘提示词影响”（默认不选）；窗口内 contribution 可立即永久移除，恢复对话也不重建。若归档时不选，归档中心永久删除或到期清扫仍会自动移除可单项归属的最近贡献；`compacted_session_ids` 仅保存会话身份归属，已压缩进整体画像的内容无法安全反向拆分，UI 明示不可单独撤销；旧版仅有压缩总数的数据标为 `legacy_unknown`，不伪造精确归属。
- **旧数据兼容**：`.episodes.jsonl/.episodes_archive.jsonl/.semantic.json` 继续只读展示和审计，但生产回合不再追加详细 episodic、不再运行 semantic consolidation，也不直接注入 prompt。
- **程序/习惯聚合**：策略成功率保存在 `students/<id>.procedural.json`，M9 习惯聚合保存在 `students/<id>.habit_patterns.json`；两者均为有界结构化状态，不保存对话正文或具体教学内容。首次读取允许从旧 `.semantic.json` 兼容投影，后续活动写入不再改写旧语义文件。
- **独立学习档案**：`students/<id>.learning_evidence.jsonl` 统一学习证据账本保存题目、作答、判分、语义解释与时间；与 prompt memory 分离，来源对话删除后证据保留（按删除语义归档）。
- **工作区共同记忆**：`Workspace.public_memory` 与用户级 prompt memory 隔离，仅同工作区会话读取；新会话边界整体压缩一次；单聊删除不回退；工作区 bundle 归档/恢复/永久删除。

## 16. M7 评估改进智能（Evaluation & Improvement）

回答「这个教师 Agent 自己是否越来越好」。包：`agents/evaluation/`。**不是自动自我修改的 Optimizer，而是 Improvement Advisor**：observe → diagnose → propose → approve → deploy，M7 只做前三步，approve/deploy 是人工确认（API）。

- **纯观察者（PURE-OBSERVER）**：只读 M2/M3/M6 投影，绝不反向写回；只拥有自己的评估产物。
- **与 M6 边界**：M6 procedural 记「策略 X 对该生 success_rate=Y」（per-student）；M7 strategy_analyzer 记「跨所有轮次哪种模式成功率最高」（系统级聚合，衡量的是教学系统质量而非学生能力）。不复制原始数据，只产出聚合层。
- **四层管线**：
  1. 观察层：TurnTrace 捕获（concept/mode/outcome/tools/tokens/steps/duration，无任何学生能力数值）。
  2. 诊断层：**Trace Analyzer**（承重件，零 LLM）——优先级瀑布诊断失败**发生地**，七类 FailureType：TEACHING_DEPTH_MISMATCH / PREREQUISITE_MISSING / RETRIEVAL_MISS / ASSESSMENT_TOO_HARD / STRATEGY_MISMATCH / NO_ASSESSMENT / NONE。
  3. 建议层：strategy_analyzer 跨轮聚合 (mode,subject) 成功率排序表；**advisor**（唯一 LLM 件，每 15 条 trace 频率门控，每次至多 1 条）产出**开放式教学指导提案** ImprovementProposal（title / applicability（适用范围文本，空=通用）/ guidance（指导原则文本）/ cautions / confidence——无值域、无参数赋值；解析失败静默丢弃），**不自动应用**。旧式 target 型提案（prompt/policy/strategy 白名单）仅存于历史数据，向后兼容读写。
  4. 部署层：人工批准（approved）→ **应用（applied）即部署**——提案的指导文本由 API 层写入 `teaching_engine/guidance_store`（M3 拥有的输入态文件 `students/<id>.teaching_guidance.json`；这是 M7 影响教学的**唯一路径**，也是唯一被允许的跨模块写点——M7 分析器代码从不 import M3）。后续每轮 `TeachingManager.adapt(student_id=…)` 读取生效条目并经 `compose` 折入 focus/avoid（确定性适用范围过滤：当前学科/概念出现在 applicability 文本中，或 applicability 为空=通用；每轮至多取最新 2 条、每行 110 字封顶），rationale 注明「已应用教学指导（提案 #id）」。指导只走 supervisor 已渲染字段（focus[:3]/avoid[:3]），对话链路零改动。
  5. （原 A/B 实验层已删除——experiment.py 为零调用死代码，见遗留物台账 C8。）
- **吊销即回滚**：`DELETE /evaluation/guidance/{id}` 将条目置 active=false（保留审计），compose 立即停止消费，教学行为恢复原状；重新应用同一提案幂等（applied_at 锚定首次应用时间，影响统计不重置）。
- **影响回显**：applied 提案带 `impact_turns` = applied_ts 之后的 eval_traces 条数（「已影响最近 N 轮」）；历史数据无 applied_ts 时为 null（显示「影响未知」）。
- **教学效果观测**：以每轮 outcome（correct/wrong/engaged/…）与工具/成本（tokens/steps/duration）为主的教学系统质量事实；旧 before/after 掌握度增量已随统一评价删除（学生能力观测只在 M2 学习证据域）。
- **持久化**：`students/<id>.eval_traces.jsonl`（黑盒）+ `students/<id>.evaluation.json`（聚合+proposals）+ `students/<id>.teaching_guidance.json`（M3 指导输入态）。
- **API**：`GET /evaluation/{report,traces,proposals,guidance,context-budget}` + `PATCH /evaluation/proposals/{id}`（`{status: approved|rejected|applied}` 人工确认门；applied=部署）+ `DELETE /evaluation/guidance/{id}`（吊销回滚）。

---

## 17. M8 交互体验智能（UX Intelligence）

回答「怎么表达最适合这个学生」。包：`agents/ux_intelligence/`。横向输出适配层：不改变教什么（M3 职责），只改变怎么表达。**M8 不用代码改写回答**，只产「如何表达」的软指令，LLM 在边界内自主表达。

- **单真相源红线**：M2 拥有学术讲解偏好（LearningStyle）；M8 的 UXProfile **只拥有 M2 没有的 UX 维度**——tone（语气）/ visual_preference（图示）/ pacing（节奏）/ patience（耐心）/ preferred_length（参与推断）。学术偏好只读投影，绝不重复拥有，绝不写学习证据/教学策略。
- **四层输出适配管线**（每轮关键路径零 LLM）：
  1. 信号层：FeedbackAnalyzer 规则分类反馈（too_hard/too_long/too_short/too_fast/too_slow/praise）。
  2. 推断层：EngagementTracker（长度窗口 + abandon 启发式）+ learner_profile（读 M2 只读）+ motivation_engine（读 M6 连续天数只读）。
  3. 适配层：interaction_style 把 UX 维度翻译成表达指令。
  4. 渲染层：ExplanationAdapter（教学计划 + UX 上下文 → ResponseDirective）+ ContextBuilder 渲染 `[交互智能·…]` 块。
- **M8↔M3 边界固化**：ExplanationAdapter 只读接收 TeachingPlan，产出 ExplanationDirective，绝不回写；数据流单向；模块级 import-clean，边界测试冻结不变量。
- **ResponseQualityEvaluator**：单轮表达效果评估（区别于 M7 跨轮聚合）——feedback 措辞 + 追问次数 + 回答长度 vs 耐受 + M4 verdict 只读 → communication_score(0-1) + ExpressionFailure（抽象过高/冗长/过简/节奏/语气）+ 调整建议。闭合回路：「还是不懂」→ 记 abstraction_too_high → 下轮提升 example_density。调整只落 UXProfile，绝不碰 TeachingPlan。
- **滞后效应（hysteresis）**：≥2 次信号才切换（防过度反应）；abandon 启发式刻意保守（仅明确抱怨才计）；同一里程碑只恭喜一次（`last_milestone_surfaced`）。
- **MotivationEngine**：连续学习天数 streak + 里程碑（读 M6 只读；长期记忆归 M6，M8 只拥有激励的表达方式）。
- **持久化**：`students/<id>.ux_profile.json`（工作集）+ `students/<id>.ux_events.jsonl`（黑盒）。
- **API**：`GET /ux/{profile,engagement,motivation,greeting}`（全只读；greeting 产出个性化开场：继续提示 + 连续天数，`?lang&grade`）。

---

## 18. M9 学习编排智能（Learning Orchestration）

回答「未来几周到几个月怎么持续成长」。包：`agents/learning_orchestration/`。纵向编排层：不直接写 M2/M3/M5/M6 存储，但通过 EventEmitter 事件流让 M6 决定是否固化长期记忆。

**计划层级（2026-08 重构，里程碑层与长期任务层已删除）**：`长期目标（多个，上限 4）→ 周计划（多周）→ 周任务（WeekTask）→ 子任务（SubTask）→ 今日任务（DailyTask）`。旧 `Milestone` 类与 `state.milestones` 字段仅为读取旧数据保留，不再产出。多目标：`state.goals`（每目标独立 id `g_{n}` 与差距分析 `goal_states` 1:1 配对），重规划把各目标 required_skills 按目标顺序合并去重后喂入同一份共享周计划；旧单目标 blob 在 `from_dict` 惰性迁移（`goal` → `[goal(id="g_1")]`，`long_term_tasks` 丢弃）。

- **GoalAnalyzer（目标推理）**：按目标逐个 gap 分析 + 倒推产出 GoalState（目标概念集/当前掌握差距/周数映射）。**W4/A13 语义**：无观测记录的概念 status=`unknown`（未测——不宣称缺口；legacy 行的 `missing` 原样往返），有证据未达标才是 `weak`（确认薄弱）；计划仍覆盖 unknown 概念（计划本就该教未测内容）。`estimate_schedule` 输出时间容量区间：周容量（daily_minutes×可用天）/每概念分钟折算第二个节奏，`est_weeks_min/max` 包夹惯例单点（单点与 fit 语义不变）。`summary()` 的 needs_replan 传本人 student_id（此前漏传，登录学生静默读游客档案——双学生回归钉死）。
- **LLM 周规划器（weekly_planner_llm）**：目标设定/编辑/重规划时一次 LLM 调用直接产出 N 周的语义化周计划——每周 focus + 行动级周任务（「学完浮力前两节」）+ 具体子任务（「做 10 道计算题」）。**大考纲窗口**：required_skills（多目标合并后）超百时只规划前 `num_weeks×5` 个概念的近端窗口，校验门（ids ⊆ 窗口、窗口全覆盖、非 review/summary 任务无重复概念、任务/子任务数上限、kind 合法）不过则回退确定性 `learning_planner` + `derive_tasks_fallback`（每周一个派生任务）。review/summary 任务允许复用先修概念（复习天性）。周起点按周一对齐取整（防亚秒漂移），id 固定 `wt_{week}_{seq}` / `st_{task}_{seq}`。
- **人工不可覆盖契约（全层级统一）**：`source="user"` 的周（`origin=user` 整周保留）、周任务、子任务，任何重规划管线不触碰；`_merge_user_plan` 按周窗口（7 天 bucket）把 auto 周内的 user 任务并回重建后的计划，week_index 按 week_start 重排。今日任务侧沿用 `custom=true` 不碰 + `materialize_day` gap-fill（任务唯一性铁律不变：落盘即身份稳定，绝不替换删除，未完成任务跨天结转置顶）。
- **周任务/子任务 CRUD + ✨推荐**：`user_wt_{week}_{seq}` / `{user|auto}_st_{task}_{seq}` id 前缀；`subtask_advisor` 单任务 LLM 拆解（2-4 个可执行子任务，gate 条数/标题/分钟；失败 → 502 前端静默提示）。子任务勾选完成驱动 WeekTask.effective_done。
- **LLM 每日编排器（daily_composer）**：候选池 = SRS 到期 ∪ 本周未掌握概念 ∪ **本周未完成子任务（行动级，带 week_task_id/subtask_id 引用）** ∪ M2 弱项 ∪ 昨日结转；LLM 挑 ≤ slots 个并产教练批注 reason；校验门（id ∈ 池、kind/phase 合法、去重、≤ slots）失败回退确定性路径。子任务物化为带引用的 DailyTask。
- **完成回写**：完成带子任务引用的今日任务 → 对应 SubTask 置 done。**标题守卫**：位置 id 会被重规划复用于新内容，只有标题仍匹配才记功（防旧任务给新内容刷完成）。**W4/A12 完成语义**：曝光轮（6g）只把概念匹配的今日任务 pending→in_progress；完成只有两条路——①任务自身绑定证据（launch 会话内已受理作答，`completion_source=quiz_evidence` + `evidence_attempt_id`，任意判定=已做该任务，达标另判），②显式勾选（`completion_source=self_report`，零 M2 写入）。无绑定会话的作答**不再**按概念名自动完成今日任务（旧「任意同概念判定完成全部匹配任务」正是 A12 缺陷：同概念多任务串联完成、补练无法归属）。
- **任务启动绑定（W4/A12）**：`POST /orchestration/task/{id}/launch`（随本路由族单数命名；纲领 §9.2 的复数拼写为设计目标名）服务端校验归属后预创建携带 `task_binding{task_id, episode_id, concept_id}` 的聊天会话并返回 `{episode_id, session_id, launch_url}`；未完成任务的 relaunch 幂等复用同 episode/会话。episode 存 `students/<id>.learning_episodes.json`（`core/learning_episodes.py`，§8.5 许可的可重建投影，最小生命周期 active→completed/abandoned + revision）。前端 TodayCard/kickoff CTA 先 launch 再跳 `launch_url?q=首选消息&send=1`（沿用 kind 感知首发文案），launch 失败回退纯文本深链（§9.4 普通深链仍支持）。判分端点（/quiz 两处）把会话的 task_binding 传给 `record_quiz_evidence`：SRS 复习键用任务的规范 concept_id（消除聊天概念串/图谱节点 id/note: 三键碎片化在该路径的影响），作答只完成绑定任务；任务完成时 episode 置 completed。
- **作答证据直供（W4/A08·M9 切片 → update_plan R19 统一 outbox）**：判分全部发生在聊天轮外（/quiz/record、/quiz/grade、/assessment/answer）。M9 的 verdict 消费不再依赖当轮窥探或端点内直接 fan-out：评价作业在 journal 内提交 `OpResultCommitted` 的 outbox 事件（`consumer="m9"`，携带 attempt_id/session/workspace/帮助/审核条件），由评价 worker（`evaluation/worker.py::_advance_outbox`）在作业终态后调用 `consume_evaluation_outbox` 幂等消费（journal `consumer_ack` + M9 侧 attempt_id 去重双层）；`core/quiz_attempts` 侧的直接喂入仅作受理路径兜底。只有已审核（verification=passed）且非全演示帮助的结果才算独立召回、延长 SM-2 间隔（未审核/揭晓后作答建卡不进通过路径）；复核/撤销事件重放受影响复习卡。M2 事件补携带 session_id。M7/M6/M8 的同款窥探消费属 W6。
- **评价调度与每日批次（update_plan 阶段B/C）**：受理/对话 hook 只负责可靠受理（202 + MC 判分即时落盘）；语义解释由 lifespan 启动的评价 worker 统一执行（按 JobKind 路由、重启恢复、wall-clock 预算、同 workspace 串行）。管理员可经 `/admin/learner-evaluation-policy` 选择即时（默认）或每日零点（业务 IANA 时区，如 Asia/Singapore；`chat_history/settings/learner_evaluation_policy.json` 持久化、expected_revision CAS）；每日模式下受理时冻结 `eligible_after_utc`/本地活动日，DailyPlanner 在本地零点后关闭上一自然日窗口（`OpDailyBatchClosed`，全部候选有最终处置才关账，无候选评价记 no_observation），漏执行日期重启后补关；切回即时立即释放入队。方案 2 开放题另有 task_only 立时反馈作业（不发布 learner 判断，零点再解释）——开放题可能增加一次语义调用，成本变化记录于配置说明。
- **SM-2 间隔复习（承重件）**：经典 SM-2；`quality_from_verdict()` 是 M4→M9 组合点（correct→5 / partial→3 / wrong→1；**unknown 及无法识别的判定返回 None**——W1/A07：无有效召回证据就没有 SM-2 观察；exposure（无判定讲解轮）与 unknown 只建卡安排首次检查，不进通过路径，复习间隔不再被「只听讲」拉长——W4 起 recall 质量只经 `record_quiz_evidence` 进入，`record_turn` 纯曝光）；`submit_review` 自评反馈可调日程但事件带 `source: "self_report"` 与作答证据分开标记；新建卡 `last_quality=None`（从未召回的卡不再伪装成 pass-3，旧文件历史值保留）；SRS 与统一学习评价正交。前端并入今日任务「间隔复习」子栏。
- **容量可行性（W4）**：`schedule_engine.capacity_report` 纯函数按日汇总 estimate_minutes vs daily_minutes 标记超载日（此前任何路径都无分钟级校验：add_task 只限数量、weekly 解析只夹单任务分钟、改预算不复查存量）。`/plan` 附 capacity、`POST /task` 与 `PATCH /task` 附当日 `capacity_warning`、`PATCH /schedule` 返回重算报告——全部 advisory 不阻断（§11.1 学生可自主超排）。今日卡超载时显示警示条。
- **进度预测（原成长模拟）**：确定性前向投影 + `headline()` 人话结论（零 LLM 模板：节奏 → 到期完成率 → 风险行动）。**前端已下线该卡片**：抽象投影数字对学生不可行动（真实用户反馈），`GET /orchestration/simulation` 端点保留可用；前端同样移除了手动「重新规划」按钮（确定性重算后页面无可见变化，等同死按钮）——needs_replan banner 改为「让教练帮我调整」对话深链，目标编辑保存仍是自动重规划的入口。
- **防死循环双守卫**（不变）：无目标永不提示；`last_plan_attempt` 区分「从未规划」与「规划了但无可安排内容」——empty_plan 是合法终态。
- **EventEmitter**：连击/进度事件白名单 + 去重，由 6g 转发给 M6 consume_turn。
- **持久化**：`students/<id>.orchestration.json`（工作集）+ `students/<id>.orchestration_events.jsonl`（黑盒）+ `students/<id>.learning_episodes.json`（W4 launch 绑定投影，账号清除与孤儿扫描按 students/ 前缀通用清扫覆盖，测试钉死）。
- **API**：`GET /orchestration/{plan,today,habit,review,simulation}` + `POST /orchestration/{goal,regenerate,task,task/{id}/complete,task/{id}/launch,week,week/{i}/concept,week/{i}/task,week/{i}/task/{tid}/subtask,week/{i}/task/{tid}/suggest}` + `PATCH /orchestration/{goal/{goal_id},task/{id},week/{i}/task/{tid}/subtask/{sid},schedule}` + `DELETE /orchestration/{goal/{goal_id},task/{id},week/{i},week/{i}/concept/{cid},week/{i}/task/{tid},week/{i}/task/{tid}/subtask/{sid}}`。`POST /goal` 为**追加**一个目标（上限 4，超出 400），`PATCH/DELETE /goal/{goal_id}` 按目标 id 寻址（未知 id 404），三者尾部自动重规划；响应带 `weeks` + `first_task`（kickoff CTA）；`POST /regenerate` 响应带 `reason`（`"" | "no_goal" | "empty_plan"`）。W4 附载：`/plan` 带 `capacity`（needs_replan 同）；`POST /task`/`PATCH /task` 带当日 `capacity_warning`（可选）；`PATCH /schedule` 带 `capacity`；`POST /task/{id}/launch` 返回 `{episode_id, session_id, launch_url, resumed}`（404=不存在/已完成/非本人）。

---

## 19. M10 学习能力运行时与证据门（Skill Runtime）

M10 是横向能力控制层，包路径 `agents/skill_runtime/`。它不替代 M1-M9，也不改变前端页面与 SSE 契约；职责是把分散的工具/教学能力收敛为有版本、前置条件、成功标准和可解释决策的 Agent Skill。知识图谱里的「学生要掌握的 Skill」继续由 M2/M5 管理，运行时能力统一使用 `agent.skill.*` 命名空间，二者不得混用。

### 19.1 Skill Manifest 与 Registry

- `manifest.py`：`SkillManifest` 声明 id/version/kind/role/tool_name/intents/preconditions/postconditions/use_when/avoid_when/fallback/side_effects/risk/cost；启动注册时执行确定性校验，Atomic Skill 必须绑定工具。
- `registry.py`：Skill 单一真相源；当前内置 5 项：分层讲解、资料证据检索、结构化练习生成、参考题变式生成、历史学习证据回忆。旧 `knowledge/teaching/assessment/memory` 四角色仍保留为兼容投影，`router.CAPABILITIES` 由 Registry 生成，不再单独维护第二份工具表。
- `PlanStep.skill_ids`：计划开始携带可执行 Skill ID；`suggested_tools` 暂时保留以读取旧会话/旧计划。Router 优先使用 `skill_ids`，未知 ID 不进入可见工具集。`tool_args/auto_invoke` 是可选的确定性履约提示，只用于系统已能完整确定参数的必执行步骤，不把所有计划工具都强制化。

### 19.2 决策与 gated 硬门

每轮在 understanding + StudentSnapshot 后构建 `TaskFrame`，字段含 intent/subject/concept/grade/资料/历史/参考题/资料引用/工具需求/置信度。`decision.py` 对 Registry 候选执行规则评分，`policy.py` 确定性检查前置条件；Trace 写入：

- `skill_task_frame`：本轮可审计任务条件；
- `skill_decision`：mode（direct/execute/clarify）、候选分数、选中 Skill、理由码、失败前置条件；
- `turn_start.skill_versions`：本轮所有 active Skill 版本。

`SKILL_RUNTIME_MODE=shadow` 是裸 uvicorn 的保守旁路模式，只记录决策、不替换现有 Planner/ReAct 行为；`start.sh` 与 `.env.example` 默认使用 `gated` 完整路径。`gated` 在 Planner 后执行 `gate_plan`：没有资料时移除检索、没有历史时移除回忆、普通练习只保留 generate、完整参考题变式只保留 fit、缺参考题则生成一个只询问关键输入的 clarification plan；`off` 关闭旁路决策和动态 Skill Card，但 Registry 仍作为 Router 的能力真相源。

### 19.3 动态 Skill Prompt

`prompts/tutor.py::skill_cards_preamble` 只把当前 TaskPlan 引用的 Skill Card 注入上下文，不把完整 Registry 塞给模型。卡片包含 Skill id/version、用途、前置条件和成功标准；Tutor Prompt 改为通用 Skill 决策边界，Planner Prompt 要求 `skill_id` 必须来自当前能力列表且禁止编造。`tutor_system@2.5.2` 在 2.4.0 基础上：「必要的简短讲解」改为「先把知识点完整讲透再调工具」（切断收尾检测对讲解篇幅的压缩连锁）；有答题卡时正文禁写文字自测题（一题不二出）；新增数学环境中文 `\text{}` 规则。保留策略驱动收尾检测、工具调用前后呈现边界、输出预算纪律，以及学生显式格式约束高于默认教学展开的规则；`understand_system@1.1.0` 输出 response_format/allow_followup_assessment，其余版本为 `planner_system@1.1.0`、`skill_decision_system@1.0.0`。

### 19.4 Runtime 后置条件

`runtime.py::SkillRuntime` 将 ToolResult 映射回 Skill，并对可确定性检查的后置条件执行验证，例如资料结果必须包含命中数与 `<material_excerpt>`、出题结果必须包含 questions、历史回忆必须有 `<history_excerpt>`。验证结果写入 `skill_postconditions` Trace。gated 模式下，只有非 error 且后置条件通过才发出 `skill_plan_advance`，Executor 重算下一 PlanStep 的可见工具；没有工具绑定的 advisory teaching step 不再阻塞后续 assessment step，而是记录 `skill_plan_advisory` 后跳到下一个工具步骤。失败留在当前步骤，由既有恢复提示和 `MAX_STEPS` 控制。

### 19.5 学习证据纪律（统一评价域）

旧 E0–E5 六级证据门（`evidence.py`）已随统一学习评价删除。现行证据纪律在 M2 评价域内统一执行（§11/§13）：学习观察只有 dialogue/assessment 两类来源、必须携带真实 `observed_at` 与当时 workspace 归属；题目在学生作答前冻结量规；帮助事件在判分前入账，assistance 后的表现不伪装独立；unknown/空作答不产生判定（不落任何评价写入）；明确判错是有效的负向观察，以语义判断（ConceptJudgment）而非数值掌握度落账。M10 自身的门是 `gate_plan`（计划级工具门）与后置条件校验，不再有第二套学习证据分级。

### 19.6 兼容边界

- 前端「纸墨书院」视觉、页面结构、SSE 事件和现有四工具名称不变。
- M10 不引入多 Agent 进程，不把 M2-M9 强行改造成互相对话的子 Agent。
- 所有 Skill 卡片、Trace 与 Manifest 禁止包含 API key/JWT secret；工具仍由会话/工作区作用域构造，资料权限边界不变。
- `gated` 已具备逐步骤工具暴露、缺输入澄清和 M4 写回证据门；原始 uvicorn 配置仍可用 `shadow` 做旁路审计，而 `start.sh` 与当前 `.env.example` 默认启动 `gated` 完整运行时。回滚时可显式设置 `SKILL_RUNTIME_MODE=shadow`。

---

## 20. 前端架构

### 20.1 技术栈与目录

Next.js 16（App Router, Turbopack）+ React + TypeScript + Tailwind CSS v4 + Zustand + lucide-react。

```
frontend/src/
├── app/                 # layout / globals.css(设计令牌) / login / register
│   └── (workspace)/     # 路由组：chat / dashboard / knowledge / plan /
│                        #   orchestration / assessment / memory / resources /
│                        #   insights / profile
├── components/
│   ├── ui/              # 设计系统原语（Card/Button/Badge/Modal/EmptyState…）
│   ├── charts/          # SVG 图表原语
│   ├── shell/           # AppShell / SideNav / TopBar
│   ├── sidebar/         # 工作区+会话边栏
│   ├── chat/            # 对话工作台组件群
│   ├── workspace/       # 工作区设置弹窗等
│   └── pages/           # 各模块页组装
└── lib/                 # api(SSE) / api-modules(投影客户端) / store(zustand) /
                         #   types / i18n(-page) / labels / format / nav
```

### 20.2 「纸墨书院」设计体系

- 三层令牌：基础色板 → 语义令牌（CSS 变量，`globals.css` + `@theme inline`）→ 组件类。宣纸底 / 黛青 / 朱砂主色，浅深双主题（`data-theme` 切换，偏好持久化 localStorage）。
- 证据四态色（未观察/初现/有支持/需复核）贯穿图谱、总览、测评，颜色语义与统一评价投影一致。
- `font-serif` 标题气质；字号 S/M/L/XL 四档（`--fs-scale` 驱动根字号，默认 M）；`page-in` 页面入场动效。
- 页面三态规范：加载骨架 / 空态（引导行动）/ 数据态；卡片化 + 徽标 + 克制留白。

### 20.3 十大模块页

| 路由 | 页面 | 体现模块 |
|------|------|---------|
| `/chat/[[...sessionId]]` | 对话工作台 | M1 + 工具卡片 + 文件/图片上传 + 引用资料 + 语音通话（沉浸式电话模式） + 当前资料右侧栏 |
| `/dashboard` | 学习总览 | M2（学习近况/证据投影）+ M9 今日任务联动卡（最近学习/需要关注均分页）|
| `/knowledge` | 知识图谱 | M5（分层浏览/自定义图谱/个性化路径）|
| `/plan` | 学习计划 | M3（教学日志/学习路径/动态难度，全列表分页）|
| `/orchestration` | 学习编排 | M9（多目标卡+差距分析/周 tabs+周任务子任务✨拆解/今日任务双子栏+间隔复习/全列表分页+数字跳页/needs_replan 教练深链/习惯）|
| `/assessment` | 测评中心 | M4（CAT 自适应测试/报告/错题本+重练深链/最近习题 100 道上限+近期会话，均分页）|
| `/memory` | 记忆中心 | M6（情景时间线按日分页/语义事实与策略条形分页）|
| `/resources/files` `/resources/textbooks`（`/resources` 重定向落点） | 资料中心 | 资料库（教材库/文件库双 Tab 路由段化：教材卡片含构建状态/进度/详情抽屉章节大纲且懒加载分包，文件库=文件夹树/下载/来源选择/会话附件）|
| `/insights` | 系统洞察 | M7（失败分布/策略排名/改进建议人工确认）+ 上下文/推理预算观测 |
| `/profile` | 我的画像 | M2 学术画像 + M8 UX 画像 + M0 账户卡与注销 |

外加 `/login`、`/register` 认证页（M0）。

### 20.4 关键机制

- **AppShell**：SideNav（工作区树 + 会话列表 + 批量管理）+ TopBar（设置齿轮：中英双语 / 回答语言 auto-zh-en / 主题 / 学段 / 字号；模型信息；登录入口）。左侧模块导航每次进入站点默认收起为图标轨（开合不持久化，仅会话内手动展开）。
- **状态**：zustand 双 store（chat-store / auth-store）+ SSR 水合安全；localStorage 持久化偏好与 token。
- **i18n 双层**：`i18n.ts` 全局词条 + `strings.ts` `makePageT` 页面级词条，zh/en 全覆盖。
- **列表分页约定**：所有条目列表统一走 `ui/Pager`（客户端切片 `paged()`，默认 5 条/页，密行/表格 8-10 条/页）；页码为可输入框，直接输数字跳页（Enter/失焦提交、自动钳位、Esc 取消）；回源后条数变少时组件先钳位页码再切片，不停留在空白页。情景记忆时间线按日分组、一页一天，「加载更多」继续向服务器取更早分组。
- **API 层**：`apiFetch`（JWT 注入、REST + SSE 统一）、`uploadFailures`（上传失败明确提示）、`downloadViaFetch`（原件下载保留文件名）。
- **chat URL 唯一事实源**：`/chat/[[...sessionId]]` catch-all，世代号防串会话（切换会话时丢弃迟到的上一会话流）。
- **流式渲染**：§5.3（本地累积 + 50ms 节流 + React.memo + pinned 滚动）。
- **资料右侧栏**：桌面固定/可折叠、窄屏抽屉；按“工作区公共资料 / 本对话引用教材 / 本对话上传文件”分组，仅消费后端 `material_sources`/workspace detail 的结构化元数据。`knowledge_search` 工具卡片先渲染结构化命中来源（文件、页码/slide、章节、相关度），再展示片段，不从模型文本猜来源。
- **语音通话（沉浸式电话模式，P10）**：chat 页唯一的语音入口是右上角电话按钮（`GET /voice/status` 决定显隐，通话中隐藏、由同角落的手机模拟接管状态）；通话**不弹对话卡**——页面照常显示，语音轮次实时写入消息流（转写即用户消息、`answer_delta` 节流进 `pendingAnswer`、`turn_end` 落定，与文字轮共用 `StreamingMessage` 渲染路径与会话世代守卫）。通话期间：页面中上部浮出虚化「板书」黑板，严格左右居中（max-w 760px，接近下方输入栏宽度）、固定占据 3/7 页面高度；板面**三块严格等分**（`flex-1 min-h-0` + 各自滚动，内容多的一块不会挤占邻板），只记**块状公式段**（`$$…$$` 段本身，行内 `$…$` 不上板也不触发换板），且**与实际播放同步**——句子音频从播放队列出队开始发声那一刻才挂板（`tts_start` 提前到达只是入队登记，语音没播到的公式不会提前出现），块式段从上往下写空板，三块都满时擦掉写得最早的那块再写新的。右上角（原电话入口位置、按钮行正下方）挂一台「手机模拟」（约 6.72rem × 14.4rem，等比 0.8×：虚拟空白头像 + 通话计时 + 声波状态，底部返回/主页/多任务为纯装饰图标，无真实功能；窄屏隐藏；黑板严格居中不预留，窄窗口下手机可能贴住板书右缘——已确认接受）。底部控制条（按住说话/停止播报/挂断）紧贴输入框上方（输入框保持可用，草稿保留；控制条下方不再常驻浏览器识别/隐私说明文案，未支持浏览器的错误码自带打字回退提示）。表格不逐格朗读：口播一句「请看这个表格」，整块 markdown 经 `board_table` 事件**即时完整**呈现（无书写动画）并整版驻留至少 7 秒，之后继续驻留，直到下一个新公式/新表格需要板面才被清空，语音流水线不停顿。原浏览器 Web Speech 单句听写已随本改版移除。
- **图谱页交互**：学段 × 学科 chips 两级筛选；章节总览 → 点击下钻章内 DAG，面包屑返回；搜索命中自动定位；pointerup 命中测试（规避 setPointerCapture 吞 click）。概念抽屉双 CTA（「在对话中学这个」/「出几道题考我」）与个性化路径点击均经深链直达对话。
- **对话深链契约（`?q=&send=1`）**：`/chat?q=<问题>` 预填输入框（不发送）；追加 `&send=1` 则进入新会话时自动发送该问题（ref 防重 + `history.replaceState` 清参数防刷新重发，StrictMode 安全）。知识图谱节点/推荐路径、编排任务行动按钮、本周复盘全部经此契约跳转，消息携带概念上下文（名称/学科/难度/掌握状态/推荐理由）。
- **加载性能架构**：`start.sh` 默认 `FRONTEND_MODE=prod`（按需 `next build --webpack` → `next start`；源码/后端端口变化自动重建，`REBUILD=1` 强制，`./start.sh dev` 子命令显式回热重载）——dev 服务器按路由现场编译、无 Link 预取，是本地“首开卡顿”的主因。路由级分包：教材库等重页面 `next/dynamic` 懒加载 + `(workspace)/loading.tsx` 统一 PageSkeleton。鉴权水合并行（`/auth/status` 与 `/auth/me` 并发，authRequired 结果 sessionStorage 缓存 5 分钟；`statusLoaded` 与 token 校验双双落定才渲染，未登录不闪屏）。数据层（P0-P4）：`GET /sidebar` 组合快照（会话+工作区+详情一次取齐，ETag/304；替代侧边栏三级 N+1 瀑布）；`GET /chat/sessions/{id}?tail=N` 渐进加载（首屏最近 40 条 + “加载更早”按钮，`.msg-cv` content-visibility 跳过视口外渲染）；教材库空闲零轮询（building 2s / ocr_waiting 15s 条件轮询 + 焦点/WS_CHANGED_EVENT 驱动刷新）；`apiFetch` 幂等 GET 30s 超时护栏（SSE/上传不受影响）；后端每个响应带 `X-Process-Time` 头 + >1s 终端告警。知识图谱冷构建索引化（§14.7）：公共教材图谱合并（~16K 节点/30K 边）从 O(E²) 去重 + 全量扫边改为 O(1)/邻接索引，冷合并分钟级 → ~0.75s；`graph_for` 每学生构建锁 + 启动后台预热默认学生模型；async 读端点的同步重活一律 `asyncio.to_thread`；`GET /orchestration/today` 确定性优先（LLM 组合只在显式写动作）。

---

## 21. 工业化与运维

### 21.1 可靠性基建

- **原子写 + 文件锁**（`core/atomic.py`）：所有 JSON 持久化（session/workspace/library/students/*）tmp+replace，防半截写入；腐坏文件按空处理不崩。
- **路径防护**：`_resolve` 统一剥目录，防路径遍历（session/library/memory/knowledge store 同规则）。
- **限流**（`core/ratelimit.py`）：登录 10/min、OCR 20/min 等。
- **CORS**：`CORS_ORIGINS` 白名单（生产禁 `*`）；开发默认覆盖 start.sh 的 3000/3001/3030 候选，`start.sh all` 会先选前端端口，再把实际 localhost/127.0.0.1/0.0.0.0 Origin 注入后端，避免端口回退后登录预检 400。
- **直连网络**：主 LLM、Embedding、视觉模型的 httpx client 固定 `trust_env=False`；`start.sh` 同时清除 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY` 大小写变量，部署不会误走本机代理。
- **鉴权矩阵**：JWT 守卫 fail-closed；按 id 资源端点对外人 404 不泄露存在性。
- **密钥与仓库卫生**：`.env`/`*.key`/`users/`/`students/`、私有 `chat_history/`、用户图谱、向量库和上传目录均 `.gitignore`；固定 `public` 命名空间的公共教材元数据/原件/解析文本及 `knowledge/custom/public/` 图谱是版本化项目资产。`GET /api/v1/model-info` 只返回模型名 + 多模态配置 bool，绝不返回 key。
- **LLM 结构化小调用防饿死**：`llm_async.complete(disable_thinking=True)` 经 `extra_body` 关闭推理模型（deepseek-v4/R1 类）的思考链——否则 max_tokens 预算被 reasoning_content 吃光、content 恒为空，JSON 抽取类调用（M9 里程碑/每日编排、M6 巩固、出题/拟合出题、主观题评分、任务理解、规划器）静默全灭、永远走确定性回退（任务理解回退曾把整句用户消息当 concept 写入记忆/评估层）。provider 不支持该字段（400）时自动去参重试一次，跨 provider 可移植。新增结构化 `complete` 调用点一律带上。

### 21.2 Prompt 工程

`prompts/tutor.py` 四层控制：不可压缩红线 → 教学过程/学段适配 → 回合动态上下文 → 当前计划 Skill Card；工具规则由 M10 Manifest 与动态卡片承接，系统 Prompt 只保留通用调用边界。Prompt 注册表版本化（新增 `quiz_intent_system@1.0.0`，与 `understand_system@1.3.0` 同一次调用识别模糊新题请求），注入防护定界标签 + redline_tail（P2 归位：不再由 build_context 代压，改在 executor plan recap 之后/legacy 调用点压真正尾部，恢复 recency 设计意图）；多层 `[xx智能]` 软指令头部压显式仲裁序（显式约束 > 红线 > 教学策略 > 表达适配），策略 deep vs UX concise 冲突确定性收敛；M7 advisor prompt 中文化与全库一致；`prompt_eval` 25 金标回归。

### 21.3 Trace 可观测

每轮 trace 落盘 `traces/`（`TRACE_DIR`，默认锚定 `<root>/backend/traces`，cwd 无关）：决策链（understanding/TaskFrame/Skill 候选与拒绝原因/plan/tool 调用/Skill 后置条件）+ prompt/skill 版本 + token 用量。`GET /trace/{run_id}`（JSON）+ `GET /trace/{run_id}/html`（可折叠视图）。

### 21.4 部署三形态（`NEXT_PUBLIC_BACKEND_URL` 单一真相源）

1. **开发/跨域生产**：`NEXT_PUBLIC_BACKEND_URL=http(s)://<host>:<port>` 构建 → 客户端直连（需 CORS 放行）。
2. **同源生产**：不设该变量 → 相对路径 `/api/v1`，nginx 反代 `/api/*` 到后端（SSE 需 `proxy_buffering off`）。模板见 `deploy/`。
3. **本地一键**：`./start.sh` 先探测后端/前端实际端口，再同步 `NEXT_PUBLIC_BACKEND_URL` 与本地 `CORS_ORIGINS`；子进程统一直连网络，不继承 shell 代理。前端默认生产模式（构建时烤入后端端口并记录于 `.next/edu-build-port`，端口漂移自动重建）。

生产清单（`.env.example` 尾部）：`AUTH_MODE=1` + 替换 `AUTH_JWT_SECRET` + `CORS_ORIGINS` 白名单 + `chmod 600 .env`。Python 生产依赖采用 `backend/requirements.txt` + `backend/constraints.txt` 约束；BM25 基础环境不安装可选向量/本地模型 requirements。**同机生产部署手册**（固定域名/端口、干净克隆、systemd、ACME/HTTPS、同机回归、备份与回滚）见 [`docs/The_Website_deployment_plan.md`](The_Website_deployment_plan.md)。
生产 systemd 与 Paper Agent 使用同一账号模型：专用系统用户/组 `edu-agent`，home `/var/lib/edu-agent`，shell `/usr/sbin/nologin`；源码位于 `/opt/edu-agent`。后端和前端 unit 均启用 `NoNewPrivileges`、`PrivateTmp`、`ProtectSystem=strict`、`ProtectHome=true`，只通过 `ReadWritePaths` 放行 §22 的运行存储根、前端 `.next` 与 `/var/lib/edu-agent`。`/opt/edu-agent` 可读不代表私人数据可读：`.env`/Deploy Key 为 0600，运行数据目录为 0700。

### 21.6 OpenAI 兼容门面（第三方平台接入）

`api/v1/compat.py`：把本 Agent 暴露成标准 OpenAI 兼容服务（契约遵循 `Paper_Agent/openai-compatible-agent-integration-guide.md`），供清小搭广场类平台「标准协议接入」零适配挂载。

- **端点**：`GET /api/v1/models`（连通+凭证校验）、`POST /api/v1/chat/completions`（SSE 流式 + 非流式 JSON）。接入侧 baseUrl 填 `https://<域名>/api/v1`。
- **鉴权**：`COMPAT_API_KEY`（Bearer 或 `x-api-key` 头），未配置 = 门面关闭（503），无效 = 401；限流 30 次/分。
- **会话映射**：接入方每轮携带完整 messages，门面只取最后一条 user 消息作为新输入，历史由按凭证哈希派生的固定会话（`compat_<sha256[:12]>`）自持；student 命名空间固定 `compat_agent`，与真人学生数据物理隔离、不出现在任何用户会话列表。
- **帧序契约**：role 帧（恰好一次）→ `delta.reasoning`（L1 思考，只出不入）/`delta.content` 增量 → stop 帧合并 usage → `data: [DONE]`；流式中途出错发 stop 帧 + `error` 字段（finish_reason 严守官方白名单，无 error 值）。
- **探测快道**：`max_tokens<=2` 的最小对话不启动 Agent，直接返回合法结构（一轮完整教学要多次 LLM 调用，探测会超时）。

### 21.5 运维注意

- **改 agent 管线代码后必须重启 uvicorn**（无热重载假设）。
- `start.sh` 默认 `SUPERVISOR_MODE=v2`；排障可 `SUPERVISOR_MODE=legacy ./start.sh` 对比。
- 各层开关（§1.4）可用于二分定位故障层。

---

## 22. 存储布局总表

| 路径 | 内容 | 隔离粒度 |
|------|------|---------|
| `chat_history/<id>.session.json` | 会话工作集（含 supervisor_state/compaction/context_card/output_language） | 会话（带 student_id 戳） |
| `chat_history/<id>.transcript.jsonl` | 会话全量黑匣（append-only） | 会话 |
| `chat_history/library/<sid>.json` + `data/<sid>/` | 资料库元数据 + 解析文本 + `.orig<ext>` 原件 | 账号 |
| `chat_history/library/<sid>.textbooks.json`（P2 新增） | 教材注册记录（状态机/进度/章节概念数/warnings） | 账号 |
| `chat_history/workspaces/ws_<ts>_<slug>.json`（+ `uploads/` 共享资料上传目录） | 工作区（含 public_memory/selected_*），每区一文件 | 账号 |
| `chat_history/trash/items/<sid>/<trash_id>/` | 统一归档包（manifest + payload） | 账号 / 公用 |
| `users/accounts.json` | 账户（bcrypt hash） | 全局 |
| `students/<sid>.json` | M2 画像（身份/学段/偏好；无能力数值） | 账号 |
| `students/<sid>.learning_evidence.jsonl` | M2 统一学习证据 journal（唯一事实源；tasks/sources/jobs/判断/scope） | 账号 |
| `students/<sid>.teaching.json` | M3 教学日志 | 账号 |
| `students/<sid>.teaching_guidance.json` | M3 已应用教学指导（M7 提案部署写入，active 标记可吊销） | 账号 |
| `students/<sid>.prompt_memory.json` / `.prompt_memory_pref.json` | M6 精简提示词画像 + 最近会话窗口偏好 | 账号 |
| `students/<sid>.procedural.json` / `.habit_patterns.json` | M6 有界策略成功率 / 学习习惯聚合 | 账号 |
| `students/<sid>.episodes.jsonl` / `.episodes_archive.jsonl` / `.semantic.json` | M6 旧情景/语义兼容审计（生产只读；写侧已删，仅聚合回退与审计 Tab 在读） | 账号 |
| `students/<sid>.learning_episodes.json` | 任务启动绑定投影（task→episode→session；可重建，非权威账本） | 账号 |
| `students/<sid>.eval_traces.jsonl` / `.evaluation.json` | M7 评估黑盒/聚合（教学系统质量事实，无学生能力数值） | 账号 |
| `students/<sid>.ux_profile.json` / `.ux_events.jsonl` | M8 UX 画像/事件 | 账号 |
| `students/<sid>.orchestration.json` / `.orchestration_events.jsonl` | M9 编排状态/事件 | 账号 |
| `knowledge/graph.json` | M5 图谱增量（reasoner 边；P6-A2 已清空，考纲 seed 删除） | 全局 |
| `knowledge/custom/<sid>/<topic>.json` | 活动教材图谱（topic_key=`tb-<id>`；sid=`public` 为公用教材图谱） | 账号 / 公用 |
| `knowledge/custom/<sid>/<topic>.chunks.json` | 概念→chunks 预索引（P6-C2，随图谱删除联动） | 账号 / 公用 |
| `knowledge/vector_db/` | Chroma 向量索引 | 全局 |
| `backend/uploads/` | 会话上传解析文本（`<id>.txt`）+ 原件 | 会话 |
| `backend/traces/` | 每轮 trace | 全局 |
| `notes/<sid>/vault.json` + `notes/<sid>/notes/*.md` + `revisions/` + `agent/*.json` + `uploads/` | M-Notes 笔记仓库（索引/正文/修订/每笔记专属智能体/附件，见文末 M-Notes 章节） | 账号 |
| `chat_history/settings/ocr_policy.json` | 教材 OCR 运行策略（管理员） | 全局 |
| `chat_history/settings/usage_docs.json` | /docs 使用文档（管理员编辑、全员读） | 全局 |
| `backend/models/voice/` | P10 MeloTTS/HF 模型缓存。gitignored 部署侧资源，非用户数据：不进 orphan 扫描，也不入测试沙箱清单 | 全局（本地资源） |
| `backend/vendor/` + `backend/voice_sidecar/.venv/` | P10 MeloTTS 源码与独立 TTS sidecar venv（CPU torch）。gitignored | 全局（本地资源） |

以上账号/运行数据默认全部由 `.gitignore` 覆盖；公共教材库例外为 `chat_history/library/public*`、`chat_history/library/data/public/` 与 `knowledge/custom/public/`，它们随项目版本发布。共享 Chroma 数据库仍不提交，因为同一数据库可能同时承载公共与私有向量，可由公共教材文本重建。

**演示账号例外**：`example@example.com`（固定 id `usr_12e410b4e2`）的产品示范数据——该账号的会话/工作区/笔记/学生模型文件与 `notes/usr_12e410b4e2/`——经 `.gitignore` 精确否定模式有意纳入版本库，作为产品使用示范；账号记录本身不入库（`users/` 仍忽略，克隆机用 `deploy/seed_demo_account.py` 以同 id 同密码重建账号后即可登录查看）。其余账号数据不受影响，一律保持忽略。

---

## 23. API 总表（前缀 `/api/v1`）

- **笔记仓库（M-Notes）**：`GET/notes/{vault,search,graph,reviews/due}`、notes/folders/revisions/templates CRUD、每笔记智能体 `GET/PATCH/DELETE /notes/{id}/agent`、`POST /notes/{id}/review`、`GET /notes/{id}/export`、`GET /notes/export`、SSE `POST /notes/{generate,chat/stream}`（详见文末 M-Notes 章节）
- **健康/模型**：`GET /health`、`GET /model-info`
- **语音通话（P10，默认 off）**：`GET /voice/status`（provider 可用性）、`POST /voice/ticket`（header JWT 换单次 60s 握手凭证）、`WS /voice/ws?ticket=`（push-to-talk 通话协议，见文末 P10 章节）
- **认证/账户**：`GET /auth/status`、`POST /auth/register`、`POST /auth/login`、`POST /auth/logout`、`GET /auth/me`、`GET/PUT /user/profile`、`DELETE /user/account`
- **对话**：`POST /chat/stream`（SSE，`grade` 默认 `""`=自动）、`POST /chat/upload`（`grade` 默认 `""`）、`POST /chat/ocr`、`GET /chat/sessions`、`GET/PATCH/DELETE /chat/sessions/{id}`（PATCH 支持 `{title?, grade?}` 会话内切换学段）、`GET /chat/sessions/{sid}/files/{fid}/download`、`POST /chat/sessions/{sid}/attach_library`（`grade` 默认 `""`）
- **测评交互**：`POST /quiz/grade`（SSE）、`POST /quiz/record`、`GET /quiz/hint`、`POST /quiz/dispute`、`GET /quiz/recent`（journal 投影，跨会话最近习题）、`POST /assessment/{start,answer,next,abandon}`、`GET /assessment/{report,active}`
- **OpenAI 兼容门面**：`GET /models`、`POST /chat/completions`（COMPAT_API_KEY 鉴权，未配置=503；供第三方平台接入，见 §21.6）
- **学生投影**：`GET /student/{profile,teaching-log,learning-path,error-notebook}`（旧 mastery/evidence-profile/bloom-profile/learning-records 端点已删除）
- **学习评价（M2 统一评价域）**：`GET /learner-evaluation/workspaces`、`GET /learner-evaluation/workspaces/{wid}`、`GET /learner-evaluation/workspaces/{wid}/concepts[/{concept_key}]`、`GET /learner-evaluation/workspaces/{wid}/sessions[/{ref}]`、`GET /learner-evaluation/workspaces/{wid}/evidence`、`GET /learner-evaluation/evidence/{source_id}`、`GET /learner-evaluation/jobs/{job_id}[/events]`、`POST /learner-evaluation/evidence/{source_id}/reviews`（异议复核）、`POST /learner-evaluation/jobs/{job_id}/retry`
- **知识**：`GET /knowledge/{graph,catalog,custom}`、`GET /knowledge/concepts/{id}`、`DELETE /knowledge/custom/{topic_key}`（P6-A4：手动 build/regenerate/rollback 端点已移除，图谱只来自教材）
- **管理（P6-B，require_admin）**：`GET /admin/users`、`POST /admin/users/{id}/clear-chat`、`DELETE /admin/users/{id}`、`GET /admin/orphan-data`、`POST /admin/orphan-data/purge`
- **记忆**：`GET /memory/{episodes,semantic,procedural}`
- **评估**：`GET /evaluation/{report,traces,proposals,guidance,context-budget}`、`PATCH /evaluation/proposals/{id}`（applied=部署教学指导）、`DELETE /evaluation/guidance/{id}`（吊销回滚）
- **UX**：`GET /ux/{profile,engagement,motivation,greeting,activity}`（activity=五源按日活动聚合）
- **使用文档**：`GET /docs/content`（公开读）、`PUT /docs/content`（require_admin）
- **编排**：`GET /orchestration/{plan,today,habit,review}`、`POST /orchestration/{goal,regenerate,task,task/{id}/complete,task/{id}/launch,week,week/{i}/concept,week/{i}/task,week/{i}/task/{tid}/subtask,week/{i}/task/{tid}/suggest}`、`PATCH /orchestration/{goal/{goal_id},task/{id},week/{i}/task/{tid}/subtask/{sid},schedule}`、`DELETE /orchestration/{goal/{goal_id},task/{id},week/{i},week/{i}/concept/{cid},week/{i}/task/{tid},week/{i}/task/{tid}/subtask/{sid}}`（/simulation 已删除——死端点，见台账 C7；长期任务 /longtask* 端点随长期任务层一并移除；W4 新增 `POST /task/{id}/launch` 任务启动绑定 + 各写端点容量 advisory 附载）
- **工作区**：`GET/POST /workspaces`、`GET/PATCH/DELETE /workspaces/{id}`、`POST/DELETE /workspaces/{id}/sessions[/{sid}]`、`POST /workspaces/{id}/upload`、`DELETE /workspaces/{id}/files/{fid}`
- **资料库**：`GET /library`、`POST /library/folders`、`PATCH/DELETE /library/folders/{id}`、`POST /library/upload`、`POST /library/files/{id}/move`、`DELETE /library/files/{id}`（级联清孤儿 Textbook 记录）、`GET /library/files/{id}/download`
- **教材库**（P2/P6）：`POST /textbooks/upload`（`level` 学段五选一 + `scope`=private/public，public 仅管理员；`group` 组名成组/`group_id` 追加卷）、`GET /textbooks`（自有+公用合并）、`GET /textbooks/{id}`（含章节大纲）、`GET /textbooks/{id}/download`（原件，公用所有人可下）、`PATCH /textbooks/{id}`（公用仅管理员）、`POST /textbooks/{id}/rebuild_graph`、`DELETE /textbooks/{id}`（级联：图谱+文件+向量+记录）、`DELETE|GET /textbooks/{gid}/volumes/{fid}[/download]`（组卷删除/下载）
- **Trace**：`GET /trace/{run_id}`、`GET /trace/{run_id}/html`

---

## 24. 测试概览

后端 `backend/tests/` 使用 unittest（`python -m unittest discover -s tests`，当前 1818 项通过、skip=4），核心覆盖面：统一学习评价域（journal 事务/校验和/损坏隔离/幂等重放、SourceReceipt 受理与答案指纹判重、MC 确定性判分、语义 job 生命周期与 §10.3 状态映射、scope 解析与公用教材全选上限、复核/撤销、删除与 generation 重写、learer-evaluation API 投影与鉴权隔离）、CAT 生命周期（start/answer/next/abandon/report/active、next 未答幂等重发、评价失败不阻塞出题、刷新恢复）、quiz 信任边界（越权 404 与存储零变化、unverified_practice 零写入、attempt 链与 M9 归因幂等）、量规冻结与 hint/dispute、出题质量门与两轮化、Chat 模糊出题语义与 `auto_invoke` 题卡护栏、检索融合与证据门、学段去僵化、教材库/OCR/知识谱系（P2–P7 各批次回归）、M6 记忆生命周期、M7 诊断/提案/部署与无增益字段（BANNED_KEYS）、M8 滞后与单真相源、M9 SM-2/计划/任务/复习闭环、M-Notes 笔记仓库与智能体、M0 鉴权/限流/账号清除级联、P10 语音票据与 WS 协议、OpenAI 兼容门面、prompt 注册表版本钉扎。每关验收记录见 `docs/plan-gates/`。前端 `tsc --noEmit` 零错误 + eslint + `next build`（默认与 --webpack 双轨）通过，Playwright e2e 10/10。

---

*本文档只描述当前架构现状。修改架构时请同步更新对应章节。*

## M5.9 教材知识谱系三级分类与资料元数据契约（2026-08-10）

M5 的知识节点/边与资料中心元数据解耦。资料中心是教材分类元数据的唯一事实源，M5 页面只建立一个动态 taxonomy 投影，不因为改名、改学科或改学段而重新 OCR、切块、向量化或生成图谱。

三级目录固定为：

```text
学段（小学 / 初中 / 高中 / 本科 / 其他）
└── 学科（资料中心 subject，原样作为唯一标签）
    └── 教材组/栏目（TextbookRecord.group_name，单本教材也有独立栏目）
        └── 该教材 topic_key 下的章节与知识节点
```

`TextbookRecord` 增加/规范以下展示元数据：`group_name`、`group_note`；旧数据缺少 `group_name` 时以 `title` 兼容回退。单个 PDF 的 `file_id`、教材组的 `file_ids` 和 `topic_key` 永远稳定。`LibraryFile.filename` 是可编辑的文件显示名，`original_filename` 仅记录上传时文件名；重命名不会改变文本、chunk、向量索引或节点 ID。

新增只读投影 `GET /api/v1/knowledge/taxonomy`，返回 `levels[].subjects[].groups[]`，每个教材组附带 `textbook_id`、`topic_key`、`node_ids`、卷文件和备注。`GET /api/v1/knowledge/graph?textbook_id=...` 支持按教材组隔离节点与边。资料中心元数据修改后，下一次 taxonomy 请求立即反映新分类，节点关联保持不变。

教材编辑契约：`PATCH /textbooks/{id}` 支持教材/栏目名称、教材组备注、学科、学段；`PATCH /textbooks/{id}/volumes/{file_id}` 支持 PDF 显示名；通用资料文件使用 `PATCH /library/files/{file_id}`。公用教材仍只允许管理员修改，普通用户只读可检索。


## 2026-08-13 生命周期与知识谱系统一契约

### M5 教材知识谱系发布规范

- 教材组采用“卷元数据 + 章节 → 概念”结构，不创建 volume 图节点。
- chapter 展示名经过确定性规范化：去书名、卷名、原始文件名、扩展名、作者/版本/出版社/网址/下载站噪声；卷身份保存在 `metadata.volume_id/file_id/volume_title/chapter_order/raw_heading`。
- 同名章节跨卷以稳定内部 `chapter_key` 区分，展示名不添加文件名前缀；概念仍按规范化名称跨卷归并。
- 发布前质量门拒绝空章、非教学章、文件/URL 污染章、无概念章；重建采用版本递增的原子替换，不生成无法管理的隐藏 `archive/*.vN.json`。
- 启动时一次性、幂等清理旧式 `knowledge/custom/*/archive/*.json`；旧快照不是完整教材归档，不承诺恢复。

### 统一回收站与彻底删除

- 回收站根目录为 `chat_history/trash/items/<owner>/<trash_id>/`，每项包含原子 `manifest.json` 与 `payload/`。覆盖 `session/library_file/library_folder/textbook/textbook_volume/workspace/knowledge_graph`。
- 删除先生成完整快照，再从活动索引、向量轨和引用中移除；恢复保留原 ID，教材/资料可选重新挂接现有工作区；工作区作为 bundle 恢复 workspace、共同记忆、专属资料和成员对话。
- `GET/PUT /api/v1/trash/policy` 管理用户 1–30 天偏好；默认 7 天。管理员 `GET/PUT /api/v1/admin/data-retention` 设置默认、最大/强制最长、manual/auto 模式。启动扫一次并由进程内定时任务幂等清理过期项。
- `GET /api/v1/trash`、`GET /api/v1/trash/{id}`、`POST /api/v1/trash/{id}/restore`、`DELETE /api/v1/trash/{id}`、`DELETE /api/v1/trash` 为用户回收站契约；管理员只管理 `/admin/public-trash`，不读取用户私有归档。
- 永久删除清理活动/归档副本、原文件、解析文本、OCR/附件、chunks、BM25/Chroma、教材注册、图谱/历史版本、会话 JSON、transcript、trace、工作区引用与 manifest。用户明确保留的独立学习档案除外。

### 记忆与学习档案

- M6 原情景/语义文件是兼容审计数据，生产只读且不再检索注入 prompt。精简 prompt memory 为 `students/<id>.prompt_memory.json`，最近窗口默认 15、用户可选 5–30，最近贡献带 `session_id` 可撤销；滑出窗口后进入不可按单会话反向拆分的限长 core profile。活动策略/习惯分别使用有界 `.procedural.json` / `.habit_patterns.json`。
- `students/<id>.learning_evidence.jsonl` 是统一学习证据账本，保存题目材料、作答、判分与语义解释，不因来源对话永久删除而删除；来源被删时该来源按删除语义归档（对外不再定位到底层来源 ID）。
- 工作区共同记忆保存在 workspace bundle 内，与用户级 prompt memory 隔离；新工作区对话边界最多整体压缩一次；删除单聊不回退，永久删除工作区才清除。

### 账号注销边界

`DELETE /user/account`（自助注销）与管理员 `DELETE /admin/users/{id}` 已统一走 `account_data.purge_account`：名下全部数据（会话/转写/trace/上传/工作区/资料库/回收站/笔记/学习档案/知识图谱）不可恢复地清除，不保留空目录；账号记录最后删除，中途失败留下可重试的账号（幂等）。历史版本曾保留 `students/` 审计数据，2026-08-23 起改为彻底清除。对象级回收站（`core/trash.py`）语义不变；`purge_item`/`restore_item`/GC 清空条目后顺手移除空的 `items/<owner>/` 目录。测试残留与注销前遗留的孤儿数据由 `GET/POST /admin/orphan-data[/purge]`（`core/orphan_cleanup.py`，管理台「数据清理」页）扫描与清理，注册账号与 `public`/`student_default` 共享命名空间永不受影响。

## M5 教材逐卷容量、四级 taxonomy 与 OCR 动态策略（2026-08-14）

- 教材统一 group 模型，taxonomy 为“学段 → 学科 → 教材组 → 单本教材 → 章节 → 概念”；单本教材只作过滤层，不生成书名节点。旧 single 启动时幂等迁移，保留 ID/topic_key/file/workspace 归属。
- `graph_policy` 以 `null` 表示不限，包含组默认与 `volume_overrides`。每卷独立裁剪，组没有共享预算；完整规范化抽取缓存位于 `knowledge/custom/<owner>/<topic>.volume_specs/<file>.json`，限制变化只快速重合并。
- 概念 ID 由规范化名称稳定 hash 生成；跨卷同名概念合并并保留多个 `PART_OF`，metadata 聚合 file/chapter 来源；page range 使用 `chapter_key`。
- 图谱接口支持 group/volume 范围与 `overview/chapter/search/full`；taxonomy group 返回 volumes coverage。构建状态增加 `partial`，staging 质量门和 last-known-good 防止临时失败降级覆盖。
- `PDF_OCR_CONCURRENCY=20` 仅作 bootstrap。管理员 `GET/PUT /admin/ocr-policy` 可在 1–64 动态调整；系统所有教材后台 OCR 共享 generation limiter，聊天附件和普通资料库同步 OCR 仍串行。策略存储为 `chat_history/settings/ocr_policy.json`。
- 教材归档 bundle 包含 graph policy、coverage、concept index 和完整 volume spec 缓存；恢复保持稳定 ID，彻底删除清除全部活动及归档痕迹。
- 知识谱系页面的顶层“全部教材”是全局重置：同时清空学段、学科、教材组和单本教材；下层按钮使用“当前学段全部学科/当前范围全部教材组”明确范围。跨教材章节总览的 subtitle 由 taxonomy `node_prefix` 临时派生教材组名，不把书名写入持久化章节节点。

### 教材 OCR 持久状态与 Structured RAG V2（2026-08-14）

教材 OCR 与同步附件 OCR 已拆分为两条故障契约。`core/textbook_ocr.py` 只服务教材后台：每轮仅处理 `ocr_state.volumes[file_id].pending_pages`，失败即原子保存部分文本/页状态并返回，绝不在全局页 limiter、OCR job 或 per-student build lock 内等待；`next_retry_at` 到期再由进程任务恢复。默认 `persistent_api` 不调用 tesseract，配置错误也保持可见的 `ocr_waiting`；两种 bounded 模式分别在页级上限后本地 psm3 或暂停。聊天、工作区、普通资料库继续走原 `ocr_page_image` 的短重试和本地回退。

**永久性页面错误终态（防死循环，2026-08-15）**：`empty_content`（模型正常响应但页面无文字，典型为书末空白页/版权页）与 `render_failed` 属页面永久状态，重试无意义。达到 `max_attempts` 后按「空白页」收尾：页文本保留 `""` 占位、记入 `empty_pages` 并计入完成数，教材继续构建（`bounded_then_local` 仍先试一次本地 tesseract）。瞬时错误（429/5xx/timeout/连接）不受影响，`persistent_api` 下仍按管理员策略持续等待重试。状态重建（文本 hash 变化等触发）时 `empty_pages` 跨重建继承，不再重复 OCR 已知空白页。

**逐页增量持久化（慢模型断点续传）**：视觉模型单页可达 3 分钟（管理员可将 `request_timeout_seconds` 调至最高 300），轮内每页完成即结算——先轻量原子写 `.txt`（不重切块）再保存 `ocr_state` 与 per-page 进度，进程中途被杀不丢已完成页；重启后 `reap_stale_builds` + `resume_pending_textbook_ocr` 只重试 pending 页，绝不从头开始。单页意外异常只记该页失败，不炸整轮。轮末 `_write_text_and_chunks` 按内容 hash 幂等跳过：零进展的等待轮不再全书重切块与重写 library JSON，崩溃自愈复查也零开销。删除守卫：轮内发现教材记录/库文件已被归档删除即停止一切写回（不复活孤儿 `.txt`）；`textbook_builder` 在图谱/概念索引写入点 re-check 记录存在，构建途中被删除则静默中止，不把已删除 `topic_key` 的图谱写回磁盘。

**per-owner 构建队列（有界并发，legacy 可回退，2026-08-25）**：构建锁只在单次构建期间持有且按 **(owner, textbook_id)** 粒度互斥——同一本书绝不并发构建（队列自动构建 vs 手动刷新互斥），不同书可并行。`textbook_builder.enqueue_textbook_build` 为每个 owner 维护 FIFO 队列与单一调度 worker：同时在构建的教材数 ≤ `textbook_pipeline.build_concurrency()`（默认 2；**legacy 模式强制 1**，即 2026-08-15 前的严格「队首到终态再建下一本」契约）；OCR 重试轮仍由队列项内的门控驱动，worker 只做派发与名额回收（并兜底复活死掉的 resume 任务）。上传、启动恢复、删卷重建、容量策略重合并均走队列；`rebuild_graph` 三模式等用户显式刷新旁路队列、仍受 per-book 构建锁互斥。进程重启后队列随 `resume_pending_textbook_ocr` 按记录顺序重建。默认重试间隔 `_DEFAULT_INTERVAL=10s`（等待响应的 timeout 独立可调至 300s），管理员可按实例覆盖。

教材记录的 `ocr_state` 是重启恢复事实源，包含状态版本、force-full 模式、物理/目标/成功/待处理/暂停/空白页、逐页 attempts、next retry、错误码摘要、策略 generation、配置阻塞及 API/本地成功计数，不保存图片、密钥或模型原始响应。归档取消运行任务并把状态收入 bundle；恢复重新调度 waiting 页；彻删删除 bundle 与其中状态。

`POST /api/v1/textbooks/{id}/rebuild_graph` 接受 `{mode}`：`rag_graph`（默认）只读既有 `.txt`，重建 Structured V2 chunks、BM25、可选 Chroma、concept→chunks，并使用健康 volume cache 重合并图谱，缺 cache 时可调用图谱 LLM 但仍 `skip_ocr`；`graph_only` 只重抽图谱；`full_ocr` 清空旧页状态并人工确认后整本 OCR。`POST /api/v1/admin/public-textbooks/rag-upgrade` 只批量调度公共教材 `rag_graph`。PDF/.txt 是事实源，RAG 是可删除重建的派生物。

Structured V2 chunks 保存 `chunk_schema=structured-v2`、block types、noise flags、章→节→小节 section path、page range、NFKC normalized offset 与回映原文 offset、结构 parent/prev/next、token estimate 与 content hash。确定性切片目标 220–480 tokens、软上限 520、硬上限 650；定义/定理/公式/表格/例题等结构块在硬上限内保持完整。重建先在内存 staging 质检唯一 ID、页映射、hash 和硬上限，再按教材组单次原子发布；同 owner 的刷新串行，避免公共库批量升级发生 JSON 丢更新。检索先进行 BM25/可选 vector 广召回，RRF 只参与排序；`core/evidence_gate.py` 以专业短语覆盖、BM25 raw、校准 vector distance、concept bonus 和噪声信号执行绝对/相对门、近重复与同页去重。通过项生成稳定 EvidenceBundle；卡片与模型上下文只消费同一 excerpt/hash。`RAG_CHUNKER_MODE`、`RAG_EVIDENCE_GATE`、`RAG_CONTEXT_COMPRESS` 控制灰度；shadow telemetry 记录候选/淘汰原因/no-hit/近重复率、仅由 file/chunk 引用生成的候选指纹、selected context hashes 与延迟，不记录原文或 CoT。

## P7 教材/资料图表结构化 + 页码双轨 + 检索修复 + 对话多模态路由（2026-08-15）

### P7.1 图表结构化标记（扫描书 = 视觉模型即版面分析器）

扫描全书无文本层、无法区分图文区域——工业界对扫描件的标准做法是把版面分析
交给视觉通道：教材逐页 OCR 的 `_PAGE_OCR_PROMPT`（`core/ocr.py`）升级 v2，
转录正文的同时输出结构化标记，随 `.txt` 事实源持久化（断点续传/empty_pages
机制不变）：

- `[页码=N]`：页面自己印刷的页码（页眉/页脚），无则省略；
- `[图 3-2|图注]` + `图述：`2–4 句客观描述（对象/方向/几何关系/数值趋势）；
  纯装饰图标标 `[图|装饰]` 一笔带过；
- `[表 N|表题]` + `|` 分隔行（保留行列结构）。

**原生 PDF（文本层书）收割**（`core/figure_harvest.py`，`RAG_FIGURE_HARVEST`
默认开，关闭即降级纯文本层）：`page.find_tables()` 确定性提取表格 markdown
（零 LLM）；`get_images()` + `get_image_rects()` 提取位图区域（过滤 <60pt/
<1% 面积小图标与 >90% 整页扫描图，单卷 ≤40 张成本护栏），裁剪渲染 → 复用
多模态通道生成图述（受 `ocr_policy` 并发治理）；PDF page label 为纯数字时
输出 `[页码=N]`。收割块按页并入 `.txt`（无收获时文本 hash 不变，rag_graph
刷新零成本跳过）；图谱 spec 仍用纯正文（标记不进 TOC/骨架抽取）。仅教材
构建路径调用；矢量图形不做聚类猜测（下划线/边框易误判——宁缺毋滥）。

### P7.2 Structured Chunker V2：figure 块 + printed_page

`core/structured_chunker.py` 新增：`[图...]/[表...]` 标记行为块边界，
`图述/图注/题目转录` 前缀与 `|` 行为从属行；`figure` 块类型受保护
（不切分、不并段、前后块不得打包进它）；页首 `[页码=N]` 解析为 chunk
metadata `printed_page`（从正文剥离）。检索结果透传
`block_type`/`printed_page`，`location_label` 双轨：**教材第 N 页**（印刷
页码）优先，否则 PDF 第 M 页；模型上下文 `[来源...]` 行同步并带 `[图]`/`[表]`
徽标。旧书 `.txt` 无标记 → `GET /textbooks/{id}/figure-status` 探测，刷新
弹窗默认推荐一次「完整重新 OCR」升级（full_ocr 产出新标记；之后恢复
rag_graph）。

### P7.3 上传文件多模态对齐（docx/pptx/pdf/md）

`core/multimodal_parser.py`：docx/pptx 内嵌媒体、PDF **稠密页**插图/表格
（稀疏页仍整页视觉 OCR）、md data-uri 图统一走 `describe_embedded_image`
分型描述（题目照片→完整转录题干；插图→图述；装饰→丢弃），产出
`[图|文档图片 N]` 标记块（与教材管线同构）。失败按"丢弃该图"降级，绝不
阻塞上传。

### P7.4 对话多模态路由 + 思考模式（`core/multimodal_context.py`）

本轮上下文含图（图片附件原件 + RAG figure/table 证据页快照，合计 ≤3 张
token 护栏）时，tutor LLM 从主模型切换到 **MULTIMODAL 通道**
（`AsyncLLMClient` 参数化实例），图片以 content parts 注入**最后一条 user
消息**（会话历史/持久化保持纯文本，不污染 token 估算与压缩）；多模态轮
**开启思考推理**（覆盖 grounding 的关思考策略；预算护栏仍优先）。未配置
`MULTIMODAL_API_KEY` → 降级纯文本不报错。trace 记 `multimodal_routing`。
对照：教材 OCR/图述等**提取型**调用保持 `disable_thinking`（转录不需要
推理，开推理更慢更贵且易吃输出预算）——两策略相反是刻意设计。

### P7.5 检索准确性修复（2026-08-15「导数高中要学点什么」回归）

- **证据门问句门**（`core/evidence_gate.py`）：`weak_primary_phrase`/
  `no_professional_phrase` 曾把整句口语问句当专业短语，要求候选 chunk 覆盖
  60–75% bigram（导数/数高/高中/中要/要学/学点/点什/什么）——教材正文永远
  达不到 → 全部候选被丢 → NOT_FOUND（选必2 有 112 处「导数」仍检索不到）。
  类别级修复：`is_natural_question`（疑问标记正则族）识别问句；问句先剥
  口语尾巴（`question_core`：要学点什么/是什么/怎么学/考什么…）与学段/
  必修语境词，词项/短语从内容词派生（`effective_query` 统一基）；问句跳过
  两条短语覆盖门，相关性由词项覆盖 + 阈值门判定；非问句的专业短语门语义
  保留（麦克斯韦分布 vs 方程组反例仍拦截）。`knowledge_search._query_variants`
  同步接入内容词核变体。
- **trace 可诊断性**：`tool_result` 事件补记 `error_code`/`error_message`/
  `gate_drop_reasons`（本次排障只能看到 status=error 的缺口）。
- **伪工具标签护栏**（`agents/pseudo_tool_guard.py`）：弱模型在正文里"叙述"
  工具调用而非发起 function call。已观测两种格式（2026-08-15 假
  `<knowledge_search>` 标签；2026-08-31 `<tool_call><function=…>
  <parameter=keywords>…` XML 叙述——deepseek-v4-flash 把整段标记流进正文
  并被 TTS 原样朗读），护栏按标签集 `<knowledge_search` / `<tool_call` /
  `<function=` 匹配：流式输出时任一标签形成即停流（半截前缀持有），前导
  正文照常流出；两条 ReAct 环路（executor/chat_agent）据此执行**真实**
  检索、注入结果并继续环路让模型基于真结果续写（每轮一次，trace 记
  `pseudo_tool_guard`），检索词优先取 `<parameter=keywords>` 参数；护栏
  检出后 `answer_buf` 统一回退为实际放行的 `emitted`，标记绝不进入最终
  答案与会话历史；`to_speakable` 另有一层标记剥除兜底，任何漏网路径都
  不会被朗读。
- **NOT_FOUND 语义细分**：可见教材仍在 building/ocr_waiting 时提示
  「教材仍在后台解析中，完成后即可检索」。

### P7.6 新端点

- `GET /api/v1/textbooks/{id}/figure-status`：各卷 `.txt` 是否已含图表/
  页码标记（刷新弹窗升级提示）。
- `GET /api/v1/library/files/{file_id}/page/{page}`：PDF 原件按需渲染页快照
  PNG（自有→公共解析序，外人/非 PDF/越界 404，Cache-Control 1 天）——
  图表证据「查看原页」通道，扫描书同样适用；前端证据卡带
  图/表徽标 + 查看原页按钮，多模态轮 RAG 图表块页快照由此渲染。

## P7.7 解析终止、页码标记容错与知识谱系统一规范（2026-08-15 第二批）

### P7.7.1 在途解析终止（合作式取消）
- 端点 `POST /api/v1/textbooks/{id}/cancel`（教材卡/抽屉在 building/ocr_waiting/
  ocr_paused 显示「终止解析」）。取消 = 杀进程内任务（OCR 恢复调度器 +
  refresh 任务）+ 置记录 `parse_cancel_requested` 标记（构建各检查点观测后
  停：`_volume_spec` 入口 / 逐章抽取 / OCR 逐页 settle / 组卷循环）+
  就地结算终态：任一卷已有可用文本（.txt > 50B）→ `ready`（文本与切片保留），
  否则 `failed`。标记保留至下一轮构建开始（`run_textbook_build` / 手动刷新
  入口清除），绝不影响未来的重建。
- OCR 轮返回 `cancelled` 状态（state.status=cancelled，恢复调度器不复活），
  `_volume_spec` 识别后抛 `TextbookParseCancelled`，构建链统一结算。
- 服务启动不再复活被终止的轮次；`resume_pending_textbook_ocr` 对
  `parse_cancel_requested` 记录直接结算。

### P7.7.2 页码标记位置容错（chunker）
- 实测视觉模型把 `[页码=N]` 放在**页末行尾**而非页首（语文选必上册 109 个
  标记全在行尾）。`_PRINTED_PAGE_INLINE_RE` 页内任意位置剥离标记子串（行内
  其余正文保留，含图述行尾标记），取该页**最后一个**标记数字进
  `printed_page`，剥离后空行丢弃；页首独立行原路径保留。

### P7.7.3 OCR 恢复轮保住 force_full（类级修复）
- 组 full_ocr 中途某页失败进入 ocr_waiting 后，恢复轮曾丢失 force_full 标志
  （只补稀疏页 → 旧 prompt 文本永久保留，新标记永不生效——语文选必下/中册
  实测缺陷）。三层修复：`process_textbook_ocr_round` 入口意图继承（既有
  state.force_full=true 未完成 → 按全量继续）；`schedule_textbook_resume`
  runner 与 `resume_pending_textbook_ocr` 按 ocr_state 传播 `force_full_ocr`。

### P7.7.4 知识谱系统一规范（跨学段一致的章层契约）
- **建模模型契约（防漂移）**：谱系构建全部走主 LLM（`llm_async.get_llm()`，
  即 LLM_MODEL，`disable_thinking=True` 小 JSON 调用）；OCR 才走 MULTIMODAL
  通道（视觉模型，关思考）。两者分工不可互换。
- **Tier 1 书签标题质检**（`_garbage_outline_title`，类级结构规则、无书名
  词表）：文件名（`_`/`.pdf`/`.djvu`）、印刷段号（`^\d{3,}[-_]`、`2.23小`
  工单模式）、卷/书名包装（含出版社/教科书/第N版，或归一化等于卷文件名
  stem）、无教学关键词的长 ASCII 串；噪声条目（目录/封面/版权/扉页/封底）
  直接剔除。垃圾占比过半 → 整个 Tier 1 弃用（印刷厂分段的页码范围同样不
  可信），交 Tier 2 LLM 从正文定位真实单元。卷名包装条目剥前缀保留
  （`化学反应原理第1章`→`第1章`，页码范围是真实边界）。
- **章名规范**：兜底章名统一「全册」（原「全书」）；`_HEADING_LINE_RE` 增补
  独立短行 `第N单元/课/部分`；概念名 >12 字截断（显示一致）。
- **缓存定向失效**：`_CHAPTER_LOCATOR_VERSION=3` 进 `_valid_cached_spec`
  校验；缓存章名命中垃圾判定 → 重建（好缓存零成本复用）。
- **组级章序**：`chapter_order = 卷序号*1000 + 卷内章序`（跨卷同名序不冲突）；
  前端画布同层节点按输入序（构建顺序）稳定排序，不再按哈希 id 乱排。
- **继承关系归并（审计确认）**：`spec_to_graph` 全局 name_to_id 跨卷同名概念
  合并单节点、前置引用按名成边、DAG 环守卫保留；prompt v2.1 要求 prerequisites
  只填本章概念名，跨章前置由图谱统一归并。

### P7.7.5 prompt 与显示
- `textbook_toc_extract` v2.1（完整单元标题、忽略 OCR 噪声行）、
  `textbook_chapter_concepts` v2.1（概念必须出自本章原文、≤12 字、prereq 规范）。
  版本 bump 进 `_prompt_fingerprint` → 各书刷新时全量重抽一次（一次性成本）。
- 画布长标签**两行渲染**（词边界拆分，节点盒自动加高至 54px，`<title>` 悬停
  全名已有）；教材卡在解析中状态显示「终止解析」按钮。
- 管理台「公共教材 Structured RAG V2 升级」模块删除：切块惰性自动重建 +
  缓存定向失效已覆盖其全部功能（端点 404，前端卡片移除）。

### P7.7.6 刷新模式清理契约（refresh 各模式精确清理对应文件）

刷新按钮各模式**清理/重建的文件集合不同**，且绝不越界：

| 模式 | `.txt` 事实源 | 原 PDF | OCR 状态 | chunks/BM25 | 向量轨 | 图谱 | 概念索引 |
|---|---|---|---|---|---|---|---|
| `rag_graph`（RAG+图谱，默认） | 可被原生收割**追加**表格/插图标记（文本层 PDF） | 不动 | 不动 | 全量重建 | upsert 缺失 + **剪枝本 scope 孤儿向量** | 重合并（spec 缓存命中则复用） | 重建 |
| `graph_only`（仅图谱） | **不动**（skip_harvest 闸门） | 不动 | 不动 | 不动 | 不动 | 强制重抽 | 重建 |
| `full_ocr`（完整重新 OCR） | 页级全量重写（失败页保留旧文本，可用性优先） | 不动 | 清空重建（cancelled 不复活） | 构建后全量重建 | 同 rag_graph | 重抽 | 重建 |
| 删除教材组/教材 | 全部进**归档区**（.txt/.orig/图谱/概念索引/记录，可恢复） | 进归档 | 进归档 | 进归档 | 进归档 | 进归档 | 进归档 |

关键实现点：
- **收割归属**：`_volume_spec(skip_harvest=True)` 仅在 graph_only 置位——图谱抽取本来就
  用收割前文本（`base_text`），收割只为 RAG 服务，graph_only 因此完全不触碰 `.txt`
  （RAG 事实源与索引零变化）。
- **rag_graph 事后重索引**（`_reindex_rag_after_build`）：构建期间收割可能改写 `.txt`，
  构建完成后补一次 RAG 重建，保证 rag_index `content_sha256` 与最终 `.txt` 一致
  （语文必修实测 hash 过期问题）。
- **向量孤儿剪枝**：`ensure_indexed` 重建后按 `where={"scope": scope}` 分页取本 scope
  全部 id，删除不在当前 chunk 集合的旧版本向量——只清本 scope，其他教材/资料不受影响。
- **图谱替换**：重建单文件原子替换（version 递增），不产生 archive（archive 仅删除
  流程使用）；中途取消不落半成品（合并成功才写盘）。
- **重新 OCR**：full_ocr 开始即清空 `ocr_state` + 取消旧任务/恢复调度；页级覆盖写
  `.txt`；取消后保留已完成页（合作式），cancelled 状态不入恢复队列。

### P7.7.7 force_full 意图按卷钳制（2026-08-16 修整卷重 OCR）

教材组的恢复/入队路径按**组全局**计算 force_full（任一卷处于未完成全量轮即整组传
True），若不钳制，稀疏重试中的兄弟卷会命中轮次入口的「意图不匹配 → 状态重建」，
`successful_pages`/`attempts` 全部清零、目标翻全量——**整卷重 OCR**（实测：语文必修
一卷 api_success 150→0、大学物理学 428→515，均无人工触发）。

`process_textbook_ocr_round` 入口的两条对称规则：
- **正向继承**（原有）：state 处于未完成全量轮时，调用方丢了标志也按全量继续——
  保证新 prompt 的全量升级不被稀疏化。
- **反向钳制**（新增）：state 处于在途轮（ocr/waiting/paused）且 `force_full=False`
  时，即使调用方传 `force_full=True` 也按 False 继续——**在途稀疏轮永不升级**。
  新的全量意图只对非在途状态（全新/已完结）生效；显式 full_ocr 刷新在上游清空
  `ocr_state`，不受影响。

重试续跑语义（既有，此处成文）：逐页 checkpoint（`_settle` 每页落盘），重试只跑
`pending_pages`；从头重来仅三种显式重建——full_ocr 刷新清 state、force_full 意图
对非在途卷翻转、源文本 hash 变化。已判空白页（known-empty）跨重建继承不重烧。

### P7.7.8 单一驱动入口（2026-08-16 修队列契约违反）

构建的唯一驱动是 **per-owner 队列**（`_BUILD_QUEUES`）——上传自动构建、手动
刷新（rebuild 三模式）、失败重试、回收站恢复全部入队。旧的三驱动并行（队列｜
`_TASKS` 直连 resume runner｜刷新直连 `_safe_build`）绕开队列与锁的组合，已移除。
（2026-08-25 起串行契约升级为**有界并发 + legacy 回退**，单驱动不变，见 P7.7.11。）

关键实现点：
- **门控即重试驱动**：`_wait_book_terminal` 在 ocr_waiting 且各等待卷
  `next_retry_at` 到点时，就地 `run_textbook_build(force_reextract=False,
  auto_retry=True)`；重试是轻量的（spec 缓存有效即复用，失效由 prompt 指纹/文本
  hash 独立保证），周期不再被整书 LLM 重抽 gating。重试后全书 ready 就地补 RAG
  收尾（`_post_ready_rag`：chunk 重建 + 向量 + 摘要）。无等待卷信息的 ocr_waiting
  保持被动轮询（异常状态防御，重启后由收割逻辑处置）。
- **auto_retry 守卫**：自动续跑项遇记录已删/终态直接跳过；手动刷新项不受限。
- **enqueue 返回 Future**：手动刷新 `_safe_build` 入队并等待该次构建（含门控至
  终态）完成——full_ocr 刷新的重试轮也由门控驱动，终态后自然衔接 RAG 重建（修掉
  旧「刷新见到未 ready 就跳过 RAG」缺口）。同步测试上下文（无事件循环）回退直连。
- **取消**：合作式 `parse_cancel_requested` 标记仍是唯一取消机制（构建入口消费 +
  OCR 逐页结算检查点）；`_TASKS` 注册表保留但不再有常驻 runner。

### P7.7.9 OCR 策略边界与概念数上限链条（2026-08-16）

- **策略边界**：并发 1–**100**（原 64）、重试间隔 **0**–3600 秒（原 5 下限；0=到点
  即重试，实际节奏受构建队列轮询 `QUEUE_POLL_SECONDS=5` 限位）。persistent_api 下
  max_attempts 有实际意义（空内容类页到限即按空白页收尾），管理页输入已解锁。
  并发 100 = 100 页渲染 + base64 同时驻留内存，WSL2/小内存实例慎用。
- **概念数上限链条**：每卷裁剪读教材组 `graph_policy`（`default_max_concepts`/
  卷 override，**null=不设上限**）→ 组级合并用全局兜底
  `TEXTBOOK_GRAPH_MAX_CONCEPTS=400`（可环境变量覆盖）→ 概念抽取 prompt 无任何
  数量指令，数量由 LLM 按章节内容密度决定（线性代数 5 章 100 概念为巧合非上限，
  extracted==included 零裁剪）。

### P7.7.10 PyMuPDF 全局锁（2026-08-16 修进程段错误）

PyMuPDF 非线程安全（MuPDF 共享全局上下文）：教材 OCR 并发调到 50 时，每页一个
`asyncio.to_thread` 线程并行 `fitz.open + get_pixmap`，实测段错误整个 uvicorn
进程（SIGSEGV，后台在途构建全部丢失——重启后靠逐页 checkpoint + reap 续跑）。

契约：**所有 fitz 文档操作必须持 `pdf_ocr.FITZ_LOCK` 完成**（open→操作→close
整段在锁内；渲染/抽文本为毫秒~秒级，全局串行的吞吐损失远小于视觉模型调用延迟）。
异步函数中**不得跨 `await` 持锁或持有长命文档对象**——多页渲染逐页经
`render_page_pixmap`（内部独立加锁），整书页数/文本层走锁内助手
`pdf_page_count` / `pdf_page_texts`。覆盖点：`pdf_ocr` 全部函数、
`textbook_ocr` 轮次探针（`to_thread`）、`file_parser._extract_pdf`、
`figure_harvest.harvest_native_blocks_sync`、`textbook_builder` 目录/文本层抽取。

### P7.7.11 解析流水线有界并行（2026-08-25，执行调度优化）

目标：**只改执行调度，不改解析方式与产出**——同样的 prompt、同样的逐章/逐卷
输入、同样的确定性合并与写盘逻辑、同样的 OCR 逐页结算/断点恢复/重试语义；
相互独立的调用在时间上重叠。管理员经 `GET/PUT /admin/textbook-pipeline`
在线调整（`core/textbook_pipeline.py`，策略落盘
`chat_history/settings/textbook_pipeline_policy.json`，env 变量仅作文件缺失
时的默认值）；**legacy 模式把全部有效并发强制为 1**，FIFO 门下执行顺序与
历史严格串行实现逐调用一致。

四层并行（互不抢资源：vision OCR 页并发仍由 `ocr_policy` 单独限流）：

1. **逐章概念抽取**（`_full_path_spec`）：章间零依赖（每章 prompt 只依赖
   章名/章文本/书级 subject/level，后两者循环前已定），任务全部创建后由全局
   `llm_gate` FIFO 限流；**结算严格按章序**——chapters_out / warnings /
   progress / 取消检查点逐章语义与串行相同（完成顺序可乱序，产出不变）。
2. **跨书有界并发**（队列调度器）：同一 owner 同时在构建 ≤
   `build_concurrency`（默认 2）——书 B 的 OCR 与书 A 的逐章抽取重叠。
   派发按 FIFO 且同书去重（正在构建的书不重复派发，避免占名额等锁）。
3. **组内多卷并行**（`_build_group_inner`）：抽取阶段按
   `volume_concurrency`（默认 2）并行、每卷独立 warnings 缓冲；后处理严格
   按卷序（subject 首卷优先、`chapter_order=卷序×1000`、coverage 顺序）。
   卷 OCR 延迟（`TextbookOCRDeferred`）捕获为哨兵：兄弟卷跑完落
   volume_spec 缓存（重试轮零成本复用），按序后处理时统一抛出。
4. **图表图述**（`figure_harvest._describe_figures`）：gather 并发，仍经
   `ocr_policy.run_page` 全局限流，结果保序。

配套并发安全修复（并行化前置条件，legacy 下同样生效）：

- **per-book 构建锁**：`_BUILD_LOCKS` 键从 student 改为 (student, tb_id)——
  同书互斥保留（自动构建 vs 手动重建），跨书放行由队列名额控制。
- **ocr_state 新鲜读-合并-写**（`textbook_ocr._settle/_save_state`）：每次
  结算重新读记录、只覆盖本卷键。旧行为写轮起止快照（root），并行卷交错结算
  时后写者会用旧快照回滚兄弟卷进度。同步无 await，事件循环内原子。
- **library RMW 互斥**（`_write_text_and_chunks`）：读改写段加
  `rag_index._owner_rag_lock` 并在工作线程执行（`asyncio.to_thread`）——
  与 RAG 重建（to_thread）并发时不再互相丢 chunk 更新，重切块 CPU 同时
  移出事件循环。
- **LLM 客户端并发参数**：`AsyncLLMClient(concurrency=1)` 默认不变（全部
  既有调用方行为不变）；教材构建客户端取上限 8，实际节流统一由动态门
  `llm_gate()`（Condition 计数器，限额每次准入动态读取策略，在线调整立即
  生效、在途调用按旧限额跑完）负责——取代原模块级
  `asyncio.Semaphore(2)`。

## M-Notes 笔记仓库与笔记智能体（2026-08-19；2026-09-01 每笔记专属智能体重构）

回答「学习痕迹如何沉淀为可长期温习的个人知识库」。独立子系统（不进 M1 Supervisor
链路、不复用 chat 页面与提示词）：每用户一个 Obsidian 式 Markdown 仓库 + **每笔记
专属**笔记智能体（问答/计划/授权三模式，2026-09 起协作模式与建议队列移除）。包：
`core/notes.py`（存储）、`core/notes_templates.py`（模板）、`agents/notes_agent.py`
（智能体）、`api/v1/notes.py`（路由）；前端 `/notes`（三栏：侧栏 | 编辑/预览/图谱 |
AI 面板，两侧栏可折叠、边缘拖宽，宽度持久化）。

**存储布局**（根目录 `notes/<safe_sid>/`，`.gitignore` 以 `/notes/` 根锚定，
避免误伤 `frontend/src/components/pages/notes/`）：

- `vault.json`：仓库索引（folders / notes 元数据 / custom_templates）；folder 使用 `parent_id` 组成多层树，旧索引加载时自动补根级空值。元数据（标题、
  文件夹、标签、template_id、status、source 溯源、review 调度镜像、revision 版本号）
  只存索引；首次访问播种四个默认文件夹（错题修正/知识点总结/学习温故/章节笔记）。
- `notes/<note_id>.md`：笔记正文（纯内容）。导出时拼 YAML frontmatter（Obsidian 可
  直接导入）。`note_<YYYYmmdd_HHMMSS>_<slug>` id，`Path(name).name` 防穿越。
- `revisions/<note_id>/`：修订快照（`{rev:04d}_{ts}_{author}.md`，每笔记上限 20）。
  每次写入（用户保存/Agent 直写/版本恢复）都追加快照；author 区分
  user/agent。
- `agent/<note_id>.json`：**每笔记专属智能体状态**（2026-09-01 重构）：mode
  （ask/plan/authorize）、messages 对话历史（上限 200 截头）、pending_plan 待批复
  计划（状态机 pending→approved→executed / rejected + plan_text 全文）、working
  工作态。`note_id` 为空的仓库级对话存 `agent/_vault.json`。删除/归档笔记时同步
  清理（`delete_agent_history`）。
- `threads/`（遗留只读）：旧线程模型。首次加载某笔记历史时按消息
  `context.note_id` 一次性懒迁移进 `agent/<note_id>.json`（scope=vault 的无主消息
  不迁移，旧模式值经 `normalize_agent_mode` 映射），之后不再写入；
  `conversation://notes/<id>` 旧链接按缺失展示。

**写入契约**：乐观并发——`PUT /notes/{id}` 携带 `base_revision`，不匹配返回 409 +
服务器最新内容（前端弹「载入最新 / 覆盖保存」）。`write_note` 在 `file_lock` 内完成
校验→写正文→快照→bump revision→存索引。重命名笔记默认全仓库改写指向旧标题的
wiki 链接（被改笔记同样留修订快照，历史不跳号）。

**统一资源链接与标签**：`[[标题]]` / `[[标题|别名]]` 继续按标题解析（重名取最近更新），并支持稳定 ID：`note://<note_id>`、`conversation://session/<session_id>`、`conversation://notes/<thread_id>`。解析结果携带资源类型、状态（resolved/missing/deleted）和摘要；删除目标不改写历史正文。解析不到的 wiki 链接仍是关系图幽灵节点。链接/标签扫描前剔除代码块
与行内代码；标签另存索引元数据（每篇 ≤12 个）。反向链接、链接图、vault 概览由正文
现算（进程内 mtime 缓存）。关系图节点覆盖 笔记/被引用的普通会话/教材/幽灵（未解析
wiki 链接）：会话经正文 `conversation://session/...` 资源链接引入（未被引用的不进图），
教材从笔记 `source.textbook_ids` 派生 note→textbook 边（教材索引不可用时静默降级），
笔记助手线程不进图；笔记首页封面即该关系图的漂浮文字力导向渲染
（`TextForceGraph`，自研模拟，无图形库依赖），原统计/温故面板收入「温故面板」抽屉。

**内置模板（5 个 + 用户自定义 ≤20）**：错题修正（来源偏好：错题本/会话，含作答记录
注入）、知识点总结、学习温故（`review_enabled` 自动注册 M9 卡片）、章节笔记（教材
outline 来源）、对话总结。模板骨架注入 `notes_generator_system`；自定义模板存
`custom_templates`（`ct_` 前缀）。

**笔记智能体（`agents/notes_agent.py`）**：`NOTES_AGENT_MODE` 开关（默认开；off 时
CRUD/导出不受影响，SSE 返回降级错误事件）。两条 SSE 管线（事件词汇表沿用 chat 惯例：
run_start/step/thinking{is_delta,summary:false}/answer{is_delta}/retry/tool_start/
tool_result/note_updated/mode_changed/plan_card/run_end/error/done；事件只暴露阶段和
工具摘要，**原始思维链不落盘**——live 思考直播（`REASONING_LIVE_MAX_CHARS` 门控）
仅作为显示流发给前端，不进对话历史）：

- `POST /notes/generate`：**来源三形态**（`sources.source_mode`，缺省按旧字段推断）——
  sessions（教材/文件经 `material_sources` 从所选对话自动推导，无需另选工作区/教材）、
  workspace（`session_ids` 为限定子集，空 = 整个工作区；教材 = 工作区教材 + 所选对话
  额外引用）、textbooks（直接对教材写笔记）。管线 = 来源组装（会话优先 compaction
  摘要、教材 outline+概念、工作区 public_memory、错题本 `collect_error_notebook`；
  每类独立字符预算超限截断标注，教材图谱缺失降级标题行）→ **真实 RAG**
  （`_build_retrieval_corpus` 聚合三形态语料为 BM25 overlay + 向量 scoped stores，
  `notes_retrieval_queries` 一次小调用生成检索查询、失败降级确定性查询，
  `KnowledgeSearchTool` 检索——与 chat 同一条混合检索+证据门路径，step=retrieving，
  片段以[检索片段]并入[来源材料]，预算 `_RETRIEVAL_CHAR_BUDGET`）→
  `notes_generator_system` 流式生成（含仓库概览供 [[链接]]、用户补充要求最高优先；
  **多模态**：RAG 图表证据页快照 + 所选会话图片附件 ≤3 张，配置 MULTIMODAL 时切
  视觉通道，未配置静默降级纯文本）→ 剥开场白/代码围栏 → 存为 draft 笔记（source
  记录 source_mode/material_file_ids 溯源，供助手后续检索圈定范围、温故模板
  注册 M9 卡片）→ `note_created`/`done`。`sources_summary` 含 retrieved 计数。
- `POST /notes/chat/stream`：ReAct-lite（≤4 步），**每笔记专属 + 三模式工具矩阵**
  ——ask/plan 只读（notes_search/notes_read/knowledge_search，plan 也不能修改任何
  笔记、只能产出计划卡）；authorize 另有 `notes_write`，且工具实例绑定当前笔记
  （`allowed_note_id` 不匹配直接拒绝——不能新建笔记、不能修改其他笔记），并携带
  轮首版本快照 `base_revision` 做乐观并发：学生在智能体运行期间保存的编辑触发
  `StaleRevisionError` → 返回「先 notes_read 重读再重试」错误而不是静默覆盖。
  写入成功 emit `note_updated{note_id,title,content,revision,summary}`（前端热更新
  编辑器）。**knowledge_search 三模式统一装配**（只读，不改变模式边界）：语料 =
  当前笔记 source（生成时溯源）+ 笔记页上传附件（`notes/<sid>/uploads/`，向量
  scope `notes:<sid>`），两者皆空时回退学生可选教材（封顶 12 个文件防大库拖慢）。
  越权调用返回 error ToolResult。**计划批复状态机**（结构性消灭重复批复）：plan
  模式回复尾部的 ```json 计划卡（`{"title","steps":[{title,detail}]}`，解析失败
  视为普通问答、不设 pending）→ `set_pending_plan(pending)` + `plan_card` 事件 →
  `action="approve_plan"` 仅当 status==pending 才放行：置 approved、**持久切换该笔记
  mode 为 authorize**（emit `mode_changed`）、按 `<approved_plan>` 圈定执行、完成后置
  executed；重复批复/无计划/驳回后批复均返回明确 error。`action="reject_plan"` 置
  rejected 并停留在 plan 模式（不发 LLM 请求）。对话历史按笔记隔离
  （`agent/<note_id>.json` 注入最近 12 条）；用户消息 `<user_input>` 定界；异常分支
  也完整收尾（error + 留痕 + run_end/done，前端 finally 统一刷新）。旧值
  suggest/collab/cowrite/auto 由 `normalize_agent_mode` 映射为 plan/plan/authorize/
  authorize；API 层枚举校验未知值 422（堵客户端自报 mode 越权）。
  `knowledge_search` 一旦参与修改，会把证据标准化为去重的简短 `> [知识卡]`（来源
  位置 + 1–2 句摘要 + 隐藏指纹元数据），禁止将完整 RAG 原文写入正文。
  **图片附件**：`POST /notes/upload`（图片 OCR/文档提取，存 notes 侧 uploads），
  请求带 `attachments` 时配置 MULTIMODAL 则切视觉通道（历史持久化仍纯文本，
  附件只记 id/filename 元数据），未配置降级用消息内 `<ocr_material>` OCR 文本。

提示词注册：`notes_assistant_system@1.3.0`（当前笔记专属助手契约 + 三模式边界 +
计划卡 JSON 格式 + knowledge_search/图片附件说明 + 防注入定界条款）、
`notes_generator_system@1.1.0`（生成规则 + 检索片段/图片同为事实依据）、
`notes_retrieval_queries@1.0.0`（检索查询规划器：输入模板/要求/材料名 → JSON 查询
数组），文本改动需 bump 版本。

**M9 深度同步（学习温故）**：`LearningOrchestrationService` 新增
`upsert_review_card`（幂等、重命名只更新 concept_name 不重置调度）/
`submit_review`（canonical SM-2 + `srs_review` 事件落 `.orchestration_events.jsonl`）/
`remove_review_card`。笔记卡 `concept_id="note:<note_id>"`、`concept_name=笔记标题`，
进入 `review_queue` 后自然流入 daily_composer/今日任务（M9 消费端零改动，`/plan` 页
可见）——这是「深度同步」的支点。笔记侧 `POST /notes/{id}/review {quality}`（记得 5/
模糊 3/忘了 1）走 `submit_review` 并把调度字段镜像回索引；`GET /notes/reviews/due`
只回 `note:` 前缀到期卡并 join 笔记元数据。删除（trash 归档）摘卡、恢复重注册。

**回收站**：`notes_note` bundle（`archive_note`：note.json + content.md + revisions/
快照，温故卡与该笔记专属智能体状态随归档摘除；restore 反向重建；purge 残留清理幂等）。

**API**（`/api/v1/notes`）：`GET /vault`（含 stats：链接数/未解析/到期温故）、
`/search`、`/graph`、`/reviews/due`；**每笔记智能体** `GET/PATCH/DELETE
/{id}/agent`（模式/历史/待批复计划，PATCH 枚举校验 422）；notes CRUD（PUT 409 契约、
PATCH 改名/移动/标签/温故开关、DELETE 经 trash）；`/{id}/revisions`（列表/读取/恢复）；
`/{id}/review`；`/{id}/export`（.md frontmatter）与 `/export?folder_id=`（zip）；
folders（含 parent_id、安全上移删除、循环校验）/templates CRUD；`/bulk/move`、
`/bulk/delete`（统一进回收站）；`/generate`、`/chat/stream` 两个 SSE（mode/action
枚举校验，旧值映射放行）。旧 `/thread*`、`/suggestions*` 路由已随协作模式移除。
身份一律 `resolve_student_id`，越权 404。

**前端（`/notes/[[...noteId]]`）**：三栏布局，两侧栏（笔记栏/AI 面板）均可折叠
（PanelToggleButton 嵌中栏头部 + 面板自带 chevron，无浮动按钮）且边缘可拖宽
（PanelResizer，宽度钳制并持久化 localStorage；AI 面板开合不持久化——每次进页
默认打开，仅会话内手动折叠）。左栏递归文件夹树（折叠、子文件夹、笔记拖拽目标高亮）/标签/搜索/多选列表（Shift 连选、当前节点全选、批量移动/回收站删除，Pager
8/页）/新建下拉（空白+模板）/AI 生成/导出；中栏 NoteToolbar **单行**（标题
InlineEdit、保存状态徽章、视图三态切换、图谱/专注入口、齿轮设置菜单收纳文件夹/标签/
温故/历史/导出/删除）+ 编辑器（**自研 textarea**：19 键工具栏、Ctrl+B/I/K/S、`[[
标题自动补全（无匹配可新建）、execCommand 差分插入保 undo 栈、编辑/预览/分屏三态+
滚动同步；**编辑模式单栏满宽**，与预览模式一致不分屏）+ 预览（复用 chat Markdown
渲染器，GFM+KaTeX；wikilink/`#标签` 预处理为 note:// 内部链接，反向链接/出链面板）+
专注模式（fixed 覆盖层全屏编辑，Esc 退出）；编辑器另有统一资源搜索选择器。右栏 AI
面板（2026-09-01 重构为**每笔记专属助手**：复用原生 chat 消息组件——用户气泡
`bg-accent-soft`、助手左竖线无气泡 + chat-prose 排版 + StreamingMarkdown 流式光标 +
ThinkingBlock 深度思考折叠（live 思考直播实时展开）+ NotesToolCard 工具活动卡 + hover
复制；**切换笔记即切换智能体**（历史/模式/待批复计划各自独立，从旧线程一次性懒迁移），
无线程下拉；头部为当前笔记标题 + 三模式选择器（问答/计划/授权，PATCH /{id}/agent
持久化，旧值自动映射）+ 清空对话；**计划卡**（PlanCard：标题 + 编号步骤 + 状态徽标
待批复/已执行/已驳回，数据来自服务端 pending_plan 而非启发式猜测）pending 时输入框
上方出现一次「批准并执行 / 拒绝」批复条，批准后服务端 mode_changed 自动把选择器切到
授权模式并流式执行，结构性消灭重复批复；**note_updated 自动刷新**：编辑器非脏态热
替换，脏态改为顶部横幅「助手已更新本笔记 · 载入最新/保留我的」由用户决断，修复旧版
静默跳过 + 随后 409 冲突的问题；**图片上传**（扫图按钮 → /notes/upload → OCR 预览
卡，发送时 `<ocr_material>` 前缀 + attachments 视觉通道，≤3 张））。生成向导**来源
三形态**（从对话生成/从工作区生成——工作区单选 + 可选限定子集对话，不选=整区/从教材
生成 三张类型卡，错题本为附加项，确认步展示检索提示）。未选笔记时中栏为 NotesHome
（统计卡/今日到期温故三档反馈/未解析链接/最近编辑）+ 微型头部图谱切换（统一展示
笔记/普通对话/失效与幽灵节点；SVG 支持拖拽平移、滚轮/按钮缩放、重置/适应、悬停详情
和资源点击跳转，适配深浅色）。自动保存 dirty 后 800ms 防抖 + Ctrl+S；`notes:` 前缀的
所有请求经 `apiFetch`。导航：学习组 `/notes`（`nav.notes`，NotebookPen 图标，模块徽章
MN）。

**测试**：`test_notes.py`（CRUD/并发 409/修订/wikilink 改写/图/导出/每笔记智能体端点
与模式校验/trash 往返/双用户隔离/路径穿越/附件上传路由）、`test_notes_agent.py`
（fake LLM：生成管线/三形态来源与真实 RAG 命中/检索查询降级/工作区整区展开/三模式
工具循环与越权拒绝——authorize 跨笔记与新建拒绝/计划卡解析与 pending 状态机/批复
仅一次 + mode_changed 自动切授权/驳回路径/重复批复拒绝/每笔记历史隔离/旧线程懒迁移/
写入冲突重试提示/live 思考不落盘/异常分支完整收尾/knowledge_search 在 ask 模式可用/
附件降级/旧值归一化/降级 SSE/API 面）、`test_notes_m9_sync.py`（upsert 幂等/SM-2
规则/生命周期/到期 join）。

---

## 智能化统一改造（2026-08-25 收口）

本节是 L1/L2/L3 三层统一改造的**维护总纲**：改造涉及的所有机制在此一页可查（各模块细节仍以对应 §为准）。核心宗旨：**智能**（判断交给 LLM，确定性代码只做兜底且兜底不劣于现状；无值域白名单、无机械阶梯）、**可用**（每个功能有真实消费点）、**兼容**（新字段全带默认值，旧数据双向兼容）、**统一**（单一真相源 + 同一档案快照 + 页面去重）。

### A. 三层统一架构总览

| 层 | 职责 | 组成 |
|----|------|------|
| **L1 统一学习者档案层** | 单一真相源：所有关于「这个学生」的事实只记一遍 | 活动聚合（activity_aggregator）、学习证据账本（learning_evidence journal）、目标链（orchestration goal + 前置闭包）、M2 画像、M8 交互画像、教学日志（teaching_log） |
| **L2 统一智能决策层** | 所有 LLM 决策读同一档案快照，输出开放文本 | M3 compose、M4 出题/判分、M7 advisor、M9 周规划/日编排、对话内出题工具（quiz/fit_quiz）；布鲁姆六层级为共享认知词汇 |
| **L3 统一呈现层** | 页面按数据归属重组，无重复语义 | /dashboard（总览）、/knowledge（图谱+教学计划，/plan 已并入）、/assessment、/memory（记忆总览+学习账本）、/profile（画像镜像 M9 + 字段级来源）、/insights（M7+教学指导）、/docs（使用文档） |

数据流方向永远单向：**写入只在数据属主模块发生 → L1 聚合器只读 → L2 决策器读快照产 prompt 素材 → L3 页面只读展示**。唯一被批准的跨模块写点是 M7 的部署动作（PATCH applied → teaching_engine/guidance_store，人工发起，见 §16）。

### B. 记忆层语义与维护机制（维护手册）

系统共有四类**活的**记忆 + 一类审计遗留，各自语义/存储/注入边界如下：

| 记忆层 | 语义（记什么） | 存储 | 注入边界（谁读它） | 压缩与维护 |
|--------|--------------|------|------------------|-----------|
| **提示词记忆**（M6 prompt_memory） | 跨对话画像：learning_summary / tone_preference / explanation_preference 三字段 + 按会话记的贡献流水（current_level 水平归纳已删除，字段仅存恒空兼容位 current_level_retired） | `students/<id>.prompt_memory.json`（+ `.prompt_memory_pref.json` 窗口偏好，5–30 会话，默认 15） | 每轮对话注入 supervisor prompt（M6 build_directive；有界，不随历史膨胀） | LLM 压缩：`maybe_compact_core` 把窗口外贡献折叠进存活字段，**压缩代数**（compaction_generation）+时间戳公开可查（/memory 页）；并发写以代数校验防覆盖 |
| **工作区记忆** | 每工作区的公共记忆摘要（7 字段展开 + 更新时间） | `chat_history/workspaces/<ws>.json` 内 | 工作区对话注入；/memory 页只读展示 | 工作区级维护（随工作区更新/删除） |
| **学习账本**（M2 统一学习证据 journal） | 独立于对话的学习结果全量：任务材料/作答/判分/语义解释/帮助条件/来源归属 | `students/<id>.learning_evidence.jsonl` | **不注入对话提示词**（明示边界）；消费方=概念证据投影、总览最近作答、/memory 学习档案区、错题本、最近习题 | append-only 事务 + 只读投影端点（/learner-evaluation/*）；永不被压缩改写 |
| **程序性记忆**（M6 procedural + habit_patterns） | 策略成功率滑窗（per-student）；学习习惯聚合（来自 4 种编排事件：habit_milestone / task_batch_completed / milestone_completed / goal_progress） | `students/<id>.procedural.json` / `.habit_patterns.json` | procedural→M6 检索/统计；habit_patterns→M9 日编排上下文（`daily_composer.habit_context`，C9 转正）+ /memory 审计 | 有界聚合（evidence_count/confidence 递增），从不失效删除 |
| **旧情景/语义**（审计遗留） | 迁移前的 episodes / semantic 事实 | `.episodes.jsonl`（+archive）/ `.semantic.json` | 仅两处只读回退：活动聚合空时兜底（标来源 legacy）、/memory 历史审计 Tab；**写侧已删除**（C1/C2/C3） | 磁盘文件**永不删除**（用户数据）；压缩归档机制保留（overflow→archive） |

### C. L1 档案层数据源契约

- **活动聚合**（`core/activity_aggregator.py`，零 LLM、never raises）：五源按**本地日**并集——学习证据 journal（仅已判分作答）∪ teaching_log ∪ orchestration_events ∪ ux_events ∪ eval_traces → `active_days / current_streak / longest_streak / 每日分类计数（作答/讲解/复习）`。五源全空时回退旧 episodes 并标注来源（`source: legacy_episodes`）。消费方：TopBar 连续天数、/profile 激励卡、M8 motivation/greeting、`GET /ux/activity?days=`。streak 消费点共 4 处（TopBar / StatCards / MotivationCard / HabitCard）。
- **概念证据状态**（评价投影）：per-concept 证据状态 supported_in_scope/emerging/fragile/conflicting/not_observed（目标链差距分母据此计算）；旧布鲁姆正确率聚合档案（bloom_profile）已删除，认知层级词汇（`core/bloom.py`）仍作为 L2 共享词汇用于任务设计。
- **目标链**：goal 绑定 `target_concept_ids`（图谱概念 id）时，`prerequisite_closure` 沿 PREREQUISITE 边 BFS 下探（剔评价状态 supported_in_scope 的已支持概念、上限 120）∪ 目标自身未支持 → required_skills（进度分母）；差距按最长路径深度分层（`layer=1` 现在就能学）；`estimate_schedule`（5 概念/周 vs 截止周数 → tight/ok/loose，纯函数零 LLM）。无绑定→学科兜底（学科为空则全图谱）。深链 `/knowledge?concept=<节点id>` 一次性定位（切范围+下钻+高亮+开抽屉）。

### D. 布鲁姆六层级 = 共享认知词汇（不是核对清单）

Anderson 修订版六层级（记忆/理解/应用/分析/评价/创造）只作为 **prompt 素材与作答标签**进入各 LLM 决策；层级选择永远由 LLM 综合档案判断（可跳级/混层/回退，无「答对升级答错降级」阶梯）：

| 决策点 | 用法 | 降级路径 |
|--------|------|---------|
| M4 出题（generator） | `AssessmentGoal.bloom_focus`（可空）+ 认知档案上下文进 _constraint_block；题目 JSON 携带 `bloom_level` 回标 | LLM 不可用→出题器自选并标注；focus 为空=auto |
| M4 判分（evaluator） | 批改要点末句以自然语言点到作答体现的认知层级（明确要求**不罗列术语贴标签**） | 纯 prompt 措辞，无新字段；LLM 失败走原判分 |
| M4 报告（adaptive_test.summary） | `bloom` 分桶统计 {层级: {asked, correct, partial, wrong}} | 未标层级的旧题不入桶 |
| 对话内出题（tools/quiz、fit_quiz） | `guidance_block` 注入同一档案自适应选层；`bloom_level` 经 quiz_verify→record_recent_quiz→学习账本落标（工具层无学生身份，标签由链路自动携带） | 同 M4 |
| M9 日编排 | `context_line`（概念级弱项 + 全局弱项，≤300 字）进 compose prompt | 档案为空→不注入，行为同旧 |
| 画像页 | 认知层级弱项徽标行（读同一档案） | 无弱项→整行隐藏 |

数据闭环：出题带标 → 作答评分 → 账本落标（`bloom_level` ≤24 字符）→ 档案聚合 → 下一次决策更准。

### E. M7 教学指导真闭环（batch 5）

链路：**advisor（每 15 trace 生成 1 条开放式提案）→ 人工批准 → 应用即部署（写入 M3 guidance_store）→ compose 每轮消费（focus/avoid 前插 + rationale 归因）→ 吊销即回滚**。细节见 §16（已更新）。要点：指导是 LLM 写的开放文本，compose 只做路由（适用范围包含匹配 + 渲染预算），不做解释；`/insights` 页有「生效中的教学指导」面板（影响轮数回显 + 吊销按钮）。

### F. 页面归属地图（L3 收口后）

| 页面 | 数据归属 | 备注 |
|------|---------|------|
| /dashboard | M2 总览 + L1 活动 + 最近作答 | EvalSummaryCard 已换最近作答卡；GreetingBar 徽章已删 |
| /knowledge | M5 图谱 + M2 证据投影着色 + M3 路径条 + **M3 教学计划区**（原 /plan 页迁入：模式状态机/难度表盘/完整路径两栏/教学日志） | `/plan` 保留 redirect（外链深链不断）；导航已减一项 |
| /assessment | M4 CAT + 证据投影/最近习题 | 概念选择共用谱系选择器；「薄弱概念加入周计划」一键 |
| /memory | M6 四类记忆总览 + L1 学习档案区 + 历史审计 Tab | 程序性保留；情景/语义合并只读审计 |
| /profile | M2 学术 + M8 交互 + 激励 + **M9 目标镜像**（M2 goals 写侧保留属对话冻结区，读侧由 M9 取代——C12） | 每卡「数据从哪来」一行 + 字段级来源标注（M8 反馈推断等） |
| /insights | M7 评估 + 提案人工门 + 生效指导 | 文案已修：每 15 轮生成 1 条（C11） |
| /docs | 使用文档（chat_history/settings/usage_docs.json） | 全员读（GET 公开）；管理员页内编辑（textarea+实时预览+PUT require_admin）；TopBar BookOpen 入口 |
| /orchestration | M9 多目标/周计划 | 目标链/差距分层/预计行 |

### G. 遗留物处置台账（C1–C16 终局）

| 编号 | 内容 | 终局 | 批次 |
|------|------|------|------|
| C1 | 旧 episodes 写侧（episodic.append_episode 包装层） | ✅ 已删（store 原语保留：审计数据播种/读回用） | 6 |
| C2 | memory/consolidation.py 旧语义巩固 | ✅ 整文件删除（含 store 的 consolidation 状态读写；旧文件未知键容忍） | 6 |
| C3 | MemoryService.maybe_consolidate 桩 | ✅ 已删（零调用方；API 一直只读） | 6 |
| C4 | 记忆中心情景/语义两 Tab | ✅ 合并「历史审计（只读）」尾 Tab + 空态文案修正 | 3 |
| C5 | 旧数据文件 .episodes/.semantic | 📌 磁盘永久保留（用户数据）；终局=只读审计 | 全程 |
| C6 | streak/活动四类死源读取 | ✅ 批1 换聚合源；`_active_days_from_episodes`/旧签名/episodes 直读全部删除 | 1+6 |
| C7 | M9 模拟器全家（端点+327 行模块+manager.simulate+_episodes_safe+前端死函数+测试） | ✅ 全部删除 | 6 |
| C8 | evaluation/experiment.py + report.active_experiments + 前端字段 | ✅ 全部删除 | 5 |
| C9 | M9 `_habit_patterns_safe` 写而不读 | ✅ 转正为公共访问器 `habit_patterns` + `daily_composer.habit_context` 真实消费 + 单测（写侧 4 种事件保留） | 6 |
| C10 | DEFAULT_GRADE 配置（零消费） | ✅ 已删（config/.env.example/.env；学段来自用户画像/自动识别） | 6 |
| C11 | insights「每 15 轮生成一批提案」夸大文案 | ✅ 修为「每 15 轮生成 1 条教学指导提案」（中英） | 5 |
| C12 | M2 profile.goals 展示 | ✅ 读侧由 M9 目标镜像取代；**写侧保留不动**（goal_set 检测点在 supervisor.py 冻结区） | 5 |
| C13 | /plan 旧路由 | ✅ 并入 /knowledge「教学计划」区；/plan 保留 redirect；导航入口与 nav.plan 词条删除 | 5 |
| C14 | dashboard GreetingBar 徽章 / EvalSummaryCard | ✅ 徽章删除；摘要卡换「最近作答卡」 | 1 |
| C15 | chat_agent `_legacy_prompt_memory_block` | 📌 **不动**（对话模块冻结区红线；DESIGN 记录在案） | — |
| C16 | error_notebook 的 quiz_history 兜底链 | 📌 保留（活数据兼容路径，有真实旧数据消费） | — |

冻结区红线（全程遵守）：教材库/RAG/知识检索/笔记/对话链路（前端 chat 页面组件；backend chat_agent.py / supervisor.py / executor.py / core/session.py / api/v1/chat.py / api/v1/quiz.py）零改动——唯一例外是出题工具 tools/quiz.py、tools/fit_quiz.py 的层级指令（出题功能本体）与 supervisor 既有的 `adapt(ctx, student_id=…)` 调用点（原本就传 student_id，M7 指导消费在其内部完成）。M4 CAT 机制（停止规则/难度轴/证据纪律/三级判分→统一学习证据）不动。

**测试基线**：全量 1402 项通过（改造前 ~1371）；每批均过 tsc/eslint/next build --webpack/git diff --check。

## P8 文本层质量分级、保真质检与课文结构（2026-08-26）

背景取证（27 卷公共教材）：仅按「每页 <20 字符」判扫描会漏掉**稠密乱码页**——
人教/同济定制数学字体无 ToUnicode 映射，文本层每页数百乱码字符（全角公式/
PUA 音标/犃犅 型斜体替换/ꎬ 彝文区逗号），字符量达标却不可用。结果 8/27 卷
（线性代数、必修2、选必3、英语词表卷）从未 OCR 即进入 RAG 索引；而同系列
扫描卷（必修1/选必1/2）全量 OCR 后质量良好——**问题在路由判定，不在管线**。

### P8.1 质量分级器（`core/text_quality.py`）

纯函数、无 LLM：`classify_page(text) -> good/corrupt/sparse/empty`。corrupt 双
门槛 = 乱码字符数 ≥8 **且** 占比 ≥0.2%（取证坏卷 0.6%+，好卷 ≈0，排版偶用
全角数字不误判）；辅助信号「孤立短行占比 ≥0.35 且乱码 ≥3」（矩阵逐字炸竖排），
单独短行（诗歌/LaTeX 定界符）不构成 corrupt。乱码字符集 = PUA + U+FFFD +
意外文字系统（彝文/埃塞/切罗基/Saurashtra）+ 全角字母数字（标点除外）+ 已知
字体替换字集（犃犅犿狀…，按需扩充）。存量验证（只读扫描）：线性代数 96.2%
corrupt、必修2 93.6%、选必3 93.5%、英语词表卷 10-11%（正是词表页）；12 本
OCR 好卷乱码率全部 0.0000，零误报。

### P8.2 路由接入（OCR 触发 = 稀疏 ∪ 乱码 ∪ 空）

`pdf_ocr.pages_needing_ocr()`（新）取代各处的仅稀疏判定：上传决策
（`api/v1/textbook.py`）、OCR 调度器目标页（`textbook_ocr.py`）、构建预算
（`textbook_builder._volume_spec`）、同步回退门槛（`file_parser`）。
`ocr_pdf_pages_mixed(_sync)` 增加 `ocr_indices` 参数（外部 verdict 路由传入）；
stats 形状不变（"sparse" 语义 = 本轮目标页数）。`sparse_page_indices` 保留向后
兼容。**良好文本层页绝不 OCR 降质**的既有契约不变。

### P8.3 质量报告 + quality_ocr 手动重建

- `GET /textbooks/{id}/quality`（只读、零 OCR 成本）：逐卷 verdict 统计 +
  乱码率 + staging 状态；`recommended_mode = quality_ocr`（corrupt ≥10%）或
  `rag_graph`。
- `POST /textbooks/{id}/rebuild_graph mode=quality_ocr`（bulk 同）：清
  `ocr_state` 后按当前文本 verdict 逐页择优重建——稀疏∪乱码页 OCR，良好页
  保留；与 full_ocr（整本重试）互补。触发权在用户（不自动烧 OCR API）。

### P8.4 staging 保真质检（`rag_index`）

`_validate_staged_chunks` 增加 `text_quality` 摘要：corrupt 页占非空页 ≥10%
→ `staging_quality.status="failed_garble"`。**仍发布索引**（降级可检索优于
不可用），由质量报告显形 + 手动 quality_ocr 兜底。旧行为（只查 token 数，
乱码全 passed）是 8 坏卷畅通无阻的放大器。

### P8.5 表格收割反伪造门槛（`figure_harvest`）

`_rows_look_like_table`：≥2 行、≥2 列、≥4 个非空单元格、≥2 行有多列内容，
且任意两列 **≥2 行内容完全一致 → 判伪造丢弃**（取证：选必3 一卷 203 张
「问题框复制进两列」的假 markdown 表）。

### P8.6 课文结构与上下文注入（chunker `structured-v2.2`）

- **断行修复**（确定性预处理）：孤立小写字母行 = 脚注上标锚点——前行尾 +
  后行头均 CJK 且后行 ≤2 字符（「橘子洲/b头。」）→ 合并保词；其余锚点行丢弃
  （大写单字母行不丢：可能是答案键）。跨页重复运行页眉（≥3 页页首/页尾、
  ≤30 字符）整行剥离（取证：「语文 必修上册」52 处污染）。
- **注释/词表独立块**：`annotation`（①…：… /〔注〕）、`vocabulary`（音标/
  词表行）作为 block_types，同种类连续行合块、不被后续正文打包稀释。信号
  在 NFKC **前**抓取（① 经 NFKC 变 "1" 会与数字标题混淆）。
- **课题父文档锚点**：课题标题（「1 春」「第10课 背影」，数字无小数点）打
  `is_lesson`，其后同课文 chunk 带 `lesson` 字段（章级标题重置）——父子
  chunk 分层的地基（parent_id/prev_id/next_id 既有）。
- **检索面包屑**（`retriever.retrievable_text`，Anthropic contextual-retrieval
  的零 LLM 版）：索引 token = 「书名 · 课题 · 章节路径 · 印刷页码」+ 正文，
  BM25 tokenize 与（未来激活的）embedding 同一入口；展示文本不变。修复
  「《荷塘月色》讲了什么」类课题查询的词面覆盖缺失（正文 chunk 原先对课题
  零覆盖 → no_absolute_evidence 假阴性）。`tool_context` 压缩投影补
  `printed_page`（教材第 N 页优先于 PDF 第 M 页）。
- schema 升 `structured-v2.2`：`rag_index._stage_file_index` 按 schema 不匹配
  自动重建既有卷的 chunk（免迁移）。

### P8.6.1 独立本地语义向量轨

`EMBEDDING_PROVIDER=local` 使用部署方自备的离线本地模型（仓库不内置默认模型）；`openai` 保留兼容端点，`off`
（默认）为纯 BM25。教材 BM25 发布后，向量构建进入单槽后台队列并将状态从
`bm25_ready` 更新为 `ready`；失败维持 BM25。collection 按模型指纹隔离，公共
教材通过 `knowledge/public_vector_artifacts/` 的校验分片导入部署本地 Chroma。
完整部署/发布契约见 `docs/LOCAL_SEMANTIC_RAG.md`。

## P9 检索运行时：查询核、置信度校准、分级证据与上下文重建（2026-08-26）

3252512295 账号 8 轮对话取证结论：**BM25 召回侧全部命中正确 chunk**（多变体
+ question_core 归一），问题全在门控与注入——置信度与真实相关性零相关
（0.901 给《套中人》、0.602 给真命中）、库里有全文却 NOT_FOUND（48 候选
全灭）、注入的是 250-500 字符句窗孤岛（6 条证据 2000 字符预算只装 3-4 条，
「式（8.50）」被切成「50)所表示的…」）。P9 修检索运行时，不动解析链路。

### P9.1 查询核提取级联（`evidence_gate.effective_query`）

旧 `_core_query` 正则把「X讲了什么」的内容核吃成「了什」「了》」——垃圾
词项让含「什么」的任意段落拿 0.9 置信、空词项让全部候选
no_absolute_evidence。新级联：动词尾捕获（是否讲到X/讲了X吗→X，剥首尾
虚词）→ 问句内容词核 → 原查询，**每级过 `_has_content` 守卫（≥2 个非停用
内容字），词项集永不坍缩**。配套：`对/在` 移出 bigram 边缘停用集（对数/
对称/对应是关键术语，旧规则整个丢弃「对数」导致向量块压过对数块），
`normalize_query` 空结果兜底折叠 bigram。

### P9.2 置信度重校准 + title_match（`apply_evidence_gate`）

- 新增 **title_match**：查询核（折叠标点）与书名/章/节/课题/section_path
  的契合度。整串命中 → 独立 0.5 档（课题类查询最强词面证据，词本体正文零
  命中也靠它进入候选）；bigram 部分命中 → 0.2 档。
- 公式重标定（纯 BM25 诚实刻度）：`lexical*0.45 + bm25_rel*0.2 +
  title_signal + vector*0.1 + intent/phrase bonus − penalty`；`bm25_rel`
  用池内相对分（绝对饱和归一对池规模敏感）。绝对证据测试扩展为
  `lexical≤0 且 title_match≤0` 才 no_absolute_evidence。
- `strong_primary` 豁免并入 title 整串命中；紧凑目录行/单行目录项 chunk
  （v2.1 把目录拆成「N 篇名/作者」单块）补 toc 标记。

### P9.3 分级证据（FOUND/PARTIAL/NOT_FOUND）

`GateResult.tier`：没有达标项但存在弱信号（词项/标题部分命中、置信
≥0.05）→ **partial**，返回低置信项（`confidence_tier=low`）而非 NOT_FOUND
——「检索器没找到足够好的证据」≠「知识库里没有」。MMR 淘汰后不足额用弱
信号池补位。工具输出带档位（高≥0.75/中≥0.45/低）与 partial 前缀；
NOT_FOUND 文案附已尝试核心词，允许模型换篇名/概念名重试一次（registry
tutor_system v2.7.0 同步：置信档位使用指引 + knowledge_read 指引）。

### P9.4 上下文重建（反碎片化，`core/evidence_context.py`）

- **摘录 v2**：公式感知切句（ASCII `.!?` 前后均非数字才切——「式（8.50）」
  「p.280」不再断开）、定义句居中（是指/称为/定理/公式…）、上限 500→900
  （`RAG_EVIDENCE_EXCERPT_CHARS`）、窗口下限 250→400。
- **课文合并**：同 (file_id, lesson) 的选中块（或课题标题命中的单块）合并
  为「课文《X》节选（教材第a–b页）」，上限 1600 字符——消费 v2.2 写入但
  此前零消费的 lesson 元数据。
- **邻块扩展**：摘录 <350 字符时按 prev/next_id 取邻块头部 200 字符
  （消费零消费的链式元数据）。
- **注入投影**：整包预算 2000→6000（`TOOL_CONTEXT_CURRENT_MAX_CHARS`）；
  溢出不再静默 break，降为一行指针「另有N条未展开：来源·页码·chunk」。

### P9.5 knowledge_read 工具（按指针取原文）

新工具注册于 `_build_tools`（与 knowledge_search 同一授权 store 构造）：
按 chunk 序号/PDF 页码读完整原文 + 相邻片段（span=current/prev/next/both，
chars≤4000），纯读取零 LLM、乱码片段附警示。模型看到证据卡 chunk 指针即可
深读，解决「只有一个小部分扔进上下文」的读取侧。

### P9.6 触发统一（`agents/material_signals.py`）

三套互不一致的触发词表统一到共享模块；新增书名号信号《…》（≥2 字）：
「《荷塘月色讲》」7 字短句过不了长度门槛的双重否决（preresearch ≥8 字 +
skill 剪枝）修复——`is_content_question` 书名号即 True、长度门槛 8→6、
`decision.material_grounding_required` 书名号（有资料时）强制保留检索技能、
chat_agent intent 分类同源。M5 知识指令旁路（ContentResolver 直消费未过滤
命中）经 `_GatedSearchStore` 适配器过同一证据门。

### P9.7 乱码检索期排除 + 索引准入门

- 运行时：`apply_evidence_gate` 对候选跑 `text_garble_ratio` ≥0.05 →
  drop("garble_text_layer")，绝不让 mojibake 注入上下文；全灭且主因乱码 →
  NOT_FOUND 附「文本层疑似乱码，建议重建索引」指引。
- 构建时：structured chunker 对乱码 chunk 置 `garble_excluded` 且不生成
  BM25 token（chunk 保留在存储供 knowledge_read/审计）；`staging_quality`
  记 `excluded_by_garble` 计数；vector 侧同样跳过。

### P9.8 验证

- 新增测试：`test_evidence_query_core`（8 取证问句核提取回归）、
  `test_evidence_gate_tiers`（合成迷你语料校准：套中人 vs 拿来主义、词本体
  title_match、洛伦兹定义>应用、乱码排除、partial 分级、公式切句）、
  `test_evidence_context_recon`（课文合并/邻块扩展/溢出指针）、
  `test_knowledge_read`、`test_material_trigger_signals`（书名号触发）。
- 真实库只读回放（`backend/scripts/replay_knowledge_queries.py`）：
  拿来主义 top-1=课文相关块（套中人消失）、荷塘月色 found（不再
  NOT_FOUND）、我与地坛课文本体 top-1（0.437，旧为活动框 0.602）、
  乱码卷候选被排除计数。词本体/课题合并的完全生效依赖既有卷按
  structured-v2.2+ 重建（lesson 元数据在 v2.1 索引中不存在）。

---

## P10 电话式语音对话：浏览器 Speech Recognition + MeloTTS（2026-08-30）

### P10.1 形态与边界

- **唯一输入路径**：push-to-talk 按住时由浏览器原生
  `SpeechRecognition` / `webkitSpeechRecognition` 识别，松手后只发送最终文本；
  浏览器不支持该 API 时显示明确错误，不启动服务器识别回退。
- **后端边界**：后端不接收电话输入 PCM，不安装、不加载、不启动任何 STT 引擎；
  WebSocket 只接收 `utterance_end.text`。语音文本进入现有 `run_turn`，与普通聊天
  共用会话、记忆、RAG、工具和持久化逻辑。
- **输出路径**：回答按子句级切片（`take_speech_cuts`），MeloTTS sidecar 逐片合成
  WAV，后端转 PCM16 流水线下发；前端按 `tts_start` 携带的采样率顺序播放（FIFO
  队列吸收提前到达的音频段），并保留停止播报能力。表格不逐格朗读：口播一句
  引导语，整块 markdown 经 `board_table` 即时完整呈现在黑板整版驻留至少 7 秒
  （之后继续驻留，直到新公式/新表格需要板面才清空），语音合成流水线
  不停顿（见 P10.2）。语速默认 0.9，个人可在设置页调整（见 P10.2）。
- **隐私/许可边界**：浏览器识别可能调用浏览器厂商在线服务。该平台 API 和厂商
  服务不是 Edu_Agent 的 MIT 发行物，商业、隐私、地域和可用性条款由实际浏览器
  厂商决定；详细组件许可证见 `docs/VOICE_LICENSES.md`。

### P10.2 后端语音模块

- `backend/app/api/v1/voice.py`：一次性 ticket、WebSocket 会话绑定、浏览器最终文本
  事件、`stt_start` / `stt_result` / `answer_delta` / 工具进度 / TTS / `turn_end`。
  二进制上行帧返回 `binary_audio_unsupported`，不会缓存、转码或触发 STT。
- **朗读语速**（2026-08-31）：实例默认 `VOICE_TTS_SPEED=0.9`（略慢于原速）；个人
  可在设置页（profile 页 AccountCard 滑杆，0.5–1.5）调整 `user.profile.prefs.
  tts_speed`，经既有 `PUT /user/profile` 浅合并落盘。WS 建连时按身份查
  `identity.store.get_by_id` 解析（`_resolve_tts_speed`：夹取到 sidecar 合法区间
  0.5–2.0，非法值/游客回落实例默认），逐片 `synthesize(chunk, speed=…)` 覆盖；
  修改后下次拨号生效（每连接解析一次）。
- **表格改上黑板**（2026-08-31）：`take_speech_cuts` 把 markdown 表格当不可切区
  （行首 `|` 进入表格模式，首个非 `|` 起始行结束整块；前导正文独立成句，120
  硬上限不适用，表内 `$` 不翻转数学配对）。worker 取到表格块时先发
  `{"type":"board_table","markdown":…,"hold_ms":7000}`，再把口播替换为固定引导语
  （zh「请看这个表格。」/ en 英文句）走正常合成，随后继续消费队列——语音流水线
  不停顿，`answer_delta` 原样透传（聊天记录仍是完整表格）。该判断在合成失败
  熔断之前：sidecar 挂了黑板照常显示表格。`hold_ms` 是表格独占板面的驻留下限，
  窗口过后何时清空由前端决定（见 P10.5）。
- **合成流水线**（`voice.py` `_run_turn`）：切出的子句进入**无界**队列，轮次
  循环持续消费 LLM 生成器、`answer_delta` 实时下发，不再内联等待合成。队列
  刻意不做上界：曾有界队列（8 句）在慢 CPU 上把文字流和合成速率耦合，第 8 句
  之后 `answer_delta` 按单片合成时长成批冻结（实测整句切片时 ~30 s 一档），
  比串行版更卡；队列内容只有句子文本（受回答 max_tokens 封顶，几 KB），
  PCM 从不入队。单个 worker 任务从队列取子句、合成并顺序发送 `tts_start` +
  二进制 + `tts_end` 三帧；客户端播放第 N 片时 worker 已在合成第 N+1 片，
  片间不再有等合成的空隙。每片的纯 Python 后处理（WAV 解码、响度归一，
  数十万采样级）经 `asyncio.to_thread` 移出事件循环——否则每片都会把整个
  后端（所有用户的流）停摆数百毫秒。跨帧不加发送锁：uvicorn/websockets
  sans-io 栈上每次 send 是单事件循环步写完整帧（帧级并发安全），前端
  `tts_end` 本就是 no-op，`turn_end` 顺序由 done 后入队 sentinel 并 join
  worker 保证。单 worker 保证每轮同时最多一个在途 sidecar 请求（sidecar
  模型无并发保护）、`seq` 严格递增。失败策略不变：一次合成失败发送
  `tts_error`，本轮其余子句跳过合成仅保留文字，worker 继续排水清空队列。
- `backend/app/voice/base.py`：仅保留 TTS provider contract、`VoiceProviderError`
  和 `TTSResult`。
- `backend/app/voice/tts/`：`stub` 用于回归测试，`melo` 通过 localhost HTTP
  调用 sidecar；`sentences.py`、`speak_text.py`、`loudness.py` 和 `wav.py` 分别负责
  流式切句、Markdown/LaTeX 朗读清洗、响度归一和 sidecar WAV 解码。
- **公式朗读链路**（`speak_text.py` / `sentences.py` 协同）：
  `normalize_math_delimiters` 先把 `\(...\)` / `\[...\]` 统一折成 `$` / `$$`
  （与前端 markdown 管线对齐；流式增量中"开符号已到、闭符号未到"时同样折算，
  使未闭合公式像未闭合 `$$` 一样锁住切句缓冲，`\\[2mm]` 行距命令有反斜杠守卫
  不会被误认成开符号）；`_read_math` 分级映射——数集、SI 单位（`\text{m/s}` →
  米每秒、`\mathrm{kg}` → 千克）、无花括号形式（`\frac12`、`\vec L`、`\mathrm dt`）、
  `\frac`/`\sqrt`/`\boxed`/偏导数等带参结构（定点循环由内向外折叠）、区间/绝对值/
  ASCII 比较（`[-1,1]` → 闭区间、`\left|x\right|` → 绝对值、`\varepsilon>0` → 大于）、
  正负号上下标（`0^+`/`f'_+` → 正）、下标与底数直接连读（`W_0`/`W_{max}` →
  W0/Wmax，不读「下标」二字；正文裸下标同规则，残余孤立 `_` 静默删除；`\lim_`/
  `\sum_`/`\log_` 等结构化上下标仍走各自口语化规则）、二元/一元负号（`x^2-9` 减、`=-1` 负），最后
  未知命令保留字母念英文、已知命令在参数被截断时丢弃命令名；`_force_split`
  数学感知（不在 `$` 段内部下刀，硬上限 280 字兜住 sidecar 400 字限制），
  语音流水线用子句级 `take_speech_cuts`（弱标点 ≥24 字即切、无标点硬上限
  120 字），`voice.py` 的 speak worker 再把超 240 字的朗读文本按标点分块顺序
  合成（首块 `tts_start.text` 带原句供板书，后续块传空串不重复上板）。规则以
  真实会话语料回归（`test_voice.py`）。
- 服务器端语音识别包、输入 PCM 缓冲和旧繁简转换数据均已移除。

### P10.3 WebSocket 契约

浏览器先调用 `POST /api/v1/voice/ticket`，再用单次 `?ticket=` 建立 WebSocket；
非浏览器客户端也可以直接带 Authorization header。协议如下：

```text
C→S {"type":"start","session_id":string|null,"workspace_id":string|null,"lang":string}
S→C {"type":"session_bound","session_id":string}
C→S {"type":"utterance_end","text":string}
S→C {"type":"stt_start"}
S→C {"type":"stt_result","text":string}
S→C step / {"type":"tool_start","name":string} / {"type":"tool_result","result":object}
S→C {"type":"answer_delta","content":string} / {"type":"retry","attempt":number}
S→C {"type":"tts_start","seq":number,"text":string,"sample_rate":number}
S→C <binary PCM16> / {"type":"tts_end","seq":number}
S→C {"type":"board_table","markdown":string,"hold_ms":number}（表格不朗读时）
S→C {"type":"turn_end","session_id":string,"tts_ok":boolean}
C→S {"type":"end"}  S→C {"type":"bye"}
```

同一连接只允许一轮并行执行；重复提交返回 `busy`，空文本返回
`empty_transcript`，TTS sidecar 失败时保留文字回答并发送 `tts_error`。思考内容
不通过电话协议输出；`tool_start`/`tool_result` 完整透传（仅 name + 结果载荷），
通话中的消息流与文字轮一样渲染题目卡与知识检索命中来源卡。每次 `send` 写一个
完整帧（uvicorn/websockets sans-io
单事件循环步），文字流（`answer_delta`）与音频流（`tts_start`/二进制/
`tts_end`）相互独立、可能交错，但帧本身永不撕裂；前端 `tts_end` 为 no-op、
按到达顺序消费音频，`turn_end` 仍在最后一帧音频之后。`board_table` 独立于
音频流：前端收到后黑板进入整版表格模式（表格**即时完整渲染**，无书写动画），
驻留 `hold_ms`（下限 7 s）期间句子上板被抑制、音频播报照常；窗口过后表格
继续驻留、不会到点自动消失，只有当新公式需要板面（或新表格替换）时才清空，
板面随即交还给三块公式黑板。

**轮次在途时 `end` 走「静默写完」（drain）**：服务端立即停止合成（`audio_off`
后入队的句子只排水不合成、不发音频），但 LLM 文字流跑到自然完成并照常落盘，
客户端继续收 `answer_delta` 直到 `turn_end`，随后收尾协程发 `bye` 并以 1000
关闭连接；重复 `end` 幂等忽略。前端对应保持 WS 存活、隐藏通话 UI，把回答写完
再收起通话层；超时（90 s）/断网/服务端提前收线则兜底**提交半截回答**并解除
`streaming`——任何路径下聊天都不允许悬空在流式态（旧的立即取消行为曾把
`chat.streaming` 永久锁死为 true，输入框与电话按钮随之永久禁用）。

### P10.4 MeloTTS sidecar

`backend/voice_sidecar/` 是独立 FastAPI 进程，挂载固定 revision 的
`backend/vendor/MeloTTS`，调用 `TTS(language="ZH", device="cpu")`。接口为：

- `GET /health`：sidecar 健康检查；
- `POST /tts`，请求 `{ "text": string, "speed": number }`，返回 WAV(44.1 kHz)。

`deploy/install_voice.sh` 只准备 CPU-only PyTorch、当前中文 MeloTTS 直接运行依赖、
MeloTTS 源码和模型缓存，并执行一次中文 warmup。`melo_bootstrap.py` 对未启用的
非中文 cleaner/BERT backend 使用 fail-loud stubs，避免安装日/韩语言包和下载
法/西/日/韩模型；并按消息定向屏蔽固定版本依赖栈必发的两条 FutureWarning
（`resume_download`、`torch.nn.utils.weight_norm`）——它们恰在模型加载时打出，
会混进 start.sh 并行进行的前端构建输出、被误读成构建报错（非按类别一刀切，
新的弃用告警仍会正常出现）。主服务在
`VOICE_TTS_PROVIDER=melo` 时自动拉起 sidecar；主服务本身不包含 ML/STT 依赖。
安装和许可证边界分别见 `backend/voice_sidecar/requirements.txt` 与
根目录 `THIRD-PARTY-NOTICES.md`（许可全文在 `licenses/`）。

### P10.5 前端

`lib/voice/useVoiceCall.ts` 维护
`idle/connecting/ready/recording/recognizing/thinking/speaking/ended` 状态机：

- 识别参数固定为 `continuous=true`、`interimResults=true`、`maxAlternatives=1`，
  语言使用 `zh-CN` 或 `en-US`；只累积 `isFinal` 结果；
- 浏览器提前结束连续识别时，在按钮仍按住的情况下异步重启并保留已累积文本；
- 松手、识别错误、权限拒绝、空文本、重复 `onend`、挂断和组件卸载均幂等处理；
- 轮次在途时挂断进入收尾（drain）：音频立即停、通话 UI 隐藏但 WS 存活，
  `answer_delta` 继续写完；`turn_end` 落定后拆线，超时/断网/提前收线由
  `onTurnAborted` 兜底提交半截回答，`chat.streaming` 任何路径都会被清除；
- 输入侧不创建服务器输入音频采集链；播放侧仍
  使用 AudioContext 播放下行 TTS PCM；
- `VoiceCallLayer.tsx` 将转写和回答增量写入现有 chat store，板书只消费
  **块状公式段**：`markdown.tsx` 的 `displayMathSegments` 从句子原文提取
  `$$…$$`/`\[…\]` 完整段（含定界符），行内 `$…$`/`\(…\)` 不上板也不触发
  换板；句子文本随每帧 PCM 一起进播放 FIFO（`pcmMetaRef` 与音频队列严格
  平行，`tts_start` 到达只登记原文、不直接挂板——后端流水线刻意提前合成
  若干句，直接上板会比声音早好几句），`drainQueue` 出队、那一帧**真正
  开始发声**时才把所属句子暴露给黑板。黑板严格左右居中（max-w 760px）、
  固定占 3/7 页面高度，**三块等分黑板**（`flex-1 min-h-0` + 板内各自滚动
  ——flex 子项默认 `min-height:auto` 会让内容高的板挤占邻板，故 CSS 侧
  不再设 min-height），块式段从上往下写第一块空板，三块都满时擦掉写得
  最早的那块（key 即写入序）再写新的；`board_table` 到达时三块黑板全部
  作废、整版表格**即时完整渲染**
  （GFM 表格、无书写动画），驻留 `hold_ms`（下限 7 s）内新公式不上板，窗口
  过后表格继续驻留，直到下一个新公式（`clearBoardTable` 清空、effect 重跑
  完成写入）或新表格（直接替换并重置窗口）才让出板面；右上角（原电话入口
  位置）挂「手机模拟」（约 6.72rem × 14.4rem、等比 0.8×：空白头像 + 通话
  计时 + 声波状态 + 纯装饰导航图标，窄屏隐藏；黑板严格居中不预留，窄窗口
  下手机可能贴住板书右缘——已确认接受；挂断/停止播报在底部控制条），控制条
  紧贴输入框、下方无常驻说明文案；通话结束后刷新会话。

### P10.6 资源与部署

- 浏览器 STT 不占用服务器语音识别模型内存，也不需要服务器录音缓存；部署侧仅需
  MeloTTS sidecar 的 CPU venv 与 Hugging Face 模型缓存。
- 仓库只提交 sidecar 集成代码、固定依赖和许可证声明。`deploy/install_voice.sh`
  在部署时把固定 revision 的 MeloTTS 源码放入 `backend/vendor/`，把
  MeloTTS-Chinese/BERT 权重与 tokenizer 放入 `backend/models/voice/`，并把 venv
  放入 `backend/voice_sidecar/.venv/`；三者均 gitignored，不属于用户运行数据。
- 安装脚本预取已审计 revision 后切换 HF/Transformers offline 执行 warmup，避免模型
  `main` 漂移。部署时下载仅避免 Git 仓库直接携带模型；容器、VM 或离线包若包含
  下载结果，仍须保留模型卡、LICENSE/NOTICE 和实际 SBOM。
- `start.sh` 在 `VOICE_TTS_PROVIDER=melo` 时自动拉起 sidecar，并为端口回退、PID
  和健康检查保留现有逻辑；nginx 仍需为 `/api/v1/voice/` 透传 WebSocket Upgrade。
- 浏览器 `SpeechRecognition` 是厂商平台/服务边界，不是本项目 MIT 依赖；不能承诺
  永久免费或无条件商用。变更浏览器目标、MeloTTS revision、模型、语言、sidecar
  依赖或发布形态时，必须重做服务条款/许可证审计和发布 SBOM。

### P10.7 测试

`backend/tests/test_voice.py` 覆盖切句、朗读清洗、TTS WAV 解码、响度归一、ticket、
鉴权、会话所有权、会话持久化和 TTS fail-open；WebSocket 回归使用 stub TTS 与
canned `run_turn`，验证：

- status 固定返回 `stt=browser`；
- 无 PCM 的 `utterance_end.text` 能完成 `stt_result`、回答增量、TTS 和 `turn_end`；
- 空文本返回 `empty_transcript`；
- 二进制上行返回 `binary_audio_unsupported` 且不触发 STT；
- 重复轮次返回 `busy`，坏 ticket、header 直连鉴权、外来会话和 `end` 语义不变。

- **量规冻结（W3/D04）**：出题 prompt（三条生成路径共用契约 `quiz_verify.RUBRIC_REQUIREMENT`）随题输出 `rubric_criteria[{id,description,weight,critical}]` + `equivalent_solutions`；`freeze_rubric` 在题目通过校验、获得稳定题号后以 `rubric_id=题号、version=1` 冻结随题落盘（出题时学生未作答，天然满足「看学生作答前冻结」）。形状非法→rubric 缺省（题仍可用，结构化分析回退三级批改）。M4 出题/批改 prompt 已迁入注册表（`assessment_generate@1.0.0`/`assessment_generate_auto@1.0.0`/`assessment_grade@1.0.0`，文本含量规契约）。A17 四分标签（`verification_label`：content_checked/ambiguous/structural_valid/unchecked）从现有 verification 审计派生供投影/审计使用；`question_verified()` 两级语义不变。
- **结构化作答分析（W3/D06，`STRUCTURED_ASSESSMENT_MODE=off|shadow|active`，默认 off）**：`agents/assessment/structured_evaluator.py` 单次调用（prompt `assessment_analyze@1.0.0`）输出量规条目判定（met/partial/not_met/not_observed/not_applicable，criterion_id 只认冻结量规候选集）、首个实质错误+此前做对部分、≤2 错因假设（七类枚举）、uncertainties、六维能力观测、feedback{strength,next_step}、continuation 建议——D06+局部 D07+D08 建议合并为一次调用。**分数由服务端按冻结权重本地计算**（not_applicable 出分母、关键条目 not_observed→整题不确定→保守 partial），不采信模型自报。坏 JSON 修复一次→再坏弃权回退三级批改（不造默认分）；MC 始终确定性判分；shadow 旁路结果存 `structured_shadow` 可对账；active 结果即权威判定，随 `AssessmentResult.structured`、账本 attempt（criterion_results/hypotheses）、M2 事件 payload 增键落盘。学生作答中的指令不进入任何工具/指令位（schema 固定 + 白名单校验）。
- **CAT continuation（W3/D10，`agents/assessment/continuation_policy.py`）**：硬上限优先——`should_stop` 纯规则不动、先判；仅当硬规则放行时 active 模式才让分析的 continue/probe/finish 建议生效：finish→`sufficient_evidence`（分数≥0.75 且无关键条目未达标）或 `insufficient_evidence`（信息不足以未定结束，不判失败），与作答同一次落盘；probe→下一题 goal 注入未覆盖关键条目+错因假设（生成后消费清空）；shadow 存 `session.continuation_shadow` 不生效。前端对两个新 stop_reason 提供中性文案映射。
- **提示与异议（W3/F02/F05 最小形式）**：`GET /quiz/hint`（A01 同款归属校验）从冻结量规派生关键步骤提示（不含答案/判定，先答后揭晓不破），服务端在题快照落 `hint_requested` 标记——判分自动携带 `assistance=hint`（M2 事件记载；评分置信压至 0.70，帮助后表现不伪装独立掌握；客户端自报帮助状态一律不采信）。`POST /quiz/dispute` 把账本 attempt 标记 `evidence_status=disputed`（保守：不抹除证据、不改投影；修正走重答 supersede+BKT 重放）。

---

## P11 运行可靠性与证据闭环整改（2026-09 历史整改记录）

> **历史说明**：本节是 2026-09「W1–W4 整改」当期的工作记录，其中描述的判分置信度、
> BKT 回写、learning_records 账本、E0–E5 证据门等机制**已被本版统一学习评价体系
> 取代**（见 §11/§13 与 `scripts/migrate_learning_evidence.py`）。保留本节仅为变更
> 史与当时决策依据的可追溯性，不构成当前架构承诺；当前有效合同以 §1–§24 为准。

本轮整改不推翻既有架构，围绕五个既有缺口收口（当期整改计划已归档于仓库历史）。

### P11.1 统一 Quiz Grounding（教材证据进入出题器）

检索唯一事实源仍是 `KnowledgeSearchTool`（BM25/hybrid + `evidence_gate` 的
found/partial/not_found 原语义）；`core/quiz_grounding.py` 只做数据投影：

- `QuizGroundingBundle`/`QuizSourceRef`：grounded 题的可审计 provenance
  （file/chunk/章节/页码/摘录/context_hash）；provenance 只认服务端 src_N
  短 ref id，模型伪造的完整 file_id 没有注入机会；
- `GenerateQuizTool`：grounding resolve **先于** 蓝图轮（蓝图未见教材会先
  发散到教材外）；strict 教材模式 NOT_FOUND 不出假教材题；strict 题无有效
  source ref 直接丢弃（不能 fail-open 成 grounded）；
- critic 增加 `unsupported` verdict（答案/解析依赖证据未支持的教材事实），
  仅在提供 grounding_context 时启用；critic 不可用时
  `grounding_verification=unavailable` 审计标记；
- `FitQuizTool` 的事实源是 reference 本身，不强制检索；仅继承本轮已解析
  的教材证据（reference+textbook）；
- Assessment/CAT：`AssessmentContext`/`Question` additive grounding 字段
  （持久化 round-trip 不丢证据 scope）；`/assessment/start` 接受
  session_id/textbook_ids/strict_textbook，授权解析在
  `api/v1/assessment_grounding.py`（外来 session/教材 404、strict 无 scope
  400、strict NOT_FOUND 返回 grounding_not_found 不开始假教材 CAT）；
- 前端 QuizQuestionCard 教材依据 badge（data-testid=quiz-source-badge）
  与解析区依据列表；`/quiz/record` 上报 additive provenance（仅审计用，
  学习证据不因客户端声称的 ref 改变）。

### P11.2 非 OCR 教材构建的重启恢复

textbook record 内嵌 `build_job`（state/phase/attempt/intent；不另建
jobs.json）。普通进程重启不是失败：

- `reconcile_stale_builds()`：OCR 可恢复 -> 现有 ocr_waiting 路径；
  有 intent 的中断 job -> state=queued 待 lifespan 重入现有 per-owner
  队列（幂等重执行整个 intent，auto_retry 下终态记录跳过）；legacy building
  记录合成默认 intent；source 缺失/自动恢复超上限（3）才真正 failed
  （结构化 last_error：source_missing/retry_exhausted/...）；
- `enqueue_textbook_build` 入队前持久化 intent（首次上传/重试/rebuild/
  full OCR 统一）；worker 取得执行权时 running+attempt+=1；队列项结束终态
  收口；`resume_interrupted_textbook_builds()` 供 lifespan 调用；
- `reap_stale_builds()` 保留为兼容 wrapper。

### P11.3 Bootstrap 可观测性与 Readiness

`core/bootstrap.py`：每个启动维护步骤有命名 check（pending/ok/degraded/
failed），失败 log.exception（不再静默），critical fail-fast，可降级功能
失败不阻止服务。`/api/v1/health` 保持 liveness 恒 200；`/api/v1/ready`
按 bootstrap 结果返回 ready/degraded（200）或 not_ready（503）。

### P11.4 上传限流读取

`core/uploads.read_upload_limited`：分块读取，累计超限即刻 UploadTooLarge
——超限攻击文件最多读 limit+chunk_size。chat/library/workspace/textbook/
notes 五个上传端点统一接入；图片 20MB / 文档 256MB 语义不变。

### P11.5 持久化并发不变量（single-worker）

- 文件承载的业务状态 + 进程内 RLock（`core/atomic.py`）；
- 生产 backend 必须单 uvicorn worker（`deploy/edu-backend.service` 钉死
  --workers 1，`scripts/check_repository_invariants.py` 在 CI 校验）；
- 横向多副本在共享存储/锁重新设计前不受支持。

### P11.6 版本化公共知识资产（ADR）

public textbooks / public graph / public vector artifacts / demo showcase
是产品内容与展示 fixture，**使用普通 Git object 直接版本化**：

- 不使用 Git LFS；不迁 GitHub Release/S3/对象存储；不做 clone 后二次下载；
- 不针对这些资产做 history rewrite；不设 repository-size fail gate；
- 保护手段只有两类：`.gitignore` 白名单保持显式（绝不放开整个
  notes/students/users），以及 `scripts/check_repository_invariants.py`
  在 CI 校验（白名单行存在、无 LFS 接管、无真实用户运行态数据误跟踪）。

### P11.7 CI 与 E2E

`.github/workflows/ci.yml` 四个 job（名字固定供 required checks）：
backend-core（BM25-only 全量单测，vector 测试 feature-skip）、
backend-vector-regression（+requirements-test，vector/公共资产回归）、
frontend-checks（pnpm frozen install + tsc + lint + build）、
repository-invariants。Playwright E2E（frontend/e2e/）真实起
backend（隔离副本）+ frontend + fake LLM，覆盖身份隔离、教材到 BM25、严格
教材问答、grounded quiz、NOT_FOUND、笔记私有、学习计划与语音协议冒烟。

### P11.8 零凭证测试环境（本地=CI 奇偶）

CI runner 没有根目录 `.env`，测试套件必须在**零凭证**下自洽；本地默认
与 CI 完全同环境，防止"本地被真实凭证掩护、CI 才暴露"的假绿：

- `backend/tests/__init__.py` 在导入任何 app 模块前，清除根 `.env`
  定义的全部变量与 shell 里的 LLM/凭证变量（`OPENAI_API_KEY`、
  `LLM_API_KEY`、`LLM_BASE_URL` 等），并设置 `EDU_TEST_KEYLESS=1`；
- `app/core/config.py` 与 `app/identity/config.py` 在该标志下跳过
  `load_dotenv`——真实凭证在测试进程里根本不存在，也就不可能打真实
  API（费用安全）；
- 教训（回归钉死）：voice busy-rejection 测试曾因 `_run_turn` 里真实的
  `get_llm()` 在 CI 构造 `AsyncOpenAI` 即抛 "Missing credentials"、turn
  秒死、`busy` 事件永不到来而无限阻塞；所有走 turn/端点的测试必须
  patch `get_llm`/`_build_tools`（见 test_voice / test_compat_api /
  test_assessment_identity 的 setUp 模式）；
- backend-core 安装使用 `-c backend/constraints.txt` 锁定解析集（与
  本地一致，防 openai 等主版本漂移），timeout 45min 匹配 2 核 runner
  上 ~1900 测试的实际耗时。
