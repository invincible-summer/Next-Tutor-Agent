"""Usage docs: the admin-editable, all-user-readable /docs page content.

One global markdown document, stored at chat_history/settings/usage_docs.json
(alongside the OCR runtime policy — the established admin-settings root, no
per-owner attribution so the orphan scanner never touches it). Storage
contract mirrors ocr_policy: defensive read (missing/corrupt -> bootstrap
default), file_lock + atomic write. The document is version content, not user
runtime data, so it is safe to rewrite wholesale on save.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .atomic import atomic_write_text, file_lock

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DOCS_FILE = _PROJECT_ROOT / "chat_history" / "settings" / "usage_docs.json"

# size cap: a usage doc well beyond this is almost certainly a paste error
_MAX_MARKDOWN_CHARS = 200_000

_DEFAULT_MARKDOWN = """# 使用文档

欢迎来到 **Next Tutor Agent** —— 一套**教材驱动的 AI 一对一辅导系统**。它不只是问答机器人：每一轮对话背后都有教学策略、掌握度追踪与教材依据。系统由 M0–M10 十层智能模块协同编排：记住你学过什么，知道下一步该怎么教。

> 阅读路径：「快速上手」几分钟开课 → 按需查阅「功能模块」各节 → 想了解原理看每节的「技术实现」。

## 快速上手

1. **注册登录**：注册即得专属学习空间，所有数据按账号完全隔离；
2. **准备教材**：在「资源」页使用公共教材，或上传你自己的教材 / 讲义 / 试卷（自动解析入库）；
3. **开始提问**：在「对话工作台」打字提问，或点右上角电话按钮进入语音通话——回答基于教材检索生成、附带概念出处；
4. **追踪进步**：打开「学习总览」查看掌握度变化，让系统自动安排复习与测评。

## 功能模块

以下每节按「功能 → 技术实现 → 创新点」组织；小节只写一行定位，大节展开细讲。

### 1. 对话工作台（M1 任务智能 · M3 教学引擎）

与学习智能体多轮对话：讲解、追问、答疑、布置练习——系统的核心入口。

- **功能**
  - 苏格拉底式讲解：不直接给答案，用递进的小问题引导你自己想明白，卡住时给分级提示（先思路、再关键步骤、最后结论）；
  - 回答附带教材概念出处，可回原文核对；支持上传图片提问（OCR 由管理员策略控制）；
  - 会话内小测：对话中直接出题、判分、回写掌握度；
  - 「学习计划」页（/plan）透视当前会话的教学状态：教学模式、接下来学什么、该复习什么、教学日志。
- **技术实现**
  - 每轮对话经过「理解 → 规划 → 工具执行 → 状态更新」任务流水线，工具调用与中间状态服务端留痕；
  - M3 六模式教学状态机（讲解 / 引导 / 追问 / 纠错等）按你的基础与当前状态切换，并沉淀**跨轮教学记忆**；
  - 回答前先做 RAG 检索：从关联教材中取最相关片段，基于检索结果组织作答，从机制上抑制凭空发挥。
- **创新点**
  - 教学优先而非问答优先：策略状态机 + 跨轮记忆，让「上一轮教到哪」持续生效；
  - 全链路可溯源：每个概念都能落到教材原文。

### 2. 语音通话（P10）

像打电话一样和老师对话——公式也听得懂、看得清。

- **功能**
  - 按住说话、松手发送；回答逐句转成语音播报，可随时「停止播报」或挂断；
  - **公式朗读**：回答里的分数、根号、上下标、求和积分、单位自动转成自然中文读法——例如 $\\frac{a}{b}$ 读作「b 分之 a」，m/s 读作「米每秒」，$[-1,1]$ 读作「从负 1 到 1 的闭区间」；
  - **板书同步**：讲到哪个公式，页面中上部的黑板就实时写出排版好的公式，听不清抬眼就能对照；
  - 语音轮次与文字轮次共用同一会话历史，挂断后可继续打字。
- **技术实现**
  - 语音识别用浏览器原生 SpeechRecognition，后端零 STT 依赖，只接收最终文本；
  - 合成由本地 MeloTTS（中文、CPU）sidecar 完成：回答流式逐句合成、逐句播放，首句数秒内开口；每段音频响度归一，超长推导自动分块，不会中途中断；
  - **公式朗读规则引擎**：四种 LaTeX 定界符（`$…$`、`$$…$$`、`\\(…\\)`、`\\[…\\]`）统一归一后分级映射——SI 单位表、无花括号形式（`\\frac12`）、偏导数、绝对值、区间、正负上下标各有专门读法；未知命令保守保留，规则以真实对话语料回归测试。
