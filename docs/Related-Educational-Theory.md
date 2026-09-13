# Related Educational Theory — Next Tutor Agent 教育理论与评价架构

> 本文定义 Next Tutor Agent 在**讲解、练习出题、学习检验与证据回写**上的教育理论依据和工程落点。
>
> 本文不是教育理论综述，也不主张“理论越多越专业”。本项目只采用一个总架构与两个子理论：
>
> 1. **Evidence-Centered Design for Learning（ECDL，面向学习的证据中心设计）**：总架构；
> 2. **Cognitive Load Theory（CLT，认知负荷理论）**：讲解与教学支持层；
> 3. **Revised Bloom’s Taxonomy（RBT，修订版布鲁姆教育目标分类学）**：出题与认知检验层。
>
> 其中 ECDL 负责回答“系统凭什么从一次教学互动推断学生发生了什么学习变化，以及下一步为什么这样教”；CLT 负责回答“当前讲解和支持方式是否适合学生已有知识与任务复杂度”；RBT 负责回答“这道任务究竟要求学生进行哪一种认知加工”。三者职责互补，不相互代替，也不合成为一个没有理论依据的“教育质量总分”。
>
> **状态说明**：本文同时记录仓库当前已经实现的基础，以及后续将理论正式纳入评价体系时的规范。凡标记为“现状”的内容是当前 `main` 的事实；凡标记为“目标契约”的内容是后续实现时必须遵守的设计约束。本文本身不宣称这些新增审查器已经在线生效。

---

## 1. 为什么选择这三项，而不是继续堆叠理论

Next Tutor Agent 的核心闭环在 `docs/DESIGN.md` 中已经明确为：

**学习目标 → 知识理解 → 练习训练 → 能力评估 → 调整。**

当前仓库又已经形成了清晰的模块分工：M2 维护学生状态，M3 决定怎么教，M4 负责出题与测评，M5 提供教材知识依据，M10 控制学习证据是否有资格写回学生模型。问题因此不是“还缺多少理论”，而是缺一套能把这些现有模块解释为一个**有效推断闭环**的理论架构。

### 1.1 总架构选择 ECDL

ETS 的 Eric G. Hansen 在 2011 年提出 **Evidence-Centered Design for Learning**。它从广泛用于测评设计的 Evidence-Centered Design（ECD）出发，把传统的：

- Student / Proficiency Model（学生/能力模型）；
- Evidence Model（证据模型）；
- Task Model（任务模型）；

扩展为面向学习系统的：

- **Pedagogical Model（教学模型）**。

ECD 主要关注“学习者当前处于什么状态”；ECDL 更进一步关注“学习者如何发生变化，以及系统如何通过教学促进这种变化”。这正对应智能教学系统需要同时完成的两个任务：**推断**与**干预**。

它尤其适合本项目，而不是只适合传统考试系统：已有研究曾直接使用 ECDL 分析 ASSISTments 这类把 performance assessment 与 instructional assistance 混合在一起的智能辅导系统。换句话说，ECDL 与 Next Tutor Agent 的产品形态是同一类问题：不是考完给一个分数，而是在“教—练—证据—再教”之间循环。

### 1.2 讲解层选择 CLT

教学讲解最容易出现的工程误区，是把“讲得更长、更完整、更专业”直接等同于“教得更好”。CLT 的价值恰好是阻止这种等价关系。

CLT 建立在工作记忆容量有限、长期记忆中的知识结构会显著改变任务加工方式这一认知架构上。对智能教师而言，它能直接指导：

- 初学者何时需要 worked example（完整示例）；
- 复杂任务何时应该拆分或分段；
- 何时应该把分散的信息整合，减少 split-attention；
- 何时解释已经变成冗余；
- 为什么相同的支持对新手有帮助、对熟练者却可能妨碍学习；
- 为什么教学支持应随证据逐步从 `full_demo → key_hints → independent` 撤除。

这些问题与当前 M3 的 `TeachingStrategy`、`assistance`、`depth`、`examples_needed` 等字段高度一致，因此 CLT 可以进入真实决策，不需要另造一套教学状态机。

### 1.3 出题与检验层保留并正式规范 RBT

RBT 已经是当前仓库的正式组成部分，而不是待引入概念：

- `backend/app/core/bloom.py` 定义 `remember / understand / apply / analyze / evaluate / create`；
- `backend/app/core/bloom_profile.py` 从学习账本确定性聚合学生在不同认知过程上的表现；
- M4 generator、chat quiz、两轮命题蓝图、测评页与画像页都已经消费 `bloom_level`。

因此没有理由再引入另一套认知目标分类体系与它竞争。正确做法是把现有 Bloom 从“命题标签”进一步规范为 ECDL Task Model 中的**认知任务目标**，并补齐“目标认知过程是否真的被题目要求出来”的验证。

### 1.4 明确不增加第四套核心理论

本项目后续不应为了显得专业而同时再挂 Constructive Alignment、Scaffolding、ZPD、ICAP、SOLO、Gagné、Merrill 等一长串名字。它们中的一些思想可以与 ECDL/CLT/RBT 相容，但如果没有独立的数据、决策接口和验收标准，就不应成为正式系统理论。

例如当前 `full_demo / key_hints / independent` 的确具有“支架逐步撤除”的教学含义，但项目可以直接在 CLT 的 guidance fading / expertise reversal 机制下实现，无需再建立第二套“支架理论评分器”。同理，目标—任务—证据的一致性已经是 ECDL 的基本要求，不需要再为了同一个工程问题增加一个平行总框架。

---

## 2. ECDL：本项目的总评价架构

### 2.1 ECDL 的核心不是“评分”，而是证据论证链

ECDL 最重要的思想可以压缩成一句工程规则：

> **系统对学生作出的任何学习状态判断，都必须能够回答：我们主张学生发生了什么变化？什么可观察行为能支持这个主张？什么任务给了学生产生这些行为的机会？根据这些证据，下一步教学为什么合理？**

因此 ECDL 不鼓励“模型觉得学生懂了”“模型觉得这题很有质量”这类无证据判断，而要求明确区分：

