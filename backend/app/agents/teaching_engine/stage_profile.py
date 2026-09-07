"""学段画像（stage profile）：四学段教学细则的单一事实源。

背景（P1）：此前全体系对学段的约束只有 tutor_system 里四行一句话
（"小学：用生活类比，少公式"级）+ 各 prompt 的 {grade} 干标签插值——
学段行为实际靠模型自由发挥，「两档制」（高中/本科 vs 小学/初中）之外
没有任何细则。本模块把学段差异收敛成一张纯数据表，供：

  - prompts/tutor.py 的 grade_preamble（每轮讲解主注入）
  - tools/quiz.py / fit_quiz.py / assessment 出题批改 prompt（难度锚点）
  - teaching_engine 的策略分档（policy/strategy）

七维度：语言风格 / 抽象耐受 / 例题风格 / 讲解结构 / 鼓励方式 / 难度锚点 /
典型错因。全部为纯数据 + 两个渲染函数，零 LLM、零 IO。
"""
from __future__ import annotations

# 维度说明：
#   language      语言风格（句式、用词、语气）
#   abstraction   抽象耐受（定义/公式/证明给到多深）
#   examples      例题风格（什么形态的例题最有效）
#   structure     讲解结构（一次讲解的组织方式）
#   encouragement 鼓励与互动方式
#   anchor        难度锚点（easy/medium/hard 相对什么标定）
#   mistakes      该学段典型错因（供出题埋点与批改关注点）
_PROFILES: dict[str, dict[str, str]] = {
    "小学": {
        "language": "短句、口语化、用词生活化；一次只引入一个新名词，新词当场用大白话解释",
        "abstraction": "先具体后抽象：从实物/图画/生活场景出发，公式只做总结不作起点；不要求严格推导，但每个结论都要用具体例子验证一遍让学生看到它是对的",
        "examples": "生活场景题（购物、分东西、测量、游戏计分）；数字小、整十整百优先；一步到两步运算",
        "structure": "一个知识点配一个例子立刻练；每段讲完提一个小问题让学生回答；每个环节都要落实——例子要做完而不是点到为止，问题要等学生真的有机会想；只列要点不给完整例子属于不合格",
        "encouragement": "多鼓励、多互动提问；答错时先肯定思路里对的部分再纠正",
        "anchor": "easy=课本例题原题级；medium=课内变式（换一个情境/倒过来问）；hard=课内拓展思考题（奥数入门、不超纲）",
        "mistakes": "单位漏写/换算错、题意理解偏差、进退位与计算粗心",
    },
    "初中": {
        "language": "清晰直白，可用学科术语但首次出现要配直观解释",
        "abstraction": "公式+直观解释并行：给公式同时说明它从哪来、为什么合理；推导给完整思路链（关键步骤一个不落），只省略纯计算细节",
        "examples": "教材例题级+简单应用题；数值计算不刻意复杂；强调规范书写步骤",
        "structure": "概念→公式→例题→变式四步走，每步都要做实：概念给定义加直观解释，公式给来源，例题给完整解题过程（含思路由来），变式指出与例题的本质差异；每步小结；易错点单独点名；不得跳过环节只给结论",
        "encouragement": "鼓励与要求并重；引导学生说出「下一步该干什么」",
        "anchor": "easy=课本例题直接套用；medium=一次转化或两个知识点综合（中考基础-中档）；hard=中考压轴入口/多步综合",
        "mistakes": "符号与正负号错、公式张冠李戴、单位与定义域疏忽、步骤跳步",
    },
    "高中": {
        "language": "规范学科语言，术语准确；可以直接使用教材定义表述",
        "abstraction": "严格定义+完整推导：定义的条件与边界逐条讲清（为什么需要、缺了会怎样），公式定理要给出完整推导或证明思路，允许符号化运算",
        "examples": "高考真题风格：例题+变式+多解对比；重视通性通法与易错陷阱",
        "structure": "定位（在知识网络中的位置）→定义/定理→推导（关键步骤完整，说明每步动机）→例题（含思路分析：为什么这样想、怎么联想到用这个方法）→变式→易错点；可适度综合其它章节；各环节完整覆盖，不得只给知识骨架",
        "encouragement": "平等对话式，少哄多引导；鼓励学生先给思路再补全",
        "anchor": "easy=一步直接应用（课本例题级）；medium=一次转化或综合两点（高考中档）；hard=多步推理/变式/陷阱（高考压轴入口、竞赛入门）",
        "mistakes": "定义域/取值范围遗漏、分类讨论不全、条件漏用、放缩方向错误、计算跳步",
    },
    "本科": {
        "language": "学术规范语言，可使用形式化记号；定义用标准教材表述",
        "abstraction": "定义-定理-证明结构：定义精确到每个条件并逐条解读其必要性（最好配反例说明缺条件的后果）；定理工整叙述；证明给完整思路或关键引理，重要证明写出核心步骤；可联系抽象结构与更一般的框架",
        "examples": "教材例题+定理应用+反例构造；重视「为什么需要这个条件」；可联系工程/科研应用",
        "structure": "动机（为什么需要这个概念，它解决了什么问题）→定义（条件逐条解读）→性质/定理（完整叙述）→证明思路（关键引理与核心步骤）→应用（至少一个完整算例）→与其它概念的联系（极限/线性空间/概率公理等脉络，指出等价表述、特例与推广）；每个环节都要展开成段落，禁止只列提纲或名词解释",
        "encouragement": "同侪讨论式；鼓励质疑与自行验证，指出可深入阅读的延伸方向",
        "anchor": "easy=定义/定理直接应用；medium=证明题思路补全或综合两章（期末中档）；hard=考研/竞赛级证明与构造、跨章综合",
        "mistakes": "把直觉当证明、定理条件漏验证、极限/收敛顺序随意交换、量词与任意存在混淆",
    },
}