- **创新点**
  - 「听 + 看」双通道讲公式：中文口语读法 × KaTeX 板书同步，语音教学对 LaTeX 真正友好；
  - 朗读规则由全量真实会话语料驱动回归，而非拍脑袋的符号映射表。

### 3. 教材与资源（RAG 知识底座）

「资源」页管理教材与学习文件，是讲解、出题、笔记的共同知识来源。

- **功能**：公共教材全员可读（管理员维护）；私有上传仅自己可见；上传自动解析、分块、向量化入库；教材支持版本管理；每个学习区可关联多本教材，对话时按需自动检索。
- **技术实现**：文档解析 + 中文分块 + 本地向量库检索；公共数据走固定 `public` 命名空间，权限上「全员可读、仅管理员可写」；检索增强贯穿对话、出题与笔记生成三条链路。
- **创新点**：教材驱动——所有智能层的知识都锚定在你选定的教材上，而不是通用语料。

### 4. 知识图谱（M5 · 知识智能）

以图谱形式浏览教材中的概念及其关联，叠加掌握度着色：哪里学会了、哪里薄弱一眼可见；点击概念可直接发起针对性提问。图谱在教材解析时自动构建，无需手工维护。

### 5. 测评中心（M4 · 测评智能）

真实检验「学会了没有」。

- **功能**：输入概念发起 CAT 自适应测评（可选学科、学段、布鲁姆认知层级焦点）；题目难度随作答动态调整，用更少的题定位真实水平；判分按完全正确 / 部分正确 / 错误三级；结果直接回写掌握度；另提供自由练习会话。
- **技术实现**：约束出题把题目严格限定在所选教材与知识点内（不出超纲题）；CAT 引擎按作答序列实时估计能力并选题；三级评分精细回写 BKT 掌握度模型。
- **创新点**：测评不是题库抽题，而是「教材约束 + 实时自适应 + 掌握度回写」的闭环。

### 6. 笔记仓库（MN）

边学边记，把知识沉淀成你自己的资料库。

- **功能**：`[[双链]]` 互相关联，未解析链接一键成篇；标签与文件夹组织；AI 一键从对话 / 教材 / 错题生成总结、修正、温故笔记；SM-2 间隔复习到期自动排期；力导向关系图总览全局；删除先入回收站。
- **技术实现**：Obsidian 式双链仓库；AI 生成以对话与教材为素材源；SM-2 算法按遗忘曲线排期，与学习编排联动。
- **创新点**：笔记不是孤立功能——它长在对话和教材上，是学习证据的一部分。

### 7. 学习总览（M2 · 学生模型）

掌握度曲线、概念状态、学习节奏与今日任务一页汇总：强项弱项、该复习什么，开门见山。

### 8. 我的画像（M2 · M8）

系统对你的理解全部**透明可查**：学段、学习风格、每个概念的掌握状态；表达方式（M8 交互体验）按你的偏好适配。画像不是黑箱——它展示什么，教学就依据什么。

### 9. 学习编排（M9 · 编排智能）

从学期目标到今日任务的自动编排。

- **功能**：设定一个或多个长期目标，系统分解为周计划 → 今日任务，并把 SM-2 到期复习织进每天；完成情况回流掌握度，下周计划自动调整。
- **技术实现**：M9 编排智能综合目标进度、掌握度与遗忘曲线生成计划，任务完成即回流为学习证据。
- **创新点**：编排出的不是静态课程表，而是随你的实际掌握情况动态变化的计划。

### 10. 记忆（M6 · 记忆智能）

系统维护**有界画像**与策略聚合：哪些讲法对你有效、什么节奏适合你。所谓「有界」：只保留经证实的稳定结论，对话原文不无限累积——越用越懂你，且画像对你完全透明。

### 11. 洞察（M7 · 评估改进）

错题本与教学自诊断：系统复盘每轮教学效果（哪些讲对了、哪些没教会），给出改进建议，让学习智能体越教越好。

### 12. 归档中心

删除的内容（如笔记）先进回收站，可随时恢复，避免误删损失。

### 13. 管理端（M0 · 仅管理员）

账号管理、运行策略、OCR 策略、回收站、数据清理（扫描并清除测试遗留的孤儿数据）；公共教材与版本在「资源」页维护；本文档也由管理员在 /docs 页内直接编辑（Markdown 实时预览）。