1. **Claim（主张）**：希望知道学生会不会什么；
2. **Task / Situation（任务/情境）**：给学生什么机会表现；
3. **Observable Evidence（可观察证据）**：学生实际做了什么；
4. **Inference（推断）**：哪些证据允许支持到什么范围；
5. **Pedagogical Action（教学行动）**：根据当前证据下一步怎样教；
6. **Expected Observation（预期观察）**：下一步行动之后希望看到什么，作为新的证据。

这正是 Next Tutor Agent 应当采用的“教育评价”含义：不是再增加一个 LLM judge，而是让现有 LLM 判断都进入同一条可追踪证据链。

### 2.2 四个核心模型与仓库映射

| ECDL 模型 | 理论职责 | Next Tutor Agent 对应模块 | 当前已有基础 |
|---|---|---|---|
| Student / Proficiency Model | 表示关于学生知识与能力状态的假设 | M2 `student_model/`、BKT、capability projection、Bloom profile | 已实现 |
| Task Model | 描述为了获得证据或促进学习，应提供什么任务 | M3 `next_check`、M4 question generator、`quiz_design.py`、练习/测评 | 已实现大量基础 |
| Evidence Model | 定义观察什么、如何解释、什么不能推断 | frozen rubric、`structured_evaluator.py`、quiz critic、M10 evidence gate | 已实现强基础 |
| Pedagogical Model | 根据学生状态与证据选择下一教学动作 | M3 rules engine + `decision_adapter.py` + Tutor prompt | 已实现动作框架，理论契约待加强 |

M1 Supervisor 是这些模型在单轮对话中的执行编排层；M9 是跨日/跨周的任务装配与调度层；M5 是任务与讲解的**内容事实来源**。它们都参与 ECDL 闭环，但并不需要被改造成新的学生模型或证据模型。

### 2.3 Student / Proficiency Model：学生状态必须分清“估计”和“证据”

#### 当前实现

M2 已有两类互补状态：

- `mastery.py` 使用 BKT 维护每个 skill 的 `p_known`；
- `capability_projection.py` 根据事件与学习账本派生 concept × dimension 的证据状态；
- `bloom_profile.py` 按 concept × Bloom process 聚合作答表现。

同时，capability projection 已采用非常关键的保守原则：无观测即 `not_observed`，存在正负冲突则 `needs_recheck`，需要多个不同 rubric family 才进入 `demonstrated_in_scope`。M10 evidence gate 也已经阻止 exposure、自报和简单复述直接更新 mastery。

#### ECDL 目标契约

这些状态不应该被合并成一个“学生能力总分”：

- `p_known` 是一个模型化的掌握度估计；
- `capability_projection` 是从具体证据派生的能力陈述；
- `bloom_profile` 是按认知任务标签组织的历史表现统计。

它们在 ECDL 中共同属于 Proficiency Model，但语义不同。未来任何 LLM 都不得通过一句“学生看起来已经掌握”覆盖这些确定性或统计状态。

特别要求：

- 讲解完成本身不构成 mastery evidence；
- 学生说“懂了”只构成 self-report；
- 学生能复述定义，不自动支持 apply/analyze 能力；
- 一道即时题做对，不自动支持长期 retention；
- 同模板改数字，不自动支持 transfer；
- 接受 `full_demo` 后模仿成功，不应与独立成功具有相同证据解释。

### 2.4 Task Model：任务必须服务于一个明确的学习主张

当前 M4 已经有 `AssessmentGoal.assesses`、`forbidden`、difficulty、Bloom focus、grounding、blueprint 等任务约束。ECDL 不要求另造一个庞大的统一对象；第一阶段应优先复用这些字段，让每个重要 task 最少可以回答：

- `target_concept`：测什么概念；
- `target_capability`：希望观察哪类能力；
- `target_bloom`：希望触发什么认知过程；
- `evidence_opportunity`：题目中哪一个要求能够产生所需证据；
- `assistance_condition`：学生完成时是否获得了帮助；
- `grounding_refs`：题目使用的教材事实来自哪里。

如果一个任务没有提供某种证据机会，那么后续评价器必须输出 `not_observed`，不能“根据答案整体质量顺便推断”。

### 2.5 Evidence Model：评价器的第一职责是限制推断边界

当前 `structured_evaluator.py` 是仓库中最接近成熟 ECD evidence model 的模块：

- rubric 在看学生答案前冻结；
- 每一 rubric criterion 有固定 id；
- LLM 只能在白名单枚举内输出；
- 分数由服务端按 frozen weights 本地计算；
- 无法观察时支持 `not_observed`；
- critical criterion 未观察到时可以使整体结果 indeterminate；
- invalid JSON 修复一次后 abstain，而不是编造结果。

这些设计应原样保留并作为全项目理论审查的模板。

未来要补齐的是 **observability eligibility（可观察资格）**。六维能力的最低证据条件建议固定为：

| 能力维度 | 允许给出 met / partial / not_met 的最低条件 | 否则 |
|---|---|---|
| `concept` | 任务要求解释、辨析、定义关系，或学生答案中存在直接概念证据 | `not_observed` |
| `procedure` | 任务要求执行步骤、算法、推导或操作过程，且过程可观察 | `not_observed` |
| `reasoning` | 任务要求理由、论证、条件判断、因果或推导，而非只填最终答案 | `not_observed` |
| `transfer` | 相对学习材料有实质新情境、新表征或新问题结构，而非同模板换数字 | `not_observed` |
| `retention` | 存在有时间间隔的 delayed retrieval / delayed assessment 证据 | `not_observed` |
| `self_check` | 实际观察到检查、验证、纠错、修订或显式反思行为 | `not_observed` |

这条规则非常重要：评价器的“专业性”首先来自**知道什么时候不能评价**。

### 2.6 Pedagogical Model：每一个教学动作都必须带预期证据

当前 `TeachingDecision` 已经包含：

- `action`；
- `target`；
- `assistance`；
- `rationale`；
- `expected_observation`；
- `stop_condition`。

这与 ECDL Pedagogical Model 天然匹配。未来应把 `expected_observation` 和 `stop_condition` 从“有用描述字段”提升为教学决策契约：

