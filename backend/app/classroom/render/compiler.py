"""LessonSpec → 受控 HTML 编译器（plan.md §9）。

唯一生产路径：LessonSpec → validate → compile_html → render_check →
publish。显式字符串模板 + html.escape；不把 LLM 原始 HTML 放进应用 DOM；
CSP 只放行编译时固定 hash 的 runtime 与 KaTeX。

mode=online：由父页控制，无内置控件，等待 MessagePort；
mode=offline：自带前后页/目录/讲稿链接，双击 index.html 可用；
mode=print：全部页可见并分页，供浏览器“打印为 PDF”。
"""
from __future__ import annotations

import base64
from typing import Literal, Mapping

from ...schemas.classroom import (
    CheckpointTemplate,
    ImageBlock,
    LessonRevision,
    SourceRecord,
)
from . import blocks as blocks_mod
from .assets import load_asset_pack
from .themes import (
    BASE_FONT_STACK,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    THEME_IDS,
    layout_spec,
    theme_tokens,
)

CompileMode = Literal["online", "offline", "print"]

def _theme_css(theme_id: str) -> str:
    t = theme_tokens(theme_id)
    return (
        f'.slide[data-theme="{theme_id}"]{{'
        f"--cc-bg:{t.bg};--cc-surface:{t.surface};"
        f"--cc-surface-alt:{t.surface_alt};--cc-text:{t.text};"
        f"--cc-muted:{t.text_muted};--cc-accent:{t.accent};"
        f"--cc-accent-soft:{t.accent_soft};--cc-border:{t.border};"
        f"--cc-on-accent:{t.on_accent};--cc-focus-ring:{t.focus_ring};"
        f"--cc-radius:{t.radius};"
        f"--cc-fs-title:calc(44px * {t.title_scale});"
        f"--cc-fs-body:calc(28px * {t.body_scale});"
        f"--cc-fs-caption:calc(20px * {t.body_scale});"
        f"}}\n.slide[data-theme=\"{theme_id}\"] .stage{{{t.extra_css}}}"
    )


