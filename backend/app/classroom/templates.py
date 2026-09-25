"""教学模板与视觉模板注册表（plan.md §6.4/§9.2）。

模板是版本化的结构规则、段角色分配和样例元数据，不是自由 prompt 字符串
拼接。生成管线（D 阶段）读取这里的页面组织与质量要求；本文件只登记
元数据与结构约束，prompt 文本在 prompts/classroom.py。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas.classroom import (
    ClassroomTemplates,
    CheckpointDensity,
    ImageDensity,
    LessonLanguage,
    PedagogyTemplateInfo,
    SourcePolicy,
    TemplateDefaults,
    ThemeTemplateInfo,
)


@dataclass(frozen=True)
class PedagogyTemplate:
    pedagogy_id: str
    name_zh: str
    name_en: str
    description_zh: str
    description_en: str
    version: str = "1"
    # 页面组织顺序（大纲阶段的结构骨架，§6.4 表格）
    page_flow: tuple[str, ...] = ()
    requires_web_research: bool = False
    quality_rules: tuple[str, ...] = ()


@dataclass(frozen=True)
class ThemeTemplate:
    theme_id: str
    name_zh: str
    name_en: str
    description_zh: str
    description_en: str
    version: str = "1"


PEDAGOGY_TEMPLATES: tuple[PedagogyTemplate, ...] = (
    PedagogyTemplate(
        pedagogy_id="concept_deep@1",
        name_zh="概念精讲",
        name_en="Concept Deep-Dive",
        description_zh="情境引入→定义→机制→条件→例子→误区→检查→总结，适合夯实单个核心概念。",
        description_en="Context, definition, mechanism, conditions, example, misconception, check, summary.",
        page_flow=("情境", "定义", "机制", "条件", "例子", "误区", "检查", "总结"),
        quality_rules=("核心概念必须有直觉与严谨表述两层",),
    ),
    PedagogyTemplate(
        pedagogy_id="worked_example@1",
        name_zh="例题推导",
        name_en="Worked Example",
        description_zh="问题→已知/未知→方法选择→逐步解→检验→变式，展示完整的解题思路。",
        description_en="Problem, givens/goals, method choice, step-by-step solution, check, variation.",
        page_flow=("问题", "已知未知", "方法选择", "逐步解", "检验", "变式"),
        quality_rules=("展示“为什么选这个方法”", "例题演示与独立检查题分开"),
    ),
    PedagogyTemplate(
        pedagogy_id="exam_review@1",
        name_zh="考前复习",
        name_en="Exam Review",
        description_zh="目标→知识框架→易混对比→代表题→错因→自测，强调辨析而非堆题。",
        description_en="Goals, framework, confusable pairs, representative items, error causes, self-test.",
        page_flow=("目标", "知识框架", "易混对比", "代表题", "错因", "自测"),
        quality_rules=("不虚构真题年份/出处", "时间受限时强调辨析而非堆题"),
    ),
    PedagogyTemplate(
        pedagogy_id="case_inquiry@1",
        name_zh="案例探究",
        name_en="Case Inquiry",
        description_zh="现象→预测→证据→解释→边界→迁移，先思考停顿再给解释。",
        description_en="Phenomenon, prediction, evidence, explanation, boundary, transfer.",
        page_flow=("现象", "预测", "证据", "解释", "边界", "迁移"),
        quality_rules=("先有思考停顿，后有解释", "案例事实有来源"),
    ),
    PedagogyTemplate(
        pedagogy_id="frontier_briefing@1",
        name_zh="前沿专题",
        name_en="Frontier Briefing",
        description_zh="背景→现状→关键证据→争议→应用→开放问题，必须联网检索并标注时效。",
        description_en="Background, state of the art, key evidence, controversies, applications, open questions.",
        page_flow=("背景", "现状", "关键证据", "争议", "应用", "开放问题"),
        requires_web_research=True,
        quality_rules=("必须联网成功、记录 as_of", "缺证据不能假称“最新”"),
    ),
)

THEME_TEMPLATES: tuple[ThemeTemplate, ...] = (
    ThemeTemplate(
        theme_id="academic_clear@1",
        name_zh="学术清晰",
        name_en="Academic Clear",
        description_zh="暖白背景、深蓝文字、单一蓝色强调、清楚分区，默认通用课程。",
        description_en="Warm white, deep blue text, single accent, clear sections.",
    ),
    ThemeTemplate(
        theme_id="chalk_focus@1",
        name_zh="板书聚焦",
        name_en="Chalk Focus",
        description_zh="深绿/深灰板面、暖白字、浅黄重点，真实照片保留原色，适合推导与公式。",
        description_en="Deep green/slate board, warm chalk text, pale-yellow highlights.",
    ),
    ThemeTemplate(
        theme_id="visual_story@1",
        name_zh="视觉叙事",
        name_en="Visual Story",
        description_zh="大图与短标题、单独解释区域、简洁时间线，适合人文与情境引入。",
        description_en="Large imagery, short titles, dedicated explain zones, simple timelines.",
    ),
    ThemeTemplate(
        theme_id="lab_notebook@1",
        name_zh="实验记录",
        name_en="Lab Notebook",
        description_zh="网格/图表、明确单位、步骤编号、实验观察框，适合理工与数据分析。",
        description_en="Grids and charts, explicit units, numbered steps, observation boxes.",
    ),
    ThemeTemplate(
        theme_id="gentle_beginner@1",
        name_zh="温和入门",
        name_en="Gentle Beginner",
        description_zh="柔和浅色、较大字号、较少要点、克制插图，适合初学与低学段。",
        description_en="Soft palette, larger type, fewer bullets, restrained illustration.",
    ),
)

DEFAULTS = TemplateDefaults(
    duration_minutes=15,
    language=LessonLanguage.zh,
    pedagogy_id="concept_deep@1",
    theme_id="academic_clear@1",
    image_density=ImageDensity.balanced,
    checkpoint_density=CheckpointDensity.standard,
    source_policy=SourcePolicy.textbook_plus,
)


def pedagogy_by_id(pedagogy_id: str) -> PedagogyTemplate | None:
    for t in PEDAGOGY_TEMPLATES:
        if t.pedagogy_id == pedagogy_id:
            return t
    return None


def theme_by_id(theme_id: str) -> ThemeTemplate | None:
    for t in THEME_TEMPLATES:
        if t.theme_id == theme_id:
            return t
    return None


def templates_public() -> ClassroomTemplates:
    return ClassroomTemplates(
        pedagogy=[
            PedagogyTemplateInfo(
                pedagogy_id=t.pedagogy_id, name_zh=t.name_zh,
                name_en=t.name_en, description_zh=t.description_zh,
                description_en=t.description_en, version=t.version)
            for t in PEDAGOGY_TEMPLATES
        ],
        themes=[
            ThemeTemplateInfo(
                theme_id=t.theme_id, name_zh=t.name_zh, name_en=t.name_en,
                description_zh=t.description_zh, description_en=t.description_en,
                version=t.version)
            for t in THEME_TEMPLATES
        ],
        defaults=DEFAULTS,
    )