**任何会改变教学路径的 LLM decision，都必须明确说明它希望下一步观察什么，以及什么证据出现后应停止当前策略。**

例如：

- 不能只输出“继续解释牛顿第二定律”；
- 应表达为“针对受力图中方向判断错误，用一个分步 worked example 重建力的方向关系；随后给一个同构但数值不同的无提示判断题；若学生能独立正确标出全部力并解释方向，则撤除 full_demo，进入 key_hints/independent”。

这里的关键不在于文字格式，而在于教学行动成为一个**可证伪的假设**。如果预期观察长期没有出现，M3 应换策略，而不是无限重复同一种讲解。

### 2.7 ECDL 在系统中的完整闭环

推荐的理论闭环是：

```text
M2 当前学生状态
        ↓
确定本轮 Learning Claim
        ↓
M3 Pedagogical Action
  - 为什么这样教
  - assistance level
  - expected observation
  - stop condition
        ↓
Tutor / M4 Task
  - 教学讲解（CLT）
  - 练习/测评任务（RBT）
  - M5 教材事实 grounding
        ↓
M4 Evidence Interpretation
  - critic / frozen rubric
  - structured evaluator
  - not_observed discipline
        ↓
M10 Evidence Gate
  - 该证据是否有资格更新学生模型
        ↓
M2 更新 BKT / capability / Bloom profile
        ↓
M3 选择下一教学行动
```

整个闭环的评价对象不是“LLM 回答漂不漂亮”，而是：**主张—任务—证据—推断—行动是否形成闭合关系。**

---

## 3. Cognitive Load Theory：讲解与教学支持层的理论

### 3.1 理论核心

CLT 的基础是人类认知架构：对学习者而言，处理新颖信息的工作记忆容量和持续时间有限；已经组织在长期记忆中的知识结构则能够显著降低同类任务的即时加工负担。因此“同一份解释”不会对所有学生产生同样的学习效果。

Sweller、van Merriënboer 与 Paas 对 CLT 二十年研究的总结指出，教学设计需要尽量减少与学习目标无关的认知加工，并管理任务本身的复杂性，使学习者能把有限资源用于建构可长期使用的知识结构。

在工程上，本项目不把 CLT 简化为“越短越好”，也不把它简化成“每次只能讲三个知识点”。真正需要关注的是：

- 学习者已有知识；
- 新信息元素之间的交互复杂度；
- 呈现方式是否产生不必要的搜索、整合与重复；
- 当前帮助程度是否适合学生水平；
- 支持是否应该逐渐撤除。

### 3.2 关于 intrinsic / extraneous / germane load 的使用口径

CLT 文献长期使用 intrinsic、extraneous、germane load 的三分法，但后续理论发展已经调整了 germane load 的概念化。为了避免项目把旧术语做成伪精确指标，Next Tutor Agent 不建立三个 0–100 的“负荷分”。

工程上只保留两个可靠问题：

1. **任务本身的必要复杂度是否被合理管理？**——复杂概念不能被“优化”到失去本质，但可以通过顺序、分段、先备知识和示例来管理；
2. **是否存在可避免的额外负荷？**——例如信息分散、无关细节、重复解释、过早要求新手完全独立求解等。

“促进学习所需要的有效加工”是目标，不由 LLM 假装测量成一个独立的 germane-load 数值。

### 3.3 Worked-example effect：为什么 M3 的 full_demo 有理论依据

CLT 对初始技能习得最稳定的发现之一是 worked-example effect：对于缺乏相关知识结构的新手，直接研究完整解题示例，通常比一开始就让其进行传统问题求解更有效或更高效。相关研究也在 Cognitive Tutor 场景中观察到了 faded worked examples 的优势。

因此当前 M3 的：

`full_demo → key_hints → independent`

不是随意设计的三个 UI 标签，而可以正式解释为“高支持 → 部分支持 → 独立完成”的 guidance fading 通道。

但 CLT 同时意味着这条通道**不能成为机械阶梯**：

- 新手、高 element interactivity 任务、连续失败时，允许提高支持；
- 已经表现稳定的学生仍反复收到完整示范，会产生冗余并削弱主动解决机会；
- 当学生具备相应 schema 后，原本有益的指导可能因 expertise reversal 变成不必要负担；
- “做对一次”不足以永远撤除支持，应该结合独立表现、错误类型和任务变化判断。

### 3.4 讲解层应正式审查的六个维度

未来讲解评价不输出“认知负荷 = 73”。它只审查**教学设计风险**：

| 维度 | 评价问题 | 可使用的当前信号 | 典型风险 |
|---|---|---|---|
| `guidance_fit` | 当前帮助量与已有知识是否匹配 | BKT、capability、recent outcomes、assistance | 新手无支持；熟练者反复 full demo |
| `element_interactivity_control` | 一次要求同时处理的相互依赖新元素是否过多 | concept prerequisites、stage、任务步骤 | 定义/公式/多个新概念同时引入 |
| `split_attention_risk` | 学生是否必须来回搜寻并自行拼接信息 | 文本、公式、图片、步骤引用 | 图和解释分离；变量定义散落 |
| `redundancy_risk` | 是否重复提供学生已掌握或已可直接获得的信息 | mastery、已讲历史、当前材料 | 高水平学生仍被逐句解释基础内容 |
| `transience_segmentation` | 短暂信息是否需要分段、停顿或可回看结构 | Voice、长推导、连续步骤 | 语音一次念完长公式链；步骤无法定位 |
| `fading_readiness` | 是否具备减少支持或转独立任务的证据 | 独立作答、hint dependence、recent errors | 长期依赖提示；过早撤架 |

这些 verdict 应采用 `pass / warn / fail / not_observed`，并带输入证据引用。`not_observed` 很重要，因为系统目前没有直接测量学生主观 mental effort 的仪器或量表；LLM 只能审查**设计风险**，不能声称测出了实际 cognitive load。

### 3.5 CLT 与 `teaching_engine/policy.py` 的耦合

#### 现状