_BASE_CSS = f"""
:root{{--canvas-w:{CANVAS_WIDTH}px;--canvas-h:{CANVAS_HEIGHT}px;
--safe:{CANVAS_WIDTH // 20}px}}
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{height:100%}}
body{{font-family:{BASE_FONT_STACK};background:#2A2A2E;color:#22252A;
-webkit-font-smoothing:antialiased}}
#viewport{{position:fixed;inset:0;display:flex;align-items:center;
justify-content:center;overflow:hidden}}
.slide{{display:none;--cc-fs-title:44px;--cc-fs-body:28px;
--cc-fs-caption:20px}}
.slide.current{{display:block}}
.stage{{position:relative;width:var(--canvas-w);height:var(--canvas-h);
background:var(--cc-bg);color:var(--cc-text);border-radius:14px;
overflow:hidden;transform-origin:center center;padding:var(--safe);
display:flex;flex-direction:column}}
.stage h1{{font-size:var(--cc-fs-title);line-height:1.22;font-weight:700;
margin-bottom:16px}}
.slide[data-hero="1"] .stage h1{{font-size:calc(var(--cc-fs-title) * 1.5);
margin-top:auto;margin-bottom:auto}}
.stage h2{{font-size:calc(var(--cc-fs-title) * .72);font-weight:650;
margin-bottom:16px}}
.body-area{{flex:1;min-height:0;display:flex;flex-direction:column;
gap:14px;overflow:hidden}}
.cols-2 .body-area{{display:grid;grid-template-columns:1fr 1fr;gap:26px;
align-content:start;overflow:hidden}}
.block{{background:var(--cc-surface);border:1px solid var(--cc-border);
border-radius:var(--cc-radius);padding:15px 20px;font-size:var(--cc-fs-body);
line-height:1.62;visibility:visible;opacity:1;
transition:opacity .18s ease,transform .18s ease}}
.block.pending{{visibility:hidden;opacity:0}}
.block.focus{{outline:3px solid var(--cc-focus-ring);
outline-offset:2px;border-color:var(--cc-focus-ring)}}
.block p{{display:inline}}
.span-em{{font-style:normal;font-weight:650;color:var(--cc-accent);
text-underline-offset:4px;text-decoration:underline;
text-decoration-color:var(--cc-accent-soft)}}
.span-math,.formula-box{{font-size:1.02em}}
.block.bullets ul{{list-style:none;display:flex;flex-direction:column;gap:10px}}
.block.bullets li{{position:relative;padding-left:30px}}
.block.bullets li::before{{content:"";position:absolute;left:2px;top:.52em;
width:12px;height:12px;border-radius:4px;background:var(--cc-accent)}}
.block.formula .formula-box{{text-align:center;padding:8px 6px;
font-size:calc(var(--cc-fs-body) * 1.1)}}
.block.formula .formula-label{{display:block;text-align:right;
font-size:var(--cc-fs-caption);color:var(--cc-muted);margin-top:4px}}
.block.image{{padding:12px;display:flex;flex-direction:column;gap:8px}}
.block.image img{{width:100%;height:100%;min-height:0;object-fit:contain;
border-radius:calc(var(--cc-radius) - 4px);background:var(--cc-surface-alt)}}
.block.image img[data-fit="cover"]{{object-fit:cover}}
.image-missing{{width:100%;min-height:180px;display:flex;align-items:center;
justify-content:center;background:var(--cc-surface-alt);color:var(--cc-muted);
border:1px dashed var(--cc-border);border-radius:calc(var(--cc-radius) - 4px);
font-size:var(--cc-fs-caption)}}
.block.image figcaption{{font-size:var(--cc-fs-caption);color:var(--cc-muted);
line-height:1.5;padding:0 4px}}
.block.table table{{width:100%;border-collapse:collapse;font-size:calc(
var(--cc-fs-body) * .86)}}
.block.table th,.block.table td{{border:1px solid var(--cc-border);
padding:7px 11px;text-align:left}}
.block.table th{{background:var(--cc-accent-soft);color:var(--cc-text)}}
.constructed-mark{{display:block;margin-top:6px;font-size:15px;
color:var(--cc-muted)}}
.block.steps ol{{list-style:none;display:flex;flex-direction:column;gap:9px;
counter-reset:step}}
.block.steps li{{display:flex;gap:14px;align-items:baseline}}
.block.steps li{{counter-increment:step}}
.block.steps .step-label{{flex:none;min-width:64px;font-weight:650;
color:var(--cc-accent)}}
.block.steps .step-label::before{{content:counter(step) ". "}}
.block.callout{{border-left:6px solid var(--cc-accent);
background:var(--cc-accent-soft)}}
.block.callout.tone-warning{{border-left-color:#B45309}}
.block.callout.tone-summary{{border-left-color:#2D6A4F}}
.block.diagram{{display:flex;align-items:center;justify-content:center;
padding:10px}}
.diagram{{width:100%;height:auto;max-height:100%}}
.flow-node{{fill:var(--cc-accent-soft);stroke:var(--cc-accent);
stroke-width:2}}
.flow-node-label{{font-size:22px;fill:var(--cc-text)}}
.edge{{stroke:var(--cc-muted);stroke-width:2}}
.edge-label,.force-label{{font-size:19px;fill:var(--cc-muted)}}
.plot-frame{{fill:none;stroke:var(--cc-border);stroke-width:2}}
.tick,.ground{{stroke:var(--cc-muted);stroke-width:1.5}}
.tick-label,.axis-label{{font-size:18px;fill:var(--cc-muted)}}
.series-label,.body-label{{font-size:20px;fill:var(--cc-text)}}
.body-point{{fill:var(--cc-accent);stroke:var(--cc-bg);stroke-width:3}}
.body-box{{fill:var(--cc-surface);stroke:var(--cc-text);stroke-width:2}}
.force{{stroke:#B45309;stroke-width:3.5}}
.edge-arrow{{fill:var(--cc-muted)}}
.block.checkpoint{{border:2px dashed var(--cc-accent);
background:var(--cc-accent-soft);padding:26px}}
.checkpoint-badge{{display:inline-block;font-size:18px;font-weight:650;
color:var(--cc-on-accent);background:var(--cc-accent);border-radius:999px;
padding:4px 16px;margin-bottom:14px}}
.checkpoint-prompt{{font-size:calc(var(--cc-fs-body) * 1.04);
font-weight:550;line-height:1.6}}
.checkpoint-slot{{min-height:36px}}
.slide-footer{{flex:none;display:flex;justify-content:space-between;
align-items:center;font-size:17px;color:var(--cc-muted);
border-top:1px solid var(--cc-border);padding-top:10px;margin-top:14px}}
.source-marks{{display:flex;gap:10px;flex-wrap:wrap}}
.source-mark{{cursor:pointer;text-decoration:underline dotted;
text-underline-offset:3px}}
.kicker{{font-size:20px;color:var(--cc-muted);margin-bottom:10px}}
#controls{{position:fixed;left:0;right:0;bottom:0;display:none;
justify-content:center;gap:14px;padding:14px;background:rgba(20,20,24,.72);
backdrop-filter:blur(6px)}}
#controls button{{font-family:inherit;font-size:18px;padding:10px 22px;
border-radius:999px;border:1px solid #777;background:#fff;color:#222;
cursor:pointer}}
html[data-mode="offline"] #controls{{display:flex}}
html[data-mode="offline"] #viewport{{padding-bottom:76px}}
html[data-mode="print"] #controls{{display:none}}
#page-indicator{{color:#eee;font-size:17px;align-self:center}}
html[data-reading="1"] #viewport{{position:static;display:block;
overflow:visible}}
html[data-reading="1"] .slide{{display:none}}
html[data-reading="1"] .slide.current{{display:block}}
html[data-reading="1"] .stage{{width:100%;max-width:720px;margin:0 auto;
height:auto;transform:none !important;padding:24px 18px;border-radius:0}}
html[data-reading="1"] .cols-2 .body-area{{grid-template-columns:1fr}}
html[data-reading="1"] .stage h1{{font-size:calc(var(--cc-fs-title) * .82)}}
html[data-reading="1"] .block{{font-size:30px}}
html[data-reading="1"] .block.image img{{max-height:56vh}}
html[data-reading="1"] .block.pending{{visibility:visible;opacity:1}}
html[data-reading="1"] .block.focus{{outline:none;box-shadow:none}}
@media (prefers-reduced-motion: reduce){{.block{{transition:none}}}}
@media print{{
 body{{background:#fff}}
 #viewport{{position:static;display:block;overflow:visible}}
 #controls{{display:none !important}}
 .slide{{display:block !important;break-after:page}}
 .slide .stage{{transform:none !important;width:100%;height:auto;
 min-height:96vh}}
}}
"""

