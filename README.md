# Next Tutor Agent · Edu_Agent

> **以教材为事实源、以学生真实表现为证据、以任务设计驱动学习闭环的教育智能体。**
>
> Next Tutor Agent 不把“聊天、知识库、出题、评价”做成彼此独立的功能页，而是把它们连成一条可追踪的学习链：**教材被解析为可检索事实与知识图谱；对话围绕教材证据进行讲解；练习先定义要观察什么再命题；学生作答进入统一学习证据账本；评价只在证据允许的范围内形成学习判断，并继续影响后续讲解、练习与学习安排。**

架构与实现细节见 [docs/DESIGN.md](docs/DESIGN.md)，教育理论与评价契约见 [docs/Related-Educational-Theory.md](docs/Related-Educational-Theory.md)，当前 CAT 结构化题图链见 [docs/ASSESSMENT_ILLUSTRATION_PIPELINE.md](docs/ASSESSMENT_ILLUSTRATION_PIPELINE.md)。

---

## 项目主线

```text
教材 PDF
  ↓
文本层提取 / 扫描页 OCR / 图表与印刷页码收集
  ↓
Structured Chunker V2
  ├──→ BM25 索引 ──→ 教材检索 ──→ 对话讲解 / Grounded Quiz
  └──→ 后台知识图谱 ──→ 概念、前置关系、学习路径与评价 Scope

学生对话 / 练习作答
  ↓
统一 Learning Evidence Journal
  ↓
ECDL 证据解释 + RBT 认知过程描述 + 帮助条件约束
  ↓
当前学习判断 / 下一验证任务 / 下一教学动作

                    ┌── 笔记：沉淀对话、教材、错题
上述状态与图谱 ────┤
                    └── 编排：目标、周计划、今日任务、SM-2 复习
```

项目的核心不是“模型回答得像老师”，而是让**教材事实、教学行为、学生表现、评价结论和下一步教学**之间存在明确的数据关系。

---

## 1. 教材解析：先建立事实源，再建立 RAG 与知识图谱

教材不是直接塞进大模型上下文。当前实现把“原始教材”“检索索引”和“知识图谱”分成三层，各自承担不同职责。

### 1.1 从 PDF 得到可引用的教材事实

教材上传后，系统先建立稳定的文本事实源：

- 对有可用文本层的 PDF 直接提取正文；
- 对文本层过稀的扫描 PDF 识别为扫描文档，并进入逐页 OCR 路径；
- 教材中的表格、插图等内容可被收集为结构化图/表描述块；
- PDF 的印刷页码会尽量保留为 `printed_page` 元数据，使“PDF 第几页”和“书上第几页”不必混为一谈；
- 解析结果先成为教材 `.txt` 事实源，RAG 和知识图谱都只是可以重新构建的派生数据。

这意味着索引或图谱需要刷新时，不必把 OCR 与原始解析全部重做；`rag_graph` 刷新会优先复用已经得到的教材文本。

### 1.2 Structured Chunker V2：不是按固定字符粗切

教材文本随后进入 `Structured Chunker V2`。切片会尽量保留教材结构信息，并把定义、定理、例题、图表、课题、页码等作为检索可用的结构元数据，而不是只留下匿名文本块。

检索侧还会使用 `parent / prev / next / lesson` 等链式信息重建上下文：BM25 命中的只是入口，回答需要时可以继续读取完整片段与相邻片段，或把同一课题中的若干命中块恢复成更接近课文的可读上下文。

乱码块不会因为“有文本”就进入索引。当前 RAG 构建包含索引准入检查，被判定为乱码的 chunk 会被排除，不生成 BM25 token。

### 1.3 BM25 先可用，图谱后台继续构建

教材上传后的首要目标是**尽快可查**。当前主检索轨是纯 Python BM25，并针对中英文/CJK 文本做检索处理；教材达到 `bm25_ready` 后即可参与知识检索，不需要等待大模型完成整本知识图谱。

如果配置了 embedding provider，还可以启用 Chroma 向量轨，与 BM25 通过 RRF 融合。向量检索是增强项，不是教材可用性的前置条件。

