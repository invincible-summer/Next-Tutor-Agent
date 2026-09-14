# Next Tutor Agent · Edu_Agent

> 一个**有长期记忆、会自我迭代的一体化虚拟教育智能体**。
> 不是问答机器人,而是为小学 / 初中 / 高中 / 本科学生构建
> 「学习目标 → 知识理解 → 练习训练 → 能力评估 → 策略调整」完整闭环的
> 长期陪伴式私人学习空间。

[English](#english) · 架构主文档 [docs/DESIGN.md](docs/DESIGN.md) 

---

## 它是什么

大多数「AI 家教」只是把通用大模型包了一层皮:问一句答一句,答完即忘,
出的题停留在记忆复述层,对学生没有长期了解。Edu_Agent 走的是另一条路:

- **长期记忆**:它记得你学过什么、错在哪里、喜欢怎样的讲解方式,跨会话、跨周持续累积,且记忆有边界、可审计、可遗忘。
- **专有化适配**:每一次真实作答都作为学习证据进入统一评价账本,评价只基于可定位的原始表现与当时的帮助条件;教学引擎据此在六种教学模式间切换。同一个概念,对小学生讲故事、对本科生讲证明。
- **自我迭代**:每一次教学互动都被记录、诊断、聚合;系统会提出教学改进建议,经人工确认后真正改变后续教学行为,并可随时吊销回滚。
- **一体化**:十余个智能模块不是松散拼装——学生模型驱动教学引擎,所有测评结果写入同一份学习证据账本,编排层消费记忆与画像,知识图谱贯穿检索、出题与路径规划。模块虽多,共用同一个学生、同一份证据。

产品形态是**学生学习空间(Learning Workspace)**:以流式对话为核心,辅以学习总览、
知识图谱、学习计划、学习编排、测评中心、记忆中心、资料中心、笔记仓库、系统洞察、
我的画像等模块页。

## 功能亮点

**学习与教学**
- 流式对话(SSE):实时推送思考过程、回答增量与工具进度;工具卡片结构化展示来源与页码。
- 自适应教学引擎:六模式状态机(导入/讲解/纠偏/练习/复习/挑战),跨轮教学记忆,动态难度保持在最近发展区。
- 学段去僵化:每个对话可选学段或「自动」;显式学段注入四学段 × 七维度(语言/抽象/例题/结构/鼓励/难度锚点/典型错因)强约束,讲解、出题、批改同步切换语言风格与深度。
- 交互体验适配:只改「怎么表达」不改「教什么」——语气、节奏、篇幅、图示偏好随学生反馈滞后调整,防止过度反应。

**教材、资料与检索**
- 教材库:上传任意教材 PDF(≤256MB,扫描版自动逐页视觉 OCR),自动结构化切块、索引并后台构建**专属知识图谱**;上下册/分册可编为教材组,构建跨卷统一谱系;管理员可维护全账号可选的公用教材库。
- 混合检索:BM25(CJK 分词,常驻)+ 可选向量轨(Chroma,RRF k=60 融合);结构化切块 V2 保留定义/定理/例题块完整,图表与印刷页码双轨标注。
- 证据门与反幻觉:查询核提取、标题匹配重校准、FOUND/PARTIAL/NOT_FOUND 分级证据;上传资料或明确要求「根据教材回答」时强制先检索,未命中禁止编造;检索不到就如实说检索不到。

**练习与测评**
- 高质量出题:两轮制「命题蓝图 → 生成」替代一步直出;generator-critic 独立重解审题,错题、错答案、过浅的题在投递前丢弃重生成;拟合出题从参考题拆解考点做情境迁移/结构反转等五种变式,而非换数字。
- 交互批改:选择题本地判分、填空简答 LLM 三级评分(对/部分对/错)+ 思路讲解;所有作答统一写入学习账本,错题本跨会话聚合,可一键出变式重练。
- CAT 自适应测试:不固定题数,按作答动态调难度,四条停止规则;每道题在出题时冻结量规,判分与语义评价分离,只有真实作答证据才进入学习账本。

**记忆与编排**
- 记忆生命周期:有界 prompt 画像(最近 5–30 个会话窗口可选)、策略成功率聚合、独立学习结果账本;删除对话可选择永久遗忘,回收站 7 天可恢复,账号注销彻底清除。
- 学习编排:多个长期目标 → LLM 周计划(校验门 + 确定性回退)→ 今日任务;SM-2 间隔复习;未完成任务跨天结转,人工安排永不被自动重规划覆盖。
- 笔记仓库:Obsidian 式 Markdown 仓库 + 每篇笔记专属智能体(问答/计划/授权三模式);AI 可基于对话、教材、错题本自动写笔记,温故卡直接接入 SM-2 复习队列。
- 语音通话(可选):电话式 push-to-talk 讲解,浏览器原生语音识别 + 本地 MeloTTS 播报,公式同步「上黑板」。

**账户与运维**
- 账户体系:注册/登录/自助注销(JWT + bcrypt),每用户数据物理隔离,游客模式零配置可用;管理员管理公用教材库与账号。
- 工作区(类 ChatGPT Projects):跨对话共享教材、资料与公共记忆,按账号隔离。
- 可观测:每轮对话全链路 Trace(决策链/prompt 版本/token 用量)落盘可查;后端 1600+ 项 unittest 回归;仓库不变量脚本守护公共资产/隐私边界与旧算法零残留。

## 系统架构(M0–M10)

```
浏览器 ──> Next.js 前端 ──REST/SSE──> FastAPI 后端 ──> LLM(OpenAI 兼容,必配)
                                             ├──> 向量模型接口(可选)
                                             └──> 视觉模型(可选,识题/OCR)
```

| 层 | 职责 | 一句话 |
|----|------|--------|
| M0 | 身份基础设施 | 用户是谁、数据属于谁(JWT + 全量数据隔离) |
| M1 | 任务智能 Supervisor | 这一轮对话怎么完成:理解 → 规划 → 工具执行 |
| M2 | 学生模型 | 这个学生会什么:画像 + 统一学习评价账本(证据式) |
| M3 | 教学引擎 | 现在该怎么教:六模式状态机 + 跨轮记忆 |
| M4 | 测评智能 | 真的学会了吗:三级评分 + CAT 自适应 |
| M5 | 知识智能 | 系统知道什么:教材驱动的知识图谱 |
| M6 | 记忆智能 | 长期记忆的生命周期:有界、可撤销、可遗忘 |
| M7 | 评估改进智能 | 教师自己是否越来越好:诊断 → 提案 → 人工确认部署 |
| M8 | 交互体验智能 | 怎么表达最适合这个学生 |
| M9 | 学习编排智能 | 未来几周到几个月怎么学:目标 → 周计划 → 今日任务 |
| M10 | 学习能力运行时 | 工具/能力的契约、决策与证据门 |

每个智能层都有独立开关,关闭任一层自动降级、不影响对话主链路。
模块职责、数据流、接口契约与存储布局的完整描述见 [docs/DESIGN.md](docs/DESIGN.md)。

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Next.js 16 · React · TypeScript · Tailwind CSS v4 · Zustand |
| 后端 | FastAPI · Python 3.11 · OpenAI SDK(异步流式) |
| Agent | 自研轻量框架:Supervisor 编排 + Skill Registry + ReAct function-calling + 全链路 Trace |
| 检索 | BM25(常驻)+ Chroma 向量(可选)· RRF 融合 · 结构化切块 V2 · 证据门 |
| 语音(可选) | 浏览器 Speech Recognition + 本地 MeloTTS sidecar |
| 持久化 | JSON 文件(原子写 + 文件锁),零数据库依赖 |
| 认证 | JWT(PyJWT)+ bcrypt |

## 快速开始

环境要求:Python 3.11(推荐 Miniconda)、Node.js + pnpm。

```bash
# 1. 后端环境
conda create -y -n edu_agent -c conda-forge --override-channels python=3.11 pip
conda activate edu_agent
pip install -r backend/requirements.txt        # 自动应用 constraints.txt

# 2. 前端依赖
cd frontend && pnpm install && cd ..

# 3. 配置(必填:任一 OpenAI 兼容端点,默认推荐 DeepSeek)
cp .env.example .env
#    编辑 .env:LLM_BASE_URL / LLM_API_KEY / LLM_MODEL

# 4. 一键启动(自动端口回退 + CORS 注入,前端默认生产模式)
./start.sh          # 开发热重载:./start.sh dev
```

按终端输出访问前端地址(通常为 3000/3001/3030)。游客模式开箱即用;
生产部署请设置 `AUTH_MODE=1`、更换 `AUTH_JWT_SECRET` 并配置 `CORS_ORIGINS` 白名单,
详见[部署手册](docs/The_Website_deployment_plan.md)。

可选能力(默认全部关闭,缺省即纯 BM25 + 本地 tesseract 兜底):

- `EMBEDDING_PROVIDER=openai|local`:启用 RAG 向量轨(仓库不内置任何本地向量模型,local 为模型无关接口);
- `MULTIMODAL_*`:视觉模型,用于拍照识题与扫描教材 OCR;
- `VOICE_TTS_PROVIDER=melo` + `deploy/install_voice.sh`:语音通话播报。

调试 CLI:`cd backend && python cli.py --grade 初中 --once "讲一下惯性"`。

## 测试

```bash
cd backend && python -m unittest discover -s tests          # 后端全部回归
cd frontend && pnpm exec tsc --noEmit && pnpm exec eslint src/ && pnpm exec next build --webpack
```

## 项目结构

```
Edu_Agent/
├── start.sh                # 一键启动
├── backend/app/
│   ├── api/v1/             # REST/SSE 路由(chat/quiz/assessment/notes/...)
│   ├── agents/             # M1–M10 智能层(supervisor/student_model/.../skill_runtime)
│   ├── core/               # 上下文工程/检索/会话/工具协议/原子写
│   ├── prompts/            # 版本化 Prompt 注册表
│   └── identity/           # M0 身份(JWT + bcrypt)
├── frontend/src/           # app(模块页)/ components / lib(api·store·i18n)
├── docs/                   # DESIGN.md 架构主文档等
├── scripts/                # 仓库不变量检查 + 旧学习数据迁移 CLI(§16)
└── deploy/                 # nginx/systemd 模板与部署脚本
```

## 安全与隐私

- `.env`、密钥、`users/`、`students/`、私有会话与上传均由 `.gitignore` 排除;版本化公共教材命名空间是刻意的例外。
- 密码 bcrypt 哈希,绝不回传;所有端点的学生身份只来自 JWT 解析(`resolve_student_id`),伪造无效。
- 多用户数据物理隔离,按 id 资源端点对外人 404;自助注销与管理员注销均彻底清除名下数据。
- 电话语音输入只使用浏览器平台识别 API,音频 PCM 不上传到本系统后端。

## 许可与第三方声明

- 本项目代码以 **MIT** 许可发布。
- 第三方组件声明见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md),许可全文归档于 [licenses/](licenses/);语音链路(MeloTTS 等)的逐项许可、模型权重边界与发布义务见 [docs/VOICE_LICENSES.md](docs/VOICE_LICENSES.md)。
- 本仓库**不捆绑任何模型权重**:LLM/Embedding/视觉模型均为外部服务或部署方自备;语音模型在部署时下载到 gitignored 目录。