_PRINT_CSS = """
@media print{
 .slide{break-inside:avoid}
 .slide:last-child{break-after:auto}
}
"""


def _data_uri(mime: str, data: bytes) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def _source_marks(slide_sources: list[SourceRecord]) -> str:
    parts = []
    for index, source in enumerate(slide_sources, start=1):
        label = source.title[:18] + ("…" if len(source.title) > 18 else "")
        parts.append(
            f'<span class="source-mark" data-source-id="{source.source_id}" '
            f'role="button" tabindex="0" title="{blocks_mod._esc(label)}">'
            f"[{index}]</span>")
    return f'<div class="source-marks">{"".join(parts)}</div>'


def _slide_html(revision: LessonRevision, slide, *,
                checkpoint_prompts: dict[str, tuple[str, str]],
                assets_data: dict[str, str]) -> str:
    spec = layout_spec(slide.layout.value)
    source_by_id = {s.source_id: s for s in revision.source_snapshot}
    slide_sources = [source_by_id[sid] for sid in slide.source_ids
                     if sid in source_by_id]
    blocks_html = "".join(
        blocks_mod.render_block(
            block, assets_data=assets_data,
            checkpoint_prompts=checkpoint_prompts)
        for block in slide.blocks)
    total = len(revision.slides)
    footer_left = (f"<span>{blocks_mod._esc(revision.brief.topic[:40])}"
                   f"</span>")
    footer_right = (
        f'<span class="page-no">{slide.order} / {total}</span>')
    return (
        f'<section class="slide layout-{slide.layout.value}'
        f'{" cols-2" if spec.columns == 2 else ""}" '
        f'data-slide-id="{slide.slide_id}" data-order="{slide.order}" '
        f'data-layout="{slide.layout.value}" '
        f'data-hero="{1 if spec.hero_title else 0}" '
        f'data-theme="{revision.brief.theme_id}">'
        f'<div class="stage">'
        f"<h1>{blocks_mod._esc(slide.title)}</h1>"
        f'<div class="body-area">{blocks_html}</div>'
        f'<div class="slide-footer">{footer_left}'
        f"{_source_marks(slide_sources)}{footer_right}</div>"
        f"</div></section>")