`policy.py` 已经按 TeachingMode 定义 focus / avoid / depth / examples_needed，并按学段调整 INTRODUCTION；它还是纯规则路径，可审计、可降级。

#### 目标契约

保留这套规则，不引入第二套平行 policy。CLT 进入这里时，只负责解释和校正以下决策：

- `depth`：不是“越深越好”，而是与 prior knowledge 和任务目标匹配；
- `examples_needed`：由 worked-example / prior knowledge 逻辑支持；
- `focus / avoid`：用于减少无关信息、控制 element interactivity；
- `next_check`：讲解后应尽快用可观察任务判断是否可以撤除支持；
- `exercise_level`：复杂度增加与 support fading 应协同，而不是同时陡升。

规则路径继续是故障时的权威 fallback；CLT reviewer 不得在失败时让整个聊天中断。

### 3.6 CLT 与 `teaching_engine/decision_adapter.py` 的耦合

`decision_adapter.py` 已经具备一个很好的边界：LLM 只在触发条件命中时调用，只能从白名单 action/assistance 中选择，且验证失败就回到 rules。

未来 CLT 不需要再增加一个每轮都运行的大模型。更经济的设计是让现有 teaching decision prompt 接收一个精简的 CLT decision brief：

- 当前学生相对该概念的证据等级；
- 最近是否独立完成；
- 是否连续使用 hint；
- 当前任务是否具有高步骤依赖；
- 当前 assistance；
- 允许的下一 assistance。

`rationale` 必须解释 assistance 与这些证据的关系；`expected_observation` 必须说明怎样的学生行为会支持下一次 fading 或增加帮助。

### 3.7 CLT 与 Tutor 主讲解 prompt 的耦合

当前 `tutor_system` 已经规定讲解默认应包含知识定位、直观动机、定义、分步推导、例题、易错辨析、联系与小结，并且学生显式要求“一句话/简短/表格/步骤”等时优先尊重用户约束。

CLT 不应把这套结构简单替换成“越短越好”。正确耦合方式是：

- **新手 + 高交互复杂度**：分段，一次完成一个子目标；优先完整 worked example，再给 completion/变式；
- **已有基础 + 中等复杂度**：减少已知步骤解释，把资源放在关键推理点；
- **高掌握度**：避免冗余定义和完整示范，增加独立推理；
- **语音讲解**：对长公式、长推导和多条件过程分段，并在段落之间形成可回看的文字/结构摘要；
- **图像/公式**：避免让关键标签与解释分散到相距很远的位置；
- **学生主动要求详细**：可以详细，但仍应组织为可加工的块，而不是把大量相关知识一次倾倒。

用户显式格式/长度要求始终高于默认教学样式。CLT 是教学设计依据，不是剥夺学生控制权的硬门。

### 3.8 讲解评价如何真正上线，而不破坏流式体验

不建议在每个 Tutor token 之后再串一个“教育学审稿 LLM”。推荐两条轨：

**在线决策轨**：M3 在生成前用规则 + teaching decision 选择 assistance/depth/focus，把短 CLT 指令作为 prompt material 注入 Tutor；这一轨真正影响教学行为。

**评价轨**：对完整讲解做 shadow audit，记录六个 CLT 设计风险与 ECDL 对齐情况，用于 M7/离线 gold set。只有经过专家标注校准的少数高置信规则，才考虑进入 active revision；即使 active，也最多允许一次局部重写，不能形成 reviewer ↔ generator 无限循环。

这样 CLT 真实参与在线教学，又不要求每一次流式回答都增加第二个完整生成调用。

---

## 4. Revised Bloom’s Taxonomy：出题与认知检验层的理论

### 4.1 修订版 Bloom 的准确结构

Anderson 与 Krathwohl 主编的 2001 修订版不是单一的“六级难度表”，而是二维分类：

**Cognitive Process Dimension（认知过程维度）**：

1. Remember；
2. Understand；
3. Apply；
4. Analyze；
5. Evaluate；
6. Create。

**Knowledge Dimension（知识维度）**：

1. Factual；
2. Conceptual；
3. Procedural；
4. Metacognitive。

当前仓库只正式实现了第一维，这是合理的最小实现。第二维是否进入持久化，应在真实题目和 gold set 证明有增益后再增加；不应为了“完整复刻教科书”立即给全仓库新增字段。

### 4.2 Bloom 不是题目难度

这是本项目必须写入设计规范的边界：

> **Bloom cognitive process 表示任务要求学生做什么认知加工；difficulty 表示任务对特定学习者或总体样本有多难。二者相关但不等价。**

例如：

- 一个熟悉材料上的 Analyze 题可以很容易；
- 一个需要大量繁琐计算的 Apply 题可以很难；
- Create 不应自动等于“最高难度”；
- CAT/经验作答表现负责 difficulty / information，Bloom 不负责。

因此工程上应继续分离：

- `bloom_level` / `target_bloom`：认知目标；
- M3/M4 difficulty：当前任务复杂度/产品难度控制；
- CAT item statistics：经验测量层。

### 4.3 RBT 在 ECDL 中的角色：定义“我们希望看见哪种认知行为”

RBT 不是总评价框架，而是 Task Model 的一个关键语义维度。

如果 learning claim 是“学生能分析串联和并联结构的差异”，任务必须要求学生进行关系分解、比较或条件辨析；如果题目只问“并联电阻公式是什么”，即使标签写了 `analyze`，也没有产生分析能力证据。

因此未来每道重要题都需要区分：

- **intended process**：蓝图声明要测什么；
- **elicited / actual demand**：学生真正需要做什么才能答对；
- **alignment**：两者是否一致。

真正专业的 Bloom reviewer 不是检查题干里有没有“分析、评价、设计”等动词，而是判断**最低可行解法**是否真的要求目标认知过程。

### 4.4 与 `core/bloom.py` 的耦合

`core/bloom.py` 应继续作为全项目唯一 canonical vocabulary，不建立第二份 Bloom 枚举。

当前模块的“反僵化原则”应保留：