---

<a id="english"></a>

# Next Tutor Agent · Edu_Agent (English)

> An **integrated virtual tutoring agent with long-term memory and self-improvement**.
> Not a Q&A bot — a long-term companion that closes the loop of
> *goals → understanding → practice → assessment → adaptation* for K-12 and
> undergraduate students.

Docs: architecture [docs/DESIGN.md](docs/DESIGN.md) (Chinese) · deployment [docs/The_Website_deployment_plan.md](docs/The_Website_deployment_plan.md)

## What it is

Most "AI tutors" wrap a general LLM: stateless answers, shallow recall-level
quizzes, no lasting model of the student. Edu_Agent takes a different path:

- **Long-term memory** — it remembers what you studied, where you failed, and how
  you prefer explanations, across sessions and weeks; memory is bounded,
  auditable, and forgettable.
- **Student-specific adaptation** — every real attempt lands in a unified
  learning-evidence ledger; evaluation reasons only from locatable raw
  performance and the assistance conditions at the time. A six-mode teaching
  engine switches between introduction, explanation, remediation, practice,
  review, and challenge. The same concept is told as a story to a primary
  student and as a proof to an undergraduate.
- **Self-iteration** — every teaching turn is traced, diagnosed, and aggregated;
  the system proposes teaching improvements that take effect only after human
  approval, and can be revoked at any time.