def validate_layout_slots(revision: LessonRevision) -> None:
    """布局 slot 校验（§9.2 固定 slots 与数量上限；编译与质量门共用）。"""
    for slide in revision.slides:
        spec = layout_spec(slide.layout.value)
        counts: dict[str, int] = {}
        for block in slide.blocks:
            counts[block.kind] = counts.get(block.kind, 0) + 1
        for kind, (max_count, required) in spec.slots.items():
            actual = counts.get(kind, 0)
            if actual > max_count:
                raise ValueError(
                    f"布局 {slide.layout.value} 的 {kind} 数量超限"
                    f"（{actual}>{max_count}），slide_id={slide.slide_id}")
            if required and actual == 0:
                raise ValueError(
                    f"布局 {slide.layout.value} 缺少必需的 {kind}，"
                    f"slide_id={slide.slide_id}")
        unknown = set(counts) - set(spec.slots)
        if unknown:
            raise ValueError(
                f"布局 {slide.layout.value} 不允许 block：{sorted(unknown)}，"
                f"slide_id={slide.slide_id}")
        checkpoint_ids = {b.checkpoint_id for b in slide.blocks
                          if b.kind == "checkpoint"}
        known = {c.checkpoint_id for c in revision.checkpoint_templates}
        dangling = checkpoint_ids - known
        if dangling:
            raise ValueError(f"checkpoint 引用不存在模板：{sorted(dangling)}")


def compile_html(revision: LessonRevision, *, mode: CompileMode = "online",
                 asset_bytes: Mapping[str, bytes] | None = None) -> str:
    """编译整课 HTML；asset_bytes: asset_id → 原始图片 bytes。"""
    if revision.brief.theme_id not in THEME_IDS:
        raise ValueError(f"未知视觉主题: {revision.brief.theme_id}")
    validate_layout_slots(revision)
    pack = load_asset_pack()
    if pack is None:
        raise RuntimeError("renderer_unavailable")

    checkpoint_prompts: dict[str, tuple[str, str]] = {
        c.checkpoint_id: (c.prompt, c.kind.value)
        for c in revision.checkpoint_templates
    }
    assets_data: dict[str, str] = {}
    for asset in revision.assets:
        data = (asset_bytes or {}).get(asset.asset_id)
        if data is not None and asset.status.value == "ready":
            assets_data[asset.asset_id] = _data_uri(asset.mime, data)
        else:
            # 缺 bytes 或已 unavailable：可见占位（不导出/嵌入旧 bytes）
            assets_data[asset.asset_id] = ""

    slides_html = "".join(
        _slide_html(revision, slide, checkpoint_prompts=checkpoint_prompts,
                    assets_data=assets_data)
        for slide in revision.slides)

    def _csp_hash(hex_sha: str) -> str:
        """CSP hash 源使用 base64（不是 hex）。"""
        return base64.b64encode(bytes.fromhex(hex_sha)).decode("ascii")

    theme_css = "".join(_theme_css(theme_id) for theme_id in THEME_IDS)
    print_css = _PRINT_CSS if mode == "print" else ""
    title = blocks_mod._esc(revision.brief.topic)
    lang = revision.brief.language.value
    csp = (
        "default-src 'none'; "
        f"script-src 'sha256-{_csp_hash(pack.runtime_sha256)}' "
        f"'sha256-{_csp_hash(pack.katex_sha256)}'; "
        "style-src 'unsafe-inline'; img-src data:; font-src data:; "
        "connect-src 'none'; object-src 'none'; base-uri 'none'; "
        "form-action 'none'")

    return f"""<!DOCTYPE html>
<html lang="{lang}" data-mode="{mode}">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{csp}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{pack.katex_css}</style>
<style>{_BASE_CSS}{theme_css}{print_css}</style>
<script>{pack.katex_js}</script>
<script>{pack.runtime_js}</script>
</head>
<body>
<div id="viewport" aria-live="polite">
{slides_html}
</div>
<div id="controls">
<button type="button" data-action="prev">‹ 上一页</button>
<span id="page-indicator"></span>
<button type="button" data-action="next">下一页 ›</button>
</div>
</body>
</html>"""