### 1.4 知识图谱与 RAG 是两件事

知识图谱在后台从教材事实源继续构建：

1. 先按章节组织教材内容；
2. 用一次骨架调用补充学科/学段信息（用户已有选择时优先使用用户信息）；
3. 各章节独立抽取概念及关系，长章节会做分层取样，避免只读取章首而遗漏章末知识；
4. 最后通过确定性合并生成教材图谱，对 `PREREQUISITE` 关系执行 DAG 守卫，并处理跨章引用和主图锚定。

图谱因此不是“把 chunk 连成一张图”，而是单独描述**概念、前置关系、相关关系、应用/易错关系等学科结构**。这些概念身份与 prerequisite 链会继续被对话上下文、学习评价 Scope、练习目标和学习编排消费。

多册教材还可以组成教材组，在组级别建立跨卷的统一知识关系。

---

## 2. 对话学习：回答必须知道“依据什么”，评价必须知道“学生做了什么”

### 2.1 教材在场时，先检索再回答

普通问答、`generate_quiz` 和 `fit_quiz` 使用同一套已授权教材空间。系统会先判断当前问题是否需要教材 grounding，再由 `knowledge_search` 检索相关片段。

检索结果经过证据门后分成：

- `FOUND`：有足够教材证据，可据此回答；
- `PARTIAL`：只有局部或较弱依据，回答时保留边界；
- `NOT_FOUND`：当前授权教材中没有找到足够依据。

当用户明确要求“根据教材回答/出题”时，`NOT_FOUND` 不是鼓励模型凭常识补齐，而是一个合法结果：系统可以直接说明没有检索到依据；严格教材出题时不会注册一道人造的“教材题”。

### 2.2 对话不是一次性问答，而是教学状态的一部分

教学引擎可以在 `explain / practice / quiz / review / clarify / summarize` 等动作之间选择，并为下一任务指定 `full_demo / key_hints / independent` 的帮助级别。这里的重点不是给学生套一个永久“学习风格”标签，而是依据当前任务、已有证据和之前的帮助条件决定**下一步教什么、给多少支持、希望观察到什么**。

学段信息主要约束语言、抽象程度、例子和任务难度的表达方式；它不会被直接当成学生能力证据。

### 2.3 学生在聊天中的真实表现也可以成为证据

对话评价器只读取学生真实表达出来的理解、应用、比较、论证、自我纠错等行为。以下内容不会被直接解释成“学会了”：

- “懂了”“谢谢”“继续”；
- 单纯请求老师讲解；
- 学生对自己水平的自报；
- 老师刚刚讲对了一段内容；
- 系统历史摘要里写过“已掌握”。

如果学生在对话中确实解释了一个概念、指出反例、修正了自己的错误，系统才会把对应原文片段、任务机会、概念和限制条件写入学习证据链。测评已经拥有的答案片段也会去重，避免同一次表现被对话评价和测评评价重复记账。

### 2.4 语音讲解仍复用同一学习链

语音模式是对现有对话的交互层扩展：浏览器使用 push-to-talk 的语音识别得到最终文本，后端继续走同一对话/检索/教学路径，再由可选的 MeloTTS sidecar 播报。语音不是另一套“语音老师”逻辑，因此教材 grounding、会话状态和后续学习证据仍然保持一致。

---

## 3. 笔记与学习编排：作为学习闭环的辅助层

这两部分不是项目主线，但会消费前面的教材、对话和评价结果，而不是各自成为孤岛。

**笔记仓库**使用 Markdown 与双链组织个人学习内容，可以从对话、教材和错题等来源生成或整理笔记；“温故”类笔记可以接入复习调度。笔记的作用是把一次聊天中的内容沉淀成之后还能继续检索、编辑和复习的个人资料。

**学习编排**把长期目标拆到周计划与今日任务，并结合知识图谱中的 prerequisite 关系、已有学习证据和复习状态安排后续任务。间隔复习使用独立的 SM-2 调度状态；它负责“什么时候复习”，不会被当成学生知识掌握度本身。

---

## 4. 出题：从“让 LLM 写一道题”改成可审核的 Task Design