- 不建立“答对 N 道 remember 才能升 understand”的硬阶梯；
- 允许跳层、混层、回访；
- level 是共享语义，不是 mastery gate；
- 未知/自动可以留空，由上下文决定。

未来理论文档和 reviewer 都必须从此模块读取同一组 canonical ids。

### 4.5 与 `core/bloom_profile.py` 的耦合

当前 Bloom profile 是从 learning ledger 做确定性聚合，这是正确方向，但它只能解释为：

> “学生在被标记为某认知过程的题目上的历史表现”。

不能自动解释为：

> “学生达到了 Bloom 第 N 层”。

因为题目标记本身可能错、题型难度不同、assistance 不同、样本量不同。未来当 quiz critic 能确认 `intended_bloom ≈ actual_demand` 后，Bloom profile 的证据质量才会进一步提高。

### 4.6 与 `core/quiz_design.py` 的耦合

当前 two-pass quiz design 是 RBT 最重要的已有落点：先设计考查角度、Bloom level、题型、陷阱、构想，再生成题面。这比单轮“直接写题”更适合 ECDL Task Model。

目标契约是在第一轮 blueprint 中让每题至少明确：

- `claim`：希望从学生回答中知道什么；
- `angle`：从哪个角度制造证据机会；
- `bloom`：目标认知过程；
- `evidence_opportunity`：题目要求中的哪一部分迫使学生展示该认知过程；
- `trap`：用于暴露什么典型错误，而不是单纯增加迷惑性。

这里的“陷阱”不能只是为了让题变难；它必须服务于诊断。例如“混淆速度和速率”可以暴露概念关系错误，而无意义的繁琐数字只会产生 construct-irrelevant difficulty。

### 4.7 与 `assessment/generator.py` 的耦合

当前 generator 已经串起：

`assesses / forbidden → Bloom guidance → student Bloom context → textbook grounding → blueprint → generation → structure check → independent critic → rubric freeze`。

这已经是一个相当好的 Task Model 管线。理论层不应重写它，只需增强两个字段的闭环：

1. 生成前：明确 intended claim / Bloom；
2. 生成后：验证实际题目是否真的提供相应 evidence opportunity。

教材 grounding 负责“题目的事实从哪里来”，Bloom 负责“学生要做什么认知加工”，两者不能混为一谈。

### 4.8 与 `core/quiz_verify.py` 的耦合

当前 critic 已经做三类非常重要的检查：

- 独立重解以检查 answer key；
- 检查 medium/hard 是否明显 `too_shallow`；
- 有教材证据时检查 `unsupported`。

后续最值得增加的理论检查不是另起一次 LLM，而是扩展**同一次 critic**的结构化输出：

- `intended_bloom`；
- `actual_bloom`；
- `bloom_alignment = aligned | weaker_than_target | different_construct | indeterminate`；
- `evidence_opportunity = sufficient | insufficient | indeterminate`；
- `construct_irrelevant_risk`：题目的难是否来自与目标无关的阅读、计算或缺失信息。

只有答案正确还不够；一个声称测 Analyze、实际上只需背公式的题，应被视为“有效性不足”，而不是简单的 correct question。

### 4.9 与 frozen rubric / structured evaluator 的耦合

Rubric 应描述**学生作答中可观察的表现**，而不是重复正确答案全文。例如：

- “识别出两个支路共享相同端点”是可观察 criterion；
- “答案正确”不是足够细的 evidence statement；
- “表现出分析能力”过于抽象，无法可靠评分。

RBT 给 Task Model 提供目标认知过程，rubric 则把它转换为可观察证据。structured evaluator 只能判定 rubric 能观察到的东西，不能因为题目标签是 Analyze 就自动把 `reasoning=met`。

---

## 5. 三层理论如何进入项目的真实评价体系

### 5.1 不建立“教育学总分”

未来理论评价统一使用结构化 verdict，不计算 `pedagogy_score = 87` 之类的总分。原因是三个理论处理的 construct 不同：

- ECDL 检查推断链是否成立；
- CLT 检查讲解设计风险；
- RBT 检查认知任务目标。

把它们加权平均没有可靠理论意义，而且会掩盖关键失败。例如“事实准确 + Bloom 对齐，但任务没有给学生产生证据的机会”不能因为其它项高分而整体通过。

### 5.2 统一 review 语义

所有未来 theory review 至少应遵守以下语义：

```text
applicable
verdict = pass | warn | fail | not_observed | not_applicable
claim
observed_evidence[]
rationale
recommended_revision
blocking
reviewer_version
```

其中：

- `observed_evidence` 必须引用已有数据/任务/rubric/trace，而不是模型自述；
- `not_observed` 是正式结论，不是异常；
- `blocking=true` 只能用于经过 gold set 验证、且对学习证据有效性至关重要的规则；
- LLM 的自然语言 `confidence` 不得冒充经过校准的统计概率。

### 5.3 ECDL review contract

建议固定五个维度：

| 字段 | 问题 | 失败含义 |
|---|---|---|
| `claim_defined` | 本轮到底想促进/检验什么学习变化？ | 教学/题目没有明确目的 |
| `evidence_opportunity` | 学生是否有机会产生支持该主张的行为？ | 后续不能据此推断 |
| `task_alignment` | 任务要求是否与 claim 一致？ | 测到了别的东西 |
| `inference_scope` | 当前证据支持的结论是否越界？ | 例如即时正确→retention |
| `next_action_link` | 下一教学动作是否由已有证据触发，并定义下一观察？ | 教学决策不可验证 |

### 5.4 CLT explanation review contract

只评估设计风险，不声称测量真实 mental load：

- `guidance_fit`；
- `element_interactivity_control`；
- `split_attention_risk`；
- `redundancy_risk`；
- `transience_segmentation`；
- `fading_readiness`。

Active 模式下它最多改变**表达结构、分段、示例/提示程度**，不得改变教材事实、评分结果、BKT 或 capability status。

### 5.5 RBT task review contract

至少记录：

- `intended_process`；
- `actual_demand`；
- `alignment`；
- `evidence_for_actual_demand`；
- 可选 `knowledge_dimension`（第一阶段只做 shadow，不必持久化）。

