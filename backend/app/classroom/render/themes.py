"""五套原创视觉主题与九种布局（plan.md §9.2）。

主题仅改变设计 token 和有限装饰，教学模板独立选择。所有颜色从这里
登记的 token 白名单取值；展示正文对比度 ≥4.5:1。字体用系统 CJK
无衬线栈（不引外网字体，避免首屏闪烁）。

布局是固定 slots：每个布局声明允许的 block 种类与数量上限、是否必需、
列数；编译器与确定性质量门共用同一张表，禁止新加任意 layout 字符串。
"""
from __future__ import annotations

from dataclasses import dataclass, field

CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 720
SAFE_MARGIN = 64
FOOTER_HEIGHT = 20

# 正文 ≥28px@1280（plan.md §5.2）；标题/图注/页脚相对推导
BASE_FONT_STACK = (
    '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", '
    '"Source Han Sans SC", system-ui, -apple-system, "Segoe UI", sans-serif'
)


@dataclass(frozen=True)
class ThemeTokens:
    theme_id: str
    version: str
    bg: str
    surface: str
    surface_alt: str
    text: str
    text_muted: str
    accent: str
    accent_soft: str
    border: str
    on_accent: str
    focus_ring: str
    title_scale: float = 1.0
    body_scale: float = 1.0
    radius: str = "10px"
    extra_css: str = ""


THEMES: dict[str, ThemeTokens] = {
    "academic_clear@1": ThemeTokens(
        theme_id="academic_clear@1", version="1",
        bg="#FAF7F0", surface="#FFFFFF", surface_alt="#F1EDE3",
        text="#1B2A4A", text_muted="#5A6B8C",
        accent="#2456C6", accent_soft="#E3EBFA", border="#D8DCE6",
        on_accent="#FFFFFF", focus_ring="#2456C6",
        radius="10px",
        extra_css=(
            ".slide .kicker{letter-spacing:.18em;text-transform:uppercase}"
            ".slide h1,.slide h2{border-left:6px solid var(--cc-accent);"
            "padding-left:18px}"
        ),
    ),
    "chalk_focus@1": ThemeTokens(
        theme_id="chalk_focus@1", version="1",
        bg="#21372F", surface="#2A443B", surface_alt="#243C34",
        text="#F5F1E6", text_muted="#B9C4B4",
        accent="#F2D478", accent_soft="#3A5A4E", border="#3C574C",
        on_accent="#21372F", focus_ring="#F2D478",
        radius="6px",
        extra_css=(
            # 真实照片保留原色：绝不对 img 做 filter/invert
            ".slide .stage{background-image:radial-gradient("
            "rgba(245,241,230,.05) 1px, transparent 1px);"
            "background-size:26px 26px}"
            ".slide h1,.slide h2{font-weight:600}"
            ".block.formula .formula-box{border-bottom:2px dashed "
            "rgba(242,212,120,.55)}"
        ),
    ),
    "visual_story@1": ThemeTokens(
        theme_id="visual_story@1", version="1",
        bg="#FFFFFF", surface="#F7F5F1", surface_alt="#EFEAE2",
        text="#22252A", text_muted="#6B7280",
        accent="#C2542D", accent_soft="#F7E5DC", border="#E5E0D8",
        on_accent="#FFFFFF", focus_ring="#C2542D",
        radius="14px",
        extra_css=(
            ".slide h1{font-size:calc(var(--cc-fs-title) * 1.08);"
            "font-weight:800;letter-spacing:-.01em}"
            ".block.image img{border-radius:14px}"
            ".layout-timeline .timeline-line{background:linear-gradient("
            "90deg,var(--cc-accent),#2D6A4F)}"
        ),
    ),
    "lab_notebook@1": ThemeTokens(
        theme_id="lab_notebook@1", version="1",
        bg="#FBFCF7", surface="#FFFFFF", surface_alt="#F0F4EC",
        text="#16324F", text_muted="#5B7186",
        accent="#0F766E", accent_soft="#DDF0EE", border="#CFDCD6",
        on_accent="#FFFFFF", focus_ring="#0F766E",
        radius="4px",
        extra_css=(
            ".slide .stage{background-image:linear-gradient("
            "rgba(15,118,110,.055) 1px, transparent 1px),"
            "linear-gradient(90deg, rgba(15,118,110,.055) 1px,"
            " transparent 1px);background-size:32px 32px}"
            ".block.steps .step-label{font-family:ui-monospace,SFMono-Regular,"
            "Menlo,Consolas,monospace}"
            ".block.callout{border-style:dashed}"
        ),
    ),
    "gentle_beginner@1": ThemeTokens(
        theme_id="gentle_beginner@1", version="1",
        bg="#FFF9F2", surface="#FFFFFF", surface_alt="#FDF0E6",
        text="#3D3A50", text_muted="#7A7790",
        accent="#7C6FD9", accent_soft="#ECE9FB", border="#E7E2F2",
        on_accent="#FFFFFF", focus_ring="#7C6FD9",
        title_scale=1.12, body_scale=1.12, radius="18px",
        extra_css=(
            ".slide h1,.slide h2{font-weight:700}"
            ".block.bullets li{margin:.42em 0}"
            ".block.callout{background:linear-gradient(135deg,"
            "var(--cc-accent-soft),#FBDFE8)}"
        ),
    ),
}