出题是当前项目最重要的闭环之一。完整路径不是一次 prompt 直出，而是：

```text
教材/参考题 grounding
        ↓
ECDL 命题蓝图
  ├─ target_claims           想验证什么学习主张
  ├─ intended_processes      需要什么认知过程（RBT）
  ├─ knowledge_types         事实/概念/程序/元认知
  ├─ evidence_opportunities  学生必须产出什么，才有资格观察
  ├─ rubric_draft            评价标准草案
  ├─ task_family / novelty   与既有任务是什么关系
  └─ construction_brief      题面构造要求
        ↓
正式题目 + 冻结量规
        ↓
确定性结构检查
        ↓
独立 critic 重解 + Grounding / Evidence Opportunity / RBT 对齐审核
        ↓
通过后交付学生
```

### 4.1 教材检索发生在命题蓝图之前

如果本轮要求根据教材出题，系统先检索教材，再把允许使用的概念与材料片段提供给蓝图轮。这样第一轮“想考什么”本身就受到教材事实约束，而不是先自由发散、第二轮再勉强把题目贴回教材。

蓝图收到的教材片段明确被当成**数据**，不能作为 prompt 指令；命题只能使用片段实际支持的概念、条件、公式与结论。

### 4.2 RBT 用来描述认知过程，不用来机械“升级”学生

项目使用修订版 Bloom Taxonomy 的六种认知过程作为共享词汇：

`remember → understand → apply → analyze → evaluate → create`

但代码明确禁止把它实现成“答对 N 次就升一级”的阶梯。出题器可以根据考查目的自由选择、混合、回访不同认知过程；`hard` 也不等于 `analyze/create`。

RBT 在这里解决的是：**这道题究竟要求学生做哪种认知加工？** 例如，如果想观察“分析”，题面就必须真正要求比较、分解关系或定位错误；不能只因为题干里出现“分析”两个字，就给一道最终仍靠记忆定义作答的题贴上 analyze 标签。

### 4.3 Evidence Opportunity：想评价什么，就必须让学生有机会表现什么

ECDL 蓝图会显式设计 `evidence_opportunities`。如果系统希望判断学生是否能解释因果关系，就必须要求学生给出解释；如果题目只收一个最终数字，就不能事后声称“已经观察到完整推理过程”。

这条约束把“题目质量”从文风问题转成了可检查的证据问题：**Task 是否真的给了学生产生所需证据的机会？**

### 4.4 题目生成与量规一起冻结

正式题目生成时会同时产生用于之后评价的 rubric。学生答案提交之前，题目、答案、等价解说明、rubric criterion、教材来源、认知过程等信息会被冻结成 TaskSnapshot。

因此阅卷阶段不能看到学生答案后再临时修改评分标准，也不能为了“让模型的评价看起来合理”反向改题。

### 4.5 Generator-Critic：题目交给学生前再独立重解

题目生成后先经过两层质量门：

1. **确定性检查**：题干/答案不能为空；选择题必须有合法、非重复选项；答案键必须存在；解析必须满足基本结构要求。结构错误的题直接丢弃。
2. **独立 critic**：审核器逐题重新求解，检查答案是否成立、教材依据是否支持、实际要求的 RBT 认知过程与设计目标是否一致、rubric 是否真的覆盖题目要求、evidence opportunity 是否存在。

计算题 critic 还会先生成短的重解草稿，再基于重解结果给 verdict，减少“凭印象看答案”的审核。

审核结论是逐题的 `passed / rejected / revision_required / unreviewed`，不会用“整套题整体不错”替代单题验证。没有被真正审核到的题也不会冒充 `passed`。

### 4.6 拟合出题不是简单换数字

`fit_quiz` 从一道人类提供的参考题出发，先逆向分析它的知识点与考查结构，再生成同考点变式。当前实现明确区分三类变式：

- **Context migration**：结构相近，换到不同真实情境；
- **Structure mutation**：保留知识目标，改变题目结构；
- **Convergence variation**：保留目标答案模式，但改变建立问题的方式。