# 未知学段回退：本科是产品默认学段（默认使用本科提示词），回退档取本科。
_DEFAULT = "本科"

VALID_STAGES = tuple(_PROFILES.keys())

# 自动学段：空串（后端事实源）或前端「自动」token 均视为自动模式。
# 自动模式下不预置学段语境，由模型按提问内容/资料自适应深度与语言。
AUTO_TOKEN = "自动"


def normalize_grade(grade: str) -> str:
    """Map the frontend 「自动」 token to the canonical '' (auto) sentinel.

    ``""`` is the backend single source of truth for "auto"; the frontend keeps
    a human-readable「自动」token in its UI store and converts it on the way out.
    This helper is the belt-and-suspenders server-side mirror so a stray「自动」
    arriving through any path (LLM tool args, PATCH body, legacy client) is also
    treated as auto instead of falling through to the unknown-grade branch.
    Never raises; returns a stripped grade string (possibly empty).
    """
    g = (grade or "").strip()
    return "" if g == AUTO_TOKEN else g


def is_auto(grade: str) -> bool:
    """True when the student did not pin a stage (auto-adapt mode).

    ``""`` (canonical) and「自动」(UI token) both mean "no explicit stage"; a
    concrete stage (小学/初中/高中/本科) — or any non-empty value — means the
    student pinned it and the full stage profile applies. Never raises.
    """
    return normalize_grade(grade) == ""


def stage_profile(grade: str) -> dict[str, str]:
    """取某学段的完整画像（未知学段回退本科，产品默认学段）。永不抛异常。

    注意：自动学段（""）调用方应先用 ``is_auto`` 分支处理，**不要**把自动
    值喂进这里（否则会被回退为高中画像，与"不预置学段语境"的语义冲突）。
    """
    return _PROFILES.get(normalize_grade(grade), _PROFILES[_DEFAULT])


def stage_brief(grade: str) -> str:
    """渲染为讲解主注入块（grade_preamble 用）：全维度细则。

    调用方保证 ``grade`` 非空（显式学段）；自动模式由 ``grade_preamble``
    走轻约束分支，不调用本函数。
    """
    p = stage_profile(grade)
    g = normalize_grade(grade) or _DEFAULT
    return (
        f"[学段教学细则·{g}]\n"
        f"- 语言风格：{p['language']}\n"
        f"- 抽象深度：{p['abstraction']}\n"
        f"- 例题风格：{p['examples']}\n"
        f"- 讲解结构：{p['structure']}\n"
        f"- 鼓励与互动：{p['encouragement']}\n"
        f"- 难度锚点：{p['anchor']}\n"
        f"- 该学段典型错因（讲解与出题时重点关照）：{p['mistakes']}"
    )


def difficulty_anchor(grade: str) -> str:
    """难度锚点单行（出题/批改 prompt 用）。"""
    return stage_profile(grade)["anchor"]


def example_style(grade: str) -> str:
    """例题风格单行（出题 prompt 用）。"""
    return stage_profile(grade)["examples"]