- **Integration** — a dozen intelligence modules share one student and one body of
  evidence: the student model drives the teaching engine, all assessment results
  land in one learning ledger, orchestration consumes memory and profile, and the
  knowledge graph threads through retrieval, quizzing, and path planning.

The product is a **Learning Workspace**: streaming chat at the core, plus pages
for dashboard, knowledge graph, learning plan, orchestration, assessment,
memory, resources, an Obsidian-style note vault with per-note agents, insights,
and profile.

## Feature highlights

- **Streaming chat (SSE)** with tool cards showing structured sources and page numbers.
- **Textbook library**: upload any PDF (≤256 MB, scanned books auto-OCR'd page by
  page) → structured chunking, indexing, and a background-built **per-textbook
  knowledge graph**; multi-volume groups merge into one cross-volume graph;
  admins curate a public library shared by all accounts.
- **Hybrid retrieval**: always-on BM25 (CJK-aware) + optional vector track
  (Chroma, RRF k=60); an evidence gate (query-core extraction, title-match
  calibration, FOUND/PARTIAL/NOT_FOUND tiers) plus deterministic pre-retrieval
  makes "I couldn't find it" a first-class answer — hallucination is blocked, not
  discouraged.
- **High-quality quizzing**: two-pass generation (blueprint → questions) with an
  independent solver-critic that drops wrong, mis-answered, or too-shallow items
  before delivery; variant generation transfers scenarios instead of swapping
  numbers. Interactive grading: local MC scoring, LLM three-level grading with
  reasoning; every attempt lands in an independent learning ledger; cross-session
  error notebook with one-click variant re-drills.