参考题本身就是 `fit_quiz` 的事实来源；如果本轮还存在已检索的教材证据，则复用该证据，而不会为了“形式统一”再做一次无关检索把其它教材内容混进题目。

### 4.7 SVG 题图：把图变成可验证的题面，而不是装饰图片

当前题图不是让图像模型随意生成一张图片。蓝图先判断该题是否真的需要图，并产出 `illustration_brief`；只有需要时才进入结构化 SVG 路径。

SVG 有一个封闭语法边界：允许的元素、属性、路径、marker、尺寸、节点数、深度和总字节数都有约束。服务端使用安全 XML 解析器读取模型结果，再从白名单语法**重新构造** SVG，而不是把模型原始字符串直接透传到浏览器；外部资源、危险节点、超预算几何等都会被拒绝。

这套设计有几个实际收益：

- 适合受力图、几何图、坐标示意、关系图等需要清晰线条与标签的教学题面；
- SVG 是结构化数据，可以做机器级安全检查，而不是只能“看起来像对”；
- 图与题干会一起进入逐题 critic，检查图文一致性以及是否泄露答案；
- 图题审核失败时不能冒充已验证题目。

当前 CAT 的题图采用**文字题先生成并完成核心审核，再对同一冻结任务补充 SVG**的两阶段路径；聊天中的 `generate_quiz / fit_quiz` 保留同步结构化题图链。这样配图失败不需要重新生成另一道语义不同的题，也不会让“画图”主导题目本身的正确性。

---

## 5. 评价：不是“LLM 给学生打个分”，而是证据受限的学习推断

项目当前的学习评价总架构采用 **Evidence-Centered Design for Learning（ECDL）**。它关心的不是如何生成一个更漂亮的总分，而是以下链条是否成立：

```text
Claim
想知道学生会不会什么
  ↓
Task / Opportunity
任务是否真的允许学生表现它
  ↓
Observable Evidence
学生实际说了什么、写了什么、选了什么
  ↓
Inference + Limits
这些证据最多支持到什么范围，哪些仍不能推断
  ↓
Pedagogical Action
下一步应该讲解、提示、练习、复习还是继续验证
  ↓
Expected Observation
下一步之后希望观察到什么新的证据
```

### 5.1 所有学习判断都必须能回到原始表现

统一评价协议中的 ObservationClaim 会保存：

- 对应概念；
- 学生当前可定位的原文/答案片段；
- 这次表现发生在哪个 task opportunity 中；
- `warrant`：为什么这段表现支持/反驳某个主张；
- `limits`：本次证据不能推出什么；
- 涉及的认知过程与知识类型；
- 是否属于 transfer / retention / self-check 等特殊证据条件；
- 可能的其它解释。

因此“模型觉得他会了”不是一个合法的学生状态来源。

### 5.2 评价的是“在什么条件下做到了什么”

帮助条件会被记录在学生作答之前：请求提示、看过答案、看过完整 worked example、教师追问、先前接触等都会影响证据解释。

例如，在 `full_demo` 后成功模仿，不应与完全独立作答具有相同含义；答案已经揭晓后的重答是练习表现，也不能包装成独立检索或延迟保持证据。

这也是项目中 **Cognitive Load Theory（CLT）** 当前最直接的工程落点之一：教学侧可以在 `full_demo → key_hints → independent` 之间调整支持，评价侧同时记录这些支持，防止“帮助越多，系统反而越自信地认为学生独立掌握”的反常现象。

当前代码没有单独计算一个“认知负荷分数”；CLT 主要用于**帮助设计、示例/提示强度与支持逐步撤除**。

### 5.3 选择题判分与学习解释分离

对于选择题，正确/错误可以由服务端依据冻结答案确定性判定，LLM 不能改写这个 verdict。但一个选项本身通常只能提供有限证据：系统不能仅凭学生选了某个干扰项，就直接确诊一种复杂的深层误解。

对于填空/简答等开放答案，评价器逐项检查冻结 rubric，区分 `correct / partial / wrong` 与“某一 criterion 根本没有被观察到”；有效等价解可以按量规接受，量规自身有缺陷时则记录 rubric issue，而不是偷偷改标准。