Bloom reviewer 不输出 difficulty；difficulty/CAT 保持独立。

---

## 6. 与每个核心模块的耦合点

### M1 Supervisor

**价值**：保证一次对话里的动作不是工具拼接，而是 ECDL 闭环的一步。

**目标契约**：当本轮属于教学/练习/测评时，Supervisor 应能追踪 `claim → action → expected observation`。普通事实问答无需强行创建学习 claim。

**不做**：Supervisor 不自己评分学生，也不自行推断 Bloom/负荷；它只编排 M2/M3/M4/M10 的结果。

### M2 Student Model

**价值**：充当 ECDL Proficiency Model。

**保留现状**：BKT、capability projection、Bloom profile 各自保留语义和单一真相来源。

**理论边界**：LLM reviewer 无权直接修改 `p_known`；只有通过 M10 evidence gate 的有效学习行为才能进入写回路径。

### M3 Teaching Engine

**价值**：充当 ECDL Pedagogical Model，并由 CLT 约束讲解支持方式。

**核心接点**：

- `policy.py`：确定性 fallback；
- `decision_adapter.py`：受限 LLM 教学决策；
- `assistance`：CLT guidance continuum；
- `expected_observation / stop_condition`：ECDL 教学假设闭环；
- `recent outcomes / mistakes / misconceptions`：决策证据。

**不得做**：不能为了“符合 CLT”机械限制字数，也不能根据 grade 直接推断 mastery。

### Tutor / Prompt Registry

**价值**：将 M3 的教学决策落实为具体讲解。

所有理论提示都应通过 `prompts/registry.py` 版本化，且只注入短的、上下文相关的指令。禁止把整段教育理论教材塞进 system prompt。

CLT 主要控制：步骤分块、示例程度、信息整合、冗余、支持撤除；ECDL 要求必要时在讲解后产生一个与 claim 对齐的 `next_check`。

### M4 Question Generator / Quiz Tools

**价值**：充当 ECDL Task Model；RBT 是任务认知目标；M5 grounding 是事实边界。

保持 two-pass blueprint、constraint generation、grounding、rubric freeze。增强点是让 blueprint 明确 claim/evidence opportunity，并让 critic 验证 actual demand。

### M4 Quiz Critic

**价值**：题目送给学生之前的有效性审查。

优先级应是：

1. deterministic structural validity；
2. answer correctness；
3. grounding support；
4. intended Bloom ↔ actual demand；
5. evidence opportunity；
6. 非目标难度风险。

模型出错时保留现有降级策略；但“未验证”必须在后续 evidence quality 中保持可见，不能默认成已验证。

### M4 Structured Evaluator

**价值**：ECDL Evidence Model 的主要 LLM 解释器。

保留 frozen rubric、本地算分、枚举白名单、一次 repair、abstention。重点补 observability eligibility，尤其是 transfer / retention / self_check。

### M4 CAT / difficulty

**价值**：负责经验难度和自适应选择，是测量/选择机制，不是 RBT 的替代品。

**硬边界**：Bloom 不直接决定 CAT difficulty；CAT 也不重写题目的认知目标。两者正交。

### M5 Knowledge / RAG

**价值**：提供讲解和任务的事实依据，防止教材专属事实幻觉。

**硬边界**：检索到了某知识点只证明“系统有材料”，绝不证明“学生会这个知识点”。RAG evidence 是**内容 provenance**，不是 learner evidence。

### M7 Evaluation Intelligence

**价值**：评价教学策略长期是否有效。

M7 可以聚合：CLT risk、ECDL alignment、assistance transitions、后续学习结果，提出人审建议。但它不能仅因为 theory reviewer “评分更高”就宣称学习效果改善。

真正的策略价值要看后续行为指标，例如：next-check success、independent success、hint dependence、delayed retention、transfer、time-to-mastery。

### M8 UX Intelligence

**价值**：调整语言、形式与交互，而不改变教学内容。

CLT 的分段和信息整合可以为 UX 提供约束，但 M8 不应自行决定 student mastery 或 assessment verdict。

### M9 Learning Orchestration

**价值**：把 ECDL 单轮闭环放大到多日计划。

第一阶段不需要再加入另一套课程理论。M9 只需消费：M2 的证据状态、M3 的教学需求、M4 的未观测能力与 Bloom weaknesses，安排后续学习任务和复习。任务进入当天时仍走同一 ECDL/CLT/RBT 规则。

### M10 Skill Runtime / Evidence Gate

**价值**：这是 ECDL 最重要的“推断边界”之一。

当前 `EXPOSURE / SELF_REPORT / RESTATEMENT / SAME_FORM_TASK / VARIANT_TASK / TRANSFER` 证据等级和 mastery-write gate 应保留。理论层只补充“任务是否真的提供该 evidence opportunity”的来源信息，不绕过现有 gate。

---

## 7. 一个完整例子：从“不会电路等效电阻”到有效证据

假设学生多次在并联电阻判断上出错。

### 7.1 Proficiency Model

M2 显示：

- 对目标 concept 的 BKT 较低；
- recent mistakes 显示串并联关系识别错误；
- Bloom profile 在 Apply 上证据不足；
- capability projection 的 reasoning 仍 `not_observed`。

系统此时**不能**直接宣称“学生不会推理”，因为 reasoning 没有被有效观察过。

### 7.2 Pedagogical Model + CLT

M3 选择 `explain` + `full_demo`：

- 因为学生缺乏稳定 schema，先给一个完整电路图示例；
- 图中元件和节点标签与文字解释放在一起，降低 split-attention；
- 不同时引入戴维南等价等额外新知识；
- 推导分成“识别节点 → 判断连接关系 → 选择公式 → 计算”四段。

`expected_observation`：学生随后在一个同构图中，无需重新听完整定义，能够正确识别哪两个电阻并联并说明依据。

`stop_condition`：连续出现独立正确的关系识别后，撤到 `key_hints` / `independent`。

### 7.3 Task Model + RBT

如果本轮 claim 是 **Understand**，题目可以要求解释“为什么 R2、R3 并联”。

