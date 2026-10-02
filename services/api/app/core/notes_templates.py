"""M-Notes 内置笔记模板：固定骨架 + 来源偏好 + 温故开关。

模板是"结构性知识"，属于代码常量而非用户数据（用户自定义模板另存于
vault 索引 custom_templates）。生成管线把骨架注入 notes_generator_system
提示词；手工新建时直接预填骨架文本。改任何骨架文本视为行为变更，需同步
检查 test_notes.py 的模板契约断言。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NoteTemplate:
    id: str
    name: str
    name_en: str
    description: str
    """默认归入的种子文件夹名（仓库初始化时播种同名文件夹）。"""
    folder_hint: str = ""
    suggested_tags: tuple[str, ...] = ()
    """学习温故模板自动开启 M9 SM-2 复习调度。"""
    review_enabled: bool = False
    """偏好的来源类型：session / textbook / workspace / error_notebook。"""
    sources: tuple[str, ...] = ()
    skeleton: str = ""


_TEMPLATES: tuple[NoteTemplate, ...] = (
    NoteTemplate(
        id="mistake_correction",
        name="错题修正",
        name_en="Mistake Correction",
        description="从对话作答与错题本提炼：错因、涉及知识点、修正要点。",
        folder_hint="错题修正",
        suggested_tags=("错题",),
        sources=("error_notebook", "session"),
        skeleton="""## 题目

<原题，保留题干与条件>

## 我的作答

<当时的思路与答案>

## 正确答案

<正确解法，关键步骤逐步列出>

## 错因分析

- 错误类型：<概念混淆 / 计算失误 / 审题偏差 / 方法缺失>
- 根因：<为什么会错，追溯到具体知识点>

## 涉及知识点

- [[相关知识点笔记]]

## 修正要点

<下次遇到同类题的检查清单>

## 同类题提示

<变式方向或易混考点>
""",
    ),
    NoteTemplate(
        id="knowledge_summary",
        name="知识点总结",
        name_en="Knowledge Summary",
        description="围绕一个知识点建立结构化总结，公式用 LaTeX、概念互相链接。",
        folder_hint="知识点总结",
        suggested_tags=("知识点",),
        sources=("textbook", "session", "workspace"),
        skeleton="""## 概念定位

<这个知识在整门课中的位置，前置知识是什么>

## 核心概念

<用一两句话讲清本质>

## 公式与定理

$$
<公式，用 LaTeX>
$$

- 适用条件：<>

## 典型例题

<1-2 个例子，含关键步骤>

## 易错点

<常见坑与辨析>

## 关联笔记

- [[相关笔记]]
""",
    ),
    NoteTemplate(
        id="review_note",
        name="学习温故",
        name_en="Review Note",
        description="间隔复习卡片式笔记：创建后自动进入 M9 SM-2 复习调度。",
        folder_hint="学习温故",
        suggested_tags=("温故",),
        review_enabled=True,
        sources=("session", "textbook", "workspace"),
        skeleton="""## 复习目标

<这次复习要巩固什么>

## 当前掌握自评

<记得 / 模糊 / 忘了，以及薄弱环节>

## 遗忘点与薄弱点

- <具体哪里记不牢>

## 关键回忆（先遮后看）

<用问题驱动回忆，答案折叠在下方>

?

<答案>

## 下次复习

<按 SM-2 调度自动安排，也可在此记录自定义重点>
""",
    ),
    NoteTemplate(
        id="chapter_notes",
        name="章节笔记",
        name_en="Chapter Notes",
        description="以教材章节为纲整理：概览、知识点清单、重点难点。",
        folder_hint="章节笔记",
        suggested_tags=("章节",),
        sources=("textbook", "workspace"),
        skeleton="""## 章节概览

<本章讲什么，与前后章的关系>

## 知识点清单

- [[知识点一]]
- [[知识点二]]

## 重点难点

- 重点：<>
- 难点：<>

## 课后问题

<遗留问题或待巩固的练习>
""",
    ),
    NoteTemplate(
        id="conversation_digest",
        name="对话总结",
        name_en="Conversation Digest",
        description="把一段辅导对话沉淀为笔记：结论、遗留问题、衍生笔记。",
        folder_hint="知识点总结",
        suggested_tags=("对话沉淀",),
        sources=("session", "workspace"),
        skeleton="""## 讨论主题

<这段对话围绕什么展开>

## 关键结论

- <要点，保留推理链>

## 遗留问题

- <尚未解决或需要后续验证的问题>

## 衍生笔记

- [[由本次对话衍生的笔记]]
""",
    ),
)

BUILT_IN_TEMPLATES: dict[str, NoteTemplate] = {t.id: t for t in _TEMPLATES}


def list_templates() -> list[dict]:
    """内置模板的 API 投影（不含用户自定义模板，由 vault 索引补充）。"""
    return [
        {
            "id": t.id,
            "name": t.name,
            "name_en": t.name_en,
            "description": t.description,
            "folder_hint": t.folder_hint,
            "suggested_tags": list(t.suggested_tags),
            "review_enabled": t.review_enabled,
            "sources": list(t.sources),
            "skeleton": t.skeleton,
            "builtin": True,
        }
        for t in _TEMPLATES
    ]


def get_template(template_id: str) -> NoteTemplate | None:
    return BUILT_IN_TEMPLATES.get((template_id or "").strip())