THEME_IDS = tuple(THEMES.keys())


@dataclass(frozen=True)
class LayoutSpec:
    layout: str
    columns: int = 1
    # block kind → (max, required)
    slots: dict[str, tuple[int, bool]] = field(default_factory=dict)
    hero_title: bool = False
    description: str = ""


LAYOUTS: dict[str, LayoutSpec] = {
    "title": LayoutSpec(
        layout="title", hero_title=True,
        slots={"paragraph": (2, False), "bullets": (1, False),
               "image": (1, False), "callout": (1, False)},
        description="封面页：大标题 + 副标题/要点 + 可选题图",
    ),
    "key_points": LayoutSpec(
        layout="key_points",
        slots={"bullets": (1, True), "paragraph": (2, False),
               "image": (1, False), "diagram": (1, False),
               "callout": (1, False), "formula": (1, False)},
        description="要点页：一组要点 + 补充说明",
    ),
    "image_explain": LayoutSpec(
        layout="image_explain", columns=2,
        slots={"image": (1, True), "paragraph": (2, False),
               "bullets": (1, False), "caption": (0, False)},
        description="图解页：题图 + 独立解释区，图文不叠压",
    ),
    "compare": LayoutSpec(
        layout="compare", columns=2,
        slots={"paragraph": (2, True), "bullets": (2, False),
               "table": (1, False), "callout": (1, False)},
        description="对比页：两栏对照（易混概念/方法 A vs B）",
    ),
    "derivation": LayoutSpec(
        layout="derivation",
        slots={"formula": (1, True), "steps": (2, False),
               "paragraph": (2, False), "callout": (1, False)},
        description="推导页：公式主线 + 步骤 + 提示框",
    ),
    "worked_example": LayoutSpec(
        layout="worked_example",
        slots={"steps": (1, True), "paragraph": (2, False),
               "formula": (2, False), "table": (1, False),
               "diagram": (1, False)},
        description="例题页：问题 + 已知/未知 + 逐步解 + 检验",
    ),
    "timeline": LayoutSpec(
        layout="timeline",
        slots={"bullets": (1, True), "paragraph": (1, False),
               "image": (1, False)},
        description="时间线页：每条要点呈现为一个事件节点",
    ),
    "checkpoint": LayoutSpec(
        layout="checkpoint",
        slots={"checkpoint": (1, True), "paragraph": (1, False)},
        description="检查点页：随堂思考/题卡占位 + 引导语",
    ),
    "summary": LayoutSpec(
        layout="summary",
        slots={"bullets": (1, True), "callout": (1, False),
               "paragraph": (1, False), "formula": (1, False)},
        description="总结页：本课回顾 + 核心结论",
    ),
}

LAYOUT_IDS = tuple(LAYOUTS.keys())


def theme_tokens(theme_id: str) -> ThemeTokens:
    tokens = THEMES.get(theme_id)
    if tokens is None:
        raise KeyError(f"未知视觉主题: {theme_id}")
    return tokens


def layout_spec(layout: str) -> LayoutSpec:
    spec = LAYOUTS.get(layout)
    if spec is None:
        raise KeyError(f"未知布局: {layout}")
    return spec
