# Next Tutor Agent 系统架构

> 本文档是系统级架构入口：只回答系统边界、主要 deployable、模块关系、关键数据流与依赖方向。各模块的完整架构（契约、存储、流程、不变量）见下方索引对应的模块文档。

## 系统边界与产品定位

Next Tutor Agent 是面向小学/初中/高中/本科学生的**长期陪伴式私人学习智能体**：不是问答机器人，而是构建「学习目标 → 知识理解 → 练习训练 → 能力评估 → 调整」完整学习闭环。产品形态是**学生学习空间（Learning Workspace）**：以对话为核心，辅以总览、知识图谱、学习计划、测评、记忆、资料、画像、课堂等模块页。

系统边界之外的事实：

- 仓库只分发源码、测试、部署模板与项目自制的 synthetic fixtures；不分发任何教材或其派生数据（ADR-0001）。
- 所有运行时数据统一落在 `NEXT_TUTOR_DATA_DIR` 单根之下（默认 `.runtime/data`），源码与数据平面分离（ADR-0002）。
- 检索以 BM25 为基线能力，向量检索是可选增强；无任何模型权重入库（ADR-0003）。

## 主要 Deployable

| Deployable | 路径 | 说明 |
| --- | --- | --- |
| FastAPI 后端 | `services/api/`（`app/main.py`） | 全部 REST/SSE/WebSocket API、智能层 M0+M1–M10 与领域模块；`/api/v1` 前缀 |
| Next.js 前端 | `apps/web/` | 学生学习空间 UI（App Router）；API 地址由 `NEXT_PUBLIC_BACKEND_URL` 单一决定 |
| MeloTTS 语音 sidecar | `services/voice/` | 可选；本地语音合成，后端经 HTTP 调用（详见 [voice.md](./voice.md)） |
| GitHub Pages 演示站 | `fixtures/demo/` → 导出流程 | 只读静态演示，仅由 synthetic fixtures 构建（ADR-0005）；见 [../operations/pages-demo.md](../operations/pages-demo.md) |

物理拓扑：

```
浏览器 ──> Next.js 前端 (:3000) ──REST/SSE/WS──> FastAPI 后端 (:8000, /api/v1)
                                                  │
        后端 ──> LLM（OpenAI 兼容 Chat Completions，必配）
             ──> 本地自备向量模型接口 / Embedding API（可选，RAG 向量轨）
             ──> 多模态视觉 API（可选，拍照识题；缺省回退本地 tesseract OCR）
             ──> MeloTTS sidecar（可选，语音合成）
```

SSE 为前端直连后端的流式通道（`POST /chat/stream` 等），生产同源部署时经 nginx 反代（需 `proxy_buffering off`）。

## 模块地图

### 智能层（M0 + M1–M10）

| 层 | 名称 | 一句话职责 | 代码 | 架构文档 |
|----|------|-----------|------|---------|
| M0 | 身份基础设施 | 用户是谁、数据属于谁、如何安全访问 | `app/identity/` + `api/v1/auth.py` 等 | [identity.md](./identity.md) |
| M1 | 任务智能（Supervisor） | 这一轮对话怎么完成：理解 → 规划 → 工具执行 → 状态更新 | `app/agents/`（chat_agent 及编排模块） | [conversation.md](./conversation.md) |
| M2 | 学生模型 | 这个学生会什么：画像 + 统一学习证据账本 | `app/agents/student_model/` | [student-model.md](./student-model.md) |
| M3 | 教学引擎 | 这个学生现在该怎么教：六模式状态机 + 跨轮教学记忆 | `app/agents/teaching_engine/` | [teaching-engine.md](./teaching-engine.md) |
| M4 | 测评智能 | 学生真的学会了吗：三级评分 + 约束出题 + CAT | `app/agents/assessment/` + `core/quiz_*` | [assessment.md](./assessment.md) |
| M5 | 知识智能 | 系统知道哪些知识：教材知识图谱（公用+自有）+ 概念检索 | `app/agents/knowledge/` + RAG core | [knowledge-rag.md](./knowledge-rag.md) |
| M6 | 记忆智能 | 有界 prompt 画像 + 策略聚合 | `app/agents/memory/` | [memory.md](./memory.md) |
| M7 | 评估改进智能 | 教师自己是否越来越好：TurnTrace 诊断 + 改进建议 | `app/agents/evaluation/` | [evaluation.md](./evaluation.md) |
| M8 | 交互体验智能 | 怎么表达最适合这个学生：UX 画像 + 输出适配 | `app/agents/ux_intelligence/` | [ux.md](./ux.md) |
| M9 | 学习编排智能 | 未来几周到几个月怎么学：多目标 → 周任务 → 今日任务 + SM-2 | `app/agents/learning_orchestration/` | [learning-orchestration.md](./learning-orchestration.md) |
| M10 | 技能运行时与证据门 | Agent 能调用什么、为什么调用、学习证据能否写回 | `app/agents/skill_runtime/` | [skill-runtime.md](./skill-runtime.md) |