如果本轮 claim 是 **Apply**，题目应提供一个新电路并要求选择正确公式并求等效电阻。

如果本轮 claim 是 **Analyze**，任务应迫使学生分解拓扑关系或辨析一个错误解法；不能只把数值变大、计算变长然后标 `analyze`。

### 7.4 Evidence Model

Rubric 在学生作答前冻结，例如：

- c1：正确识别公共节点；
- c2：据节点关系判断并联；
- c3：选择对应等效关系；
- c4：计算过程与结果一致。

如果题目只要求最终数值，则 c1/c2 可能无法直接观察；评价器必须承认 `not_observed`，而不能从“算对了”反推关系理解一定正确。

### 7.5 Evidence Gate 与更新

通过 critic 验证、学生独立作答且 rubric 证据有效后，M10 才允许本次表现进入 M2。若学生是在 full_demo 后照抄，证据可以保留用于教学决策，但独立能力投影不能与无帮助作答等价。

随后 M3 根据新证据决定是否撤除支持。这就形成了完整 ECDL 闭环。

---

## 8. 明确禁止的错误实现

以下实现即使“看上去很教育学”，也不应进入项目：

1. **一个总分**：把 ECDL、CLT、Bloom 加权成 `education_score`；
2. **Bloom = difficulty**：规定 hard 必须 Analyze/Create，easy 必须 Remember；
3. **讲得长 = 认知负荷高**：只按字数判断 CLT；
4. **讲得短 = 教得好**：为了降负荷删除必要推理链；
5. **LLM 自报真实 cognitive load**：没有学生量表/行为/实验依据时声称“负荷为 0.72”；
6. **一次正确 = retention**：没有时间间隔仍评价长期保持；
7. **换数字 = transfer**：同结构同策略题不能自动算迁移；
8. **正确 = self_check**：未观察检查/修订过程不得推断自我检查；
9. **RAG 命中 = 学生掌握**：内容证据不能当 learner evidence；
10. **理论 reviewer 覆盖确定性规则**：LLM 不得推翻 frozen rubric、本地算分、候选集、grounding、evidence gate；
11. **每轮串多个 reviewer**：会增加延迟、成本和互相冲突，应尽量复用现有 LLM call；
12. **把理论全文塞入 prompt**：运行时只传与当前 action 相关的可执行条款；
13. **机械 guidance ladder**：不能规定“做对两题必升 independent”；证据与任务复杂度必须共同决定；
14. **用学段代替学生状态**：小学/本科是表达和任务上下文，不是 mastery proxy。

---

## 9. 实施策略：先证明 reviewer 可靠，再让它影响教学

本文不要求本次文档提交同步改代码。未来实现应按以下次序，而不是直接把理论规则写进 active prompt。

### Phase A — Contract / Trace

先定义 theory review schema、claim/evidence/task 标识和 trace 字段。所有判断都可回放、可定位模型/Prompt 版本。

验收：关闭 theory layer 时，当前系统行为 byte-level / semantic-level 不受影响；不得建立第二套 mastery storage。

### Phase B — Gold Set

由人工专家标注三类样本：

**讲解集**：覆盖不同学段、prior knowledge、概念复杂度、full demo/key hints/independent、文本/公式/语音场景，标 CLT design risk。

**命题集**：覆盖六个 Bloom process，特别构造“标签写 Analyze 但实际 Recall”“题目很难但只是 Apply”“表面换场景但无真实 transfer”等边界样本。

**证据集**：覆盖 frozen rubric、not_observed、assisted response、immediate vs delayed、same-form vs variant/transfer、self-check 有/无显式行为等。

### Phase C — Shadow

理论 reviewer 只记录，不改变学生看到的内容、不修改分数、不修改 mastery。

主要观察：

- 与专家标注的一致性；
- `not_observed` 是否被正确使用；
- 是否出现系统性过度推断；
- latency/cost；
- 同一输入在版本变更后的稳定性。

### Phase D — Limited Active

优先允许低风险行为生效：

- Tutor 的分段/信息整合建议；
- assistance 的有限调整；
- quiz critic 对 Bloom misalignment 的过滤/重生；
- structured evaluator 的 observability gate。

学生模型更新仍必须经过现有 M10 gate。

### Phase E — Outcome Validation

最终不能用“reviewer 给自己打高分”证明理论有效。至少观察：

- next-check independent success；
- hint dependence；
- repeated-error rate；
- delayed recall；
- transfer task success；
- time-to-mastery；
- re-learning rate。

只有这些学习结果改善，才说明理论进入产品之后真正创造了价值。

---

## 10. 测试与验收标准

以下阈值是**项目工程上线门槛建议，不是教育学界统一阈值**；正式数值应在 gold set 规模稳定后再冻结。

### 10.1 ECDL

- 每个会改变 M2 状态的评估必须能追溯到 task/question id 与 evidence；
- 无 evidence opportunity 的能力维度 100% 输出 `not_observed`，不得凭印象补全；
- exposure/self-report/restatement 不得绕过 M10 gate；
- assisted 与 independent evidence 在 capability projection 中保持可区分；
- rubric 必须在学生答案出现前形成；
- LLM 失败不得生成虚构证据。

### 10.2 CLT

建议 gold set 上：

- 对专家标注的高风险讲解，risk recall ≥ 0.90；
- `guidance_fit` / `fading_readiness` 等序数 verdict 与专家 weighted κ 目标 ≥ 0.70；
- 不允许只凭字符数判定高负荷；
- 不允许在没有测量数据时输出真实 cognitive-load 数值；
- active revision 后不得降低事实正确性或破坏学生显式格式要求。

### 10.3 RBT

建议 gold set 上：

- `actual_demand` 六分类 macro-F1 目标 ≥ 0.85；
- 对 `intended != actual` 的明显错配 recall ≥ 0.90；
- 不把 difficulty 当成 Bloom 标签输入的确定映射；
- critic 必须基于最低可行解法判断认知过程，而不是只看题干动词；
- Bloom profile 只消费经过正常评分的学习账本，不建立第二份行为日志。

### 10.4 Active gate