### 5.4 学生状态不是百分比掌握度

当前主线已经移除旧的数值掌握度/BKT 真相链。概念状态使用证据式类别：

- `not_observed`：尚无学习证据；
- `emerging`：已有局部证据；
- `supported_in_scope`：在当前条件与范围内已有支持；
- `fragile`：存在明确待解决点；
- `conflicting`：不同证据之间仍有冲突。

这些类别不是 0/25/50/75/100 的另一种写法。系统会保留正反证据、帮助条件和范围，而不是用一个全局分数覆盖它们。

### 5.5 Transfer、Retention 不能靠模型“感觉”出来

`transfer / retention / self_check` 都有来源条件。相同模板换数字不自动算迁移；刚讲完立刻答对不自动算延迟保持；学生没有实际自检行为时也不能补写 self-check。

时间间隔、帮助时序等事实由服务端写入，模型无权自行声明“已经满足延迟复习条件”。

### 5.6 CAT：自适应测评，但不伪装成当前尚未实现的 IRT

当前 CAT 会根据本次 assessment 中最近的有效作答结果动态调整 1–5 档任务难度，并结合 ECDL continuation 决定继续、针对剩余主张 probe，或在当前主张已有足够证据时结束。未验证的结果不会参与难度步进。

当前实现**没有 IRT 能力参数估计，也没有隐藏的“mastery 阈值”**。因此项目不会把启发式自适应难度包装成 IRT/CAT 心理测量模型。测评报告保留每道题、每次作答、当前评价状态与停止原因，并继续回到统一 learning evidence journal。

---

## 6. 教育理论如何真正进入系统

项目当前正式采用一个总架构和两个教学/认知子理论；其它算法只在自己负责的局部使用。这里刻意不堆砌理论名称。

| 理论 / 方法 | 在项目中回答的问题 | 当前工程落点 |
|---|---|---|
| **ECDL** · Evidence-Centered Design for Learning | “凭什么从这次表现得到这个学习判断？下一步为什么这样教？” | 命题蓝图、Task/Evidence Opportunity、冻结 rubric、Learning Evidence Journal、ObservationClaim、ConceptJudgment、NextProbe |
| **RBT** · Revised Bloom’s Taxonomy | “任务实际要求哪一种认知加工？” | `remember / understand / apply / analyze / evaluate / create` 共享词汇；蓝图设计；critic 的实际认知过程审核；评价证据元数据 |
| **CLT** · Cognitive Load Theory | “这个学生在当前任务下需要多少教学支持？” | worked example / hint / independent 帮助设计，支持逐步撤除，帮助条件写入证据并限制后续推断 |
| **SM-2** | “什么时候安排下一次复习？” | M9 间隔复习与笔记温故调度；不承担学生能力评价 |

三个教育理论的职责不会被混成一个“教育质量总分”：

- ECDL 是**推断与教学闭环架构**；
- RBT 是**认知任务描述语言**，不是能力等级；
- CLT 是**教学支持设计依据**，不是学生评分器。

这种分工也决定了系统不会因为“理论名字更多”就自动更专业。只有能进入真实数据、决策接口和验收标准的理论，才应该成为正式架构的一部分。

---

## 7. 关键代码地图

| 能力 | 主要实现 |
|---|---|
| 教材解析 / OCR / 图表 | `backend/app/core/pdf_ocr.py` · `multimodal_parser.py` · `figure_harvest.py` |
| Structured RAG / BM25 | `backend/app/core/structured_chunker.py` · `retriever.py` · `rag_index.py` · `evidence_gate.py` |
| 教材知识图谱 | `backend/app/agents/knowledge/textbook_builder.py` · `graph.py` · `context_builder.py` |
| 对话与工具编排 | `backend/app/api/v1/chat.py` · `backend/app/agents/` · `backend/app/tools/` |
| 教学决策 | `backend/app/agents/teaching_engine/` |
| 两轮命题蓝图 | `backend/app/core/quiz_design.py` |
| 普通 / 拟合出题 | `backend/app/tools/quiz.py` · `fit_quiz.py` |
| 出题审核 | `backend/app/core/quiz_verify.py` |
| SVG 题图 | `backend/app/core/quiz_illustration.py` · `quiz_illustration_enrichment.py` |
| 测评 / CAT | `backend/app/agents/assessment/` · `backend/app/api/v1/assessment.py` |
| 统一学习评价 | `backend/app/agents/student_model/evaluation/` |
| 教育理论 Prompt 契约 | `backend/app/prompts/learner_evaluation.py` · `backend/app/core/bloom.py` |
| 笔记 | `backend/app/api/v1/notes.py` · `backend/app/core/notes_*` |
| 学习编排 / SM-2 | `backend/app/agents/learning_orchestration/` |
| 前端 | `frontend/src/`（Next.js 16 + React 19 + TypeScript） |