- **CAT adaptive testing**: per-question rubrics frozen at design time,
  deterministic scoring separated from semantic evaluation — only real,
  attributable attempts enter the learning ledger.
- **Memory lifecycle**: bounded prompt profile (5–30 session window), strategy
  success aggregation, independent learning records; deleting a chat can
  permanently forget its contribution; a 7-day trash allows restore; account
  deletion purges everything.
- **Learning orchestration**: multiple long-term goals → LLM weekly plans
  (validated, with deterministic fallback) → daily tasks; SM-2 spaced repetition;
  note-vault review cards feed the same SRS queue.
- **Voice calls (optional)**: push-to-talk phone-style tutoring via the browser's
  native Speech Recognition and a local MeloTTS sidecar, with formulas synced to
  an on-screen blackboard.
- **Accounts & ops**: JWT + bcrypt, per-user physical data isolation, guest mode
  out of the box, admin console; workspace projects; per-turn traces; 1400+
  backend unit tests.

## Architecture (M0–M10)

M0 identity · M1 supervisor · M2 student model (evidence ledger) · M3 teaching
engine · M4 assessment · M5 textbook-driven knowledge graph · M6 memory ·
M7 evaluation & improvement (human-gated) · M8 UX adaptation · M9 orchestration ·
M10 skill runtime & evidence discipline. Each layer degrades independently via
its own switch. Full contracts: [docs/DESIGN.md](docs/DESIGN.md).

## Tech stack

Next.js 16 · React · TypeScript · Tailwind CSS v4 · Zustand ｜ FastAPI ·
Python 3.11 · OpenAI SDK (async streaming) ｜ custom agent framework (Supervisor +
Skill Registry + ReAct + tracing) ｜ BM25 + optional Chroma ｜ JSON file
persistence (atomic writes, zero database) ｜ JWT + bcrypt.

## Quick start

Requires Python 3.11 (Miniconda recommended), Node.js + pnpm.

```bash
conda create -y -n edu_agent -c conda-forge --override-channels python=3.11 pip
conda activate edu_agent
pip install -r backend/requirements.txt
cd frontend && pnpm install && cd ..
cp .env.example .env        # set LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
./start.sh                  # dev hot-reload: ./start.sh dev
```

Any OpenAI-compatible endpoint works (DeepSeek recommended by default). Guest
mode works out of the box. For production: `AUTH_MODE=1`, a strong
`AUTH_JWT_SECRET`, and a `CORS_ORIGINS` whitelist — see the deployment guide.
Optional tracks (all off by default): `EMBEDDING_PROVIDER` (vector RAG),
`MULTIMODAL_*` (vision OCR), `VOICE_TTS_PROVIDER=melo` (voice calls).

## Testing

```bash
cd backend && python -m unittest discover -s tests
cd frontend && pnpm exec tsc --noEmit && pnpm exec eslint src/ && pnpm exec next build --webpack
```

## License

Project code is released under the **MIT License**. Third-party notices:
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md); full license texts in
[licenses/](licenses/); voice-stack obligations in
[docs/VOICE_LICENSES.md](docs/VOICE_LICENSES.md). The repository bundles **no
model weights** — LLM/embedding/vision models are external services or supplied
by the deployer; voice models download into gitignored directories at deploy
time.