层次关系：M5 是横向**输入**基础设施（位于智能层之下，提供知识）；M8 是横向**输出**适配层（位于智能层之上，塑形输出）；M9 是纵向编排层（横跨 M1–M4 做长期规划）；M10 是横向**能力控制层**，统一 Skill 契约与学习证据门。M0 不属于 M1–M10，是所有 Agent 的身份入口。

### 领域模块

| 模块 | 职责 | 代码 | 架构文档 |
|------|------|------|---------|
| 课堂模式 | 教师创建课程/幻灯片，学生跟堂学习与作文 | `app/classroom/` | [classroom.md](./classroom.md) |
| 站内学习助手 | 站内导航、领域动作与实体深链 | `app/agents/site_assistant/` | [site-assistant.md](./site-assistant.md) |
| 笔记 | 笔记仓库 + 笔记智能体 | `app/notes/` + `app/agents/notes_agent.py` | [notes.md](./notes.md) |
| 图库与题图 | 教学 SVG 素材库、题图装配与渲染 | `app/diagrams/` + `app/illustration/` | [diagrams-illustration.md](./diagrams-illustration.md) |
| 语音 | 电话式语音对话（WS）+ TTS | `app/voice/` + `services/voice/` | [voice.md](./voice.md) |
| 前端 | 学生学习空间 UI | `apps/web/` | [frontend.md](./frontend.md) |
| 后端运行时 | 组装、配置、存储布局、API 面、开关体系 | `app/main.py` + `app/core/`（primitive） | [backend-runtime.md](./backend-runtime.md) |
| 教学法依据 | ECDL/CLT/RBT 在系统中的落点 | 分布于 M3/M4/出题链路 | [pedagogy.md](./pedagogy.md) |

## 关键数据流

1. **对话轮**（[conversation.md](./conversation.md)）：前端 SSE → Supervisor 理解/规划 → 工具执行（M5 检索、M4 出题）→ M2/M3/M6/M8 读写钩子 → SSE 流式返回；全链路 trace 落 `traces/`。
2. **教材入库**（[knowledge-rag.md](./knowledge-rag.md)）：上传（Library 文件 + Textbook 记录）→ 后台 OCR/切片/抽取 → 知识图谱（fire-and-forget，分钟级）；教材 = Library 文件 + 注册记录 + 图谱，`file_id`/`topic_key` 双向链接。
3. **检索**（[knowledge-rag.md](./knowledge-rag.md))：BM25 基线（必在）∪ 向量轨（可选）→ evidence gate 反幻觉 → 上下文重建。
4. **学习证据**（[student-model.md](./student-model.md)、[skill-runtime.md](./skill-runtime.md)）：作答/批改/教学交互 → 统一证据 journal → 画像/错题本/评价/编排消费；证据门决定哪些能力调用可写回。
5. **长期规划**（[learning-orchestration.md](./learning-orchestration.md)）：多目标 → 周计划 → 今日任务（SM-2 复习调度）→ 会话内落地与回写。

## 依赖方向

- `api/v1/*` 路由 → 各领域模块（agents/classroom/illustration/voice/...）→ `core/`（跨领域 primitive：config/paths、atomic/json、LLM runtime、context、工具协议）。
- `identity/` 是所有请求的入口依赖（`resolve_student_id()` 唯一可信身份来源）。
- 智能层之间通过 Supervisor 读写钩子协作，钩子失败只记 trace、不影响对话流（正交降级原则）。
- 领域模块不得反向依赖 `api/`；`core/` 不得依赖任何领域模块（`core/` 准入规则见 `services/api/app/core/README.md`）。
- 前端只经 `NEXT_PUBLIC_BACKEND_URL` 访问后端，无旁路通道。

## 配置与开关体系

每个智能层一个环境变量开关，默认全开，关闭任一层则上层自动降级、下层行为不受影响（开关总表见 [backend-runtime.md](./backend-runtime.md)）。统一护栏原则：每层读写钩子包在 try/except 内，任何失败只记 trace，绝不影响对话流。

## 运维与验收入口

- 测试与 CI 分层：[../development/testing.md](../development/testing.md)
- 生产部署：[../operations/deployment.md](../operations/deployment.md)
- Pages 演示：[../operations/pages-demo.md](../operations/pages-demo.md)
- 语义 RAG 运维：[../operations/semantic-rag.md](../operations/semantic-rag.md)
- 内容与分发边界：[../compliance/content-policy.md](../compliance/content-policy.md)