---

## 8. 技术栈

- **Frontend**：Next.js 16 · React 19 · TypeScript · Tailwind CSS v4 · Zustand · KaTeX
- **Backend**：FastAPI · Python 3.11 · OpenAI-compatible async LLM client
- **Agent**：Supervisor + Tool/Skill contracts + function calling + trace
- **Retrieval**：Structured Chunker V2 · BM25 · optional Chroma vector lane · RRF · evidence gate
- **Evaluation**：file-backed JSON/JSONL journal + strict Pydantic schemas + frozen task/rubric contracts
- **Voice**：browser Speech Recognition + optional MeloTTS sidecar
- **Testing**：Python unittest · TypeScript/ESLint · Playwright E2E

---

## 9. 快速开始

环境要求：Python 3.11、Node.js、pnpm。

```bash
# Backend
conda create -y -n edu_agent -c conda-forge --override-channels python=3.11 pip
conda activate edu_agent
pip install -r backend/requirements.txt

# Frontend
cd frontend
pnpm install
cd ..

# Configuration
cp .env.example .env
# 至少配置一个 OpenAI-compatible LLM：
# LLM_BASE_URL / LLM_API_KEY / LLM_MODEL

# Start
./start.sh
# 开发模式：./start.sh dev
```

常用可选能力：

- `EMBEDDING_PROVIDER=openai|local`：启用可选向量检索轨；
- `MULTIMODAL_*`：配置视觉/OCR 模型能力；
- `VOICE_TTS_PROVIDER=melo`：启用 MeloTTS 语音播报。

BM25 不依赖向量模型即可工作；教材索引也不需要等待知识图谱构建完成。

---

## 10. 测试

```bash
# Backend
cd backend
python -m unittest discover -s tests

# Frontend static checks + build
cd ../frontend
pnpm exec tsc --noEmit
pnpm exec eslint src/
pnpm build

# E2E
pnpm test:e2e
```

现有测试覆盖的不只是接口能否返回 200，也包含教材上传后 BM25 可查、严格教材 `NOT_FOUND` 不生成伪教材题、Grounded Quiz 来源、题目审核、评价 schema/证据边界、CAT 生命周期、知识图谱 prerequisite 与学习编排等主链路契约。

---

## 11. 当前实现边界

为了让 README 与代码保持同一口径，当前版本明确采用以下边界：

- **BM25 是基础检索轨；向量检索是可选增强。**
- **RAG 与知识图谱是两套不同派生结构。** RAG 负责找教材证据，图谱负责描述概念及关系。
- **学习评价没有 BKT 百分比掌握度真相源。** 当前状态以统一学习证据 journal 和证据式概念判断为准。
- **RBT 不做机械认知等级升级。** 它是任务与证据的共享认知词汇。
- **CLT 当前没有独立的认知负荷数值估计器。** 它主要约束讲解、示例、提示和帮助撤除。
- **当前 CAT 不是 IRT 实现。** 它使用 task-local 作答结果调节难度，并结合证据评价决定继续或停止。
- **题图是受限、可审核的 SVG 题面。** 不是任意 HTML/SVG 透传，也不是为了视觉效果强制给每道题配图。

这些边界是有意保留的：项目优先保证“系统实际观察到了什么，就只推断到什么”，而不是通过增加更多评分、概率或理论标签制造虚假的精确性。

---

## License

See [LICENSE](LICENSE).