def compile_speaker_notes(revision: LessonRevision) -> str:
    """逐页讲稿 Markdown（导出 ZIP 的 speaker-notes.md）。"""
    lines = [f"# {revision.brief.topic} · 讲稿", ""]
    source_by_id = {s.source_id: s for s in revision.source_snapshot}
    for slide in revision.slides:
        lines.append(f"## {slide.order}. {slide.title}")
        lines.append("")
        for segment in slide.segments:
            lines.append(segment.spoken_text)
            lines.append("")
        if slide.source_ids:
            marks = []
            for index, sid in enumerate(slide.source_ids, start=1):
                source = source_by_id.get(sid)
                if source is None:
                    continue
                locator = source.locator
                if source.kind.value == "web":
                    where = source.title
                else:
                    where = " / ".join(locator.section_path or [source.title])
                lines_note = f"[{index}] {where}"
                marks.append(lines_note)
            lines.append(f"> 来源：{'；'.join(marks)}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def compile_credits(revision: LessonRevision) -> str:
    """资料与图片署名页（credits.html，导出用）。"""
    parts = ["<h1>资料与图片来源</h1>"]
    parts.append("<h2>引用资料</h2><ul>")
    for source in revision.source_snapshot:
        locator = source.locator
        if source.kind.value == "web":
            where = (f'<a href="{blocks_mod._esc(locator.url)}" '
                     f'rel="noopener noreferrer">'
                     f"{blocks_mod._esc(source.title)}</a>"
                     f"（{blocks_mod._esc(locator.domain)}）")
        else:
            where = f"{blocks_mod._esc(source.title)}"
            if locator.section_path:
                where += " · " + blocks_mod._esc(
                    " / ".join(locator.section_path))
        parts.append(f"<li>{where}</li>")
    parts.append("</ul>")
    image_assets = [a for a in revision.assets
                    if a.provenance.provider.value != "upload"]
    if image_assets:
        parts.append("<h2>图片</h2><ul>")
        for asset in image_assets:
            p = asset.provenance
            parts.append(
                f"<li>{blocks_mod._esc(asset.alt)} — "
                f'<a href="{blocks_mod._esc(p.source_url)}" '
                f'rel="noopener noreferrer">'
                f"{blocks_mod._esc(p.creator or p.provider.value)}</a> · "
                f'<a href="{blocks_mod._esc(p.license_url)}" '
                f'rel="noopener noreferrer">许可</a></li>')
        parts.append("</ul>")
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>credits</title></head><body style='font-family:sans-serif;"
            "max-width:720px;margin:40px auto;line-height:1.7'>"
            + "".join(parts) + "</body></html>")