任何会直接阻断题目、改变评分或影响能力晋级的 theory rule，在进入 active 前必须：

- 有专家 adjudicated gold set；
- 有版本固定的 prompt/schema；
- 有 regression suite；
- 有 shadow 对照数据；
- 有 fail-open / fail-closed 明确语义；
- false hard-block rate 建议控制在 5% 以下再考虑上线。

---

## 11. 对当前代码的具体判断：哪些已经足够好，不应为了理论改掉

以下现有设计本身已经与选定理论高度一致，应当保留：

- `core/bloom.py` 的 canonical vocabulary 与“非机械阶梯”原则；
- `bloom_profile.py` 由 learning ledger 确定性聚合，而不是再调用 LLM；
- M3 `policy.py` 的纯规则 fallback；
- `decision_adapter.py` 的 trigger gate、候选集/枚举校验、无效则回退规则路径；
- `TeachingDecision.expected_observation` 与 `stop_condition`；
- quiz two-pass blueprint；
- textbook grounding 与 source refs；
- quiz deterministic structure check + independent critic re-solve；
- generation-time frozen rubric；
- structured evaluator 的 `not_observed`、本地算分、白名单和 abstention；
- M10 evidence gate 对 exposure/self-report/restatement 的限制；
- capability projection 的“无证据不宣称会”、多 rubric family 与 assisted/independent 区分；
- BKT 作为 M2 掌握度更新机制；
- shadow/active 模式与“智能层可关、失败可降级”的全项目约定。

理论引入的目的不是重写这些架构，而是给它们一个一致的教育推断语义，并补齐目前仍由 prompt 直觉承担的有效性检查。

---

## 12. 最终架构定义

Next Tutor Agent 的教育理论基础正式定义为：

> **以 Evidence-Centered Design for Learning（ECDL）作为“学生状态—教学行动—任务—学习证据—状态更新”的总架构；以 Cognitive Load Theory（CLT）约束讲解、示例、分段与帮助撤除；以 Revised Bloom’s Taxonomy（RBT）定义练习和测评要求的认知过程。**

三者在工程上的边界为：

- **ECDL：为什么可以这样推断、下一步为什么这样教；**
- **CLT：这次讲解应该怎样组织、给多少支持；**
- **RBT：这道题究竟要求学生做哪一种认知加工。**

最终评价体系不追求“理论覆盖数量”，只追求三件事：

1. **推断有证据**；
2. **讲解与学生状态相匹配**；
3. **题目真正测到了它声称要测的认知过程**。

这三点能够直接落在当前 M2/M3/M4/M10 的现有接口上，并可通过 trace、gold set、shadow/active 与真实学习结果验证，是本项目后续教育理论工程化的唯一主线。

---

## 参考文献与权威来源

### ECD / ECDL

1. Hansen, E. G. (2011). **Evidence-Centered Design for Learning**. ETS Research Memorandum RM-11-02. Educational Testing Service.  
   https://www.ets.org/research/policy_research_reports/publications/report/2011/imbu.html

2. Mislevy, R. J., Almond, R. G., & Lukas, J. F. (2003). **A Brief Introduction to Evidence-Centered Design**. ETS Research Report RR-03-16.  
   https://www.ets.org/research/policy_research_reports/publications/report/2003/hsgs.html

3. Mislevy, R. J., Steinberg, L. S., & Almond, R. G. (2002). **Design and analysis in task-based language assessment**. *Language Testing, 19*(4), 477–496.  
   https://doi.org/10.1191/0265532202lt241oa

4. Feng, M., Hansen, E., & Zapata, D. (2009). **Using Evidence Centered Design for Learning (ECDL) to Examine the ASSISTments System**. AERA 2009. SRI International.  
   https://www.sri.com/publication/education-learning-pubs/using-evidence-centered-design-for-learning-ecdl-to-examine-the-assistments-system/

### Cognitive Load Theory

5. Sweller, J., van Merriënboer, J. J. G., & Paas, F. (2019). **Cognitive Architecture and Instructional Design: 20 Years Later**. *Educational Psychology Review, 31*, 261–292.  
   https://doi.org/10.1007/s10648-019-09465-5

6. van Gog, T., Paas, F., & Sweller, J. (2010). **Cognitive Load Theory: Advances in Research on Worked Examples, Animations, and Cognitive Load Measurement**. *Educational Psychology Review, 22*, 375–378.  
   https://doi.org/10.1007/s10648-010-9145-4

7. Paas, F., van Gog, T., & Sweller, J. (2010). **Cognitive Load Theory: New Conceptualizations, Specifications, and Integrated Research Perspectives**. *Educational Psychology Review, 22*, 115–121.  
   https://doi.org/10.1007/s10648-010-9133-8

8. Schwonke, R., Renkl, A., Krieg, C., Wittwer, J., Aleven, V., & Salden, R. (2009). **The worked-example effect: Not an artefact of lousy control conditions**. *Computers in Human Behavior, 25*(2), 258–266.  
   https://doi.org/10.1016/j.chb.2008.12.011

### Revised Bloom’s Taxonomy

9. Anderson, L. W., & Krathwohl, D. R. (Eds.). (2001). **A Taxonomy for Learning, Teaching, and Assessing: A Revision of Bloom’s Taxonomy of Educational Objectives**. Longman.

10. Krathwohl, D. R. (2002). **A Revision of Bloom’s Taxonomy: An Overview**. *Theory Into Practice, 41*(4), 212–218.  
    https://doi.org/10.1207/s15430421tip4104_2

11. Cornell University Center for Teaching Innovation. **Bloom’s Taxonomy**（教学应用说明，引用 1956 原版并说明 2001 修订框架）。  
    https://teaching.cornell.edu/resource/blooms-taxonomy

### 阅读说明

本文优先使用 ETS、同行评议期刊、学术出版社与大学教学中心来源。理论文献用于确定**概念边界和设计原则**；具体 `pass/warn/fail` 阈值、gold-set 指标和 active gate 数值属于 Next Tutor Agent 的工程决策，必须通过本项目自己的专家标注与学习结果数据验证，不能把项目阈值误写成教育学界的普遍定律。