## 技术架构速览

| 模块 | 名称 | 职责 |
| --- | --- | --- |
| M0 | 身份基础设施 | 用户是谁、数据属于谁、如何安全访问 |
| M1 | 任务智能 | 理解 → 规划 → 工具执行 → 状态更新 |
| M2 | 学生模型 | 学习画像 + BKT 掌握度 + 概念状态 |
| M3 | 教学引擎 | 六模式状态机 + 跨轮教学记忆 |
| M4 | 测评智能 | 三级评分 + 约束出题 + CAT 自适应测试 |
| M5 | 知识智能 | 教材知识图谱 + 概念检索 |
| M6 | 记忆智能 | 有界画像 + 策略聚合，越用越懂你 |
| M7 | 评估改进 | 教学自诊断，让学习智能体越教越好 |
| M8 | 交互体验 | 表达方式适配每个学生的偏好 |
| M9 | 学习编排 | 多目标 → 周任务 → 今日任务 |
| M10 | 能力运行时 | 技能调用契约与学习证据门 |

运行时：FastAPI（后端）+ Next.js（前端）+ 本地向量库 + MeloTTS sidecar；会话、画像等用户数据按账号隔离、原子写入，账号删除即彻底清除、无残留。

## 创新点一览

- **教材驱动，全程可溯源**：讲解、出题、笔记都锚定你选的教材，概念出处可回原文核对；
- **教学引擎而非对话模板**：六模式状态机 + 跨轮教学记忆，策略随学习推进；
- **掌握度闭环**：BKT 追踪 × CAT 自适应测评 × SM-2 间隔复习 × 动态编排，四个环节互为输入输出；
- **公式也能听懂**：语音通话的 LaTeX 中文朗读 + KaTeX 板书同步双通道；
- **透明与数据主权**：有界记忆、画像全量可查、删除彻底无残留。

## 学习建议

- **让系统知道你在用哪本教材**：关联教材后，讲解、出题与测评都会以它为依据；
- **答错不要跳过**：错题会进入错题本与掌握度模型，是后续编排的输入；
- **重要内容沉淀为笔记**：对话会过去，笔记与双链留下来；
- **跟着今日任务走**：编排已综合考虑你的目标与遗忘曲线，比临时突击有效。

## 常见问题

- **回答有依据吗？** 有。回答基于所选教材的 RAG 检索结果，概念出处可溯源；
- **语音通话需要什么条件？** 需要支持 SpeechRecognition 的现代浏览器（如 Chrome / Edge）且管理员已启用 TTS；不满足时会有明确提示，文字对话完全不受影响；
- **我上传的教材别人能看到吗？** 不能。私有教材与文件仅自己可见；公共教材由管理员维护；
- **忘记复习怎么办？** 学习编排会把到期复习自动排进任务，学习总览也会提醒；
- **误删的内容能找回吗？** 能。删除先进入回收站，可在「归档中心」恢复。

> 本文档由管理员维护：管理员登录后可在本页直接编辑（支持 Markdown 实时预览）。
"""


def _bootstrap() -> dict[str, Any]:
    return {"markdown": _DEFAULT_MARKDOWN, "updated_at": 0.0, "updated_by": ""}


def read_docs() -> dict[str, Any]:
    """Read the usage doc; missing/corrupt file -> bootstrap default.

    Never raises (the page must render for everyone even with a bad file).
    """
    try:
        data = json.loads(_DOCS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _bootstrap()
        md = str(data.get("markdown") or "")
        return {
            "markdown": md if md else _bootstrap()["markdown"],
            "updated_at": float(data.get("updated_at") or 0.0),
            "updated_by": str(data.get("updated_by") or ""),
        }
    except Exception:
        return _bootstrap()


def write_docs(markdown: str, *, updated_by: str = "") -> dict[str, Any]:
    """Persist a new doc version (atomic + lock). Returns the stored payload.

    Raises ValueError when the markdown exceeds the size cap; other failures
    raise OSError so the API can surface a 500 rather than silently dropping
    an admin edit.
    """
    md = str(markdown or "")
    if len(md) > _MAX_MARKDOWN_CHARS:
        raise ValueError("document too large")
    payload = {"markdown": md, "updated_at": time.time(),
               "updated_by": str(updated_by or "")}
    _DOCS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(_DOCS_FILE):
        atomic_write_text(_DOCS_FILE, json.dumps(payload, ensure_ascii=False))
    return payload
