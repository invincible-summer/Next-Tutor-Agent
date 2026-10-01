"""受控内容块渲染（plan.md §9.3）。

所有动态文本经 html.escape；公式交给固定 KaTeX 在 frame 内渲染
（trust=false）；SVG 仅由本项目图形构造器生成，不接受模型 XML。
图片在编译期以 data URI 内嵌（CSP img-src data:）。
"""
from __future__ import annotations

import html
import math
import re
from typing import Iterable

from .inline_math import math_parts

from ...schemas.classroom import (
    BulletsBlock,
    CalloutBlock,
    CodeBlock,
    CartesianPlot,
    CheckpointBlock,
    DiagramBlock,
    FlowDiagram,
    ForceDiagram,
    FormulaBlock,
    ImageBlock,
    InlineSpan,
    ParagraphBlock,
    SlideBlock,
    StepsBlock,
    TableBlock,
)


def _esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def _num(value: float) -> str:
    """SVG 数值：去尾零、避免科学计数法。"""
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def render_text(text: str) -> str:
    parts: list[str] = []
    for is_math, source in math_parts(text):
        if is_math:
            parts.append(f'<span class="span-math" data-katex="{_esc(source)}" '
                         f'role="math" aria-label="{_esc(source)}"></span>')
        else:
            parts.append(_esc(source))
    return "".join(parts)


def render_spans(spans: Iterable[InlineSpan]) -> str:
    parts: list[str] = []
    for span in spans:
        if span.kind == "emphasis":
            parts.append(f'<em class="span-em">{render_text(span.text)}</em>')
        elif span.kind == "math":
            parts.append(
                f'<span class="span-math" data-katex="{_esc(span.latex)}" '
                f'role="math" aria-label="{_esc(span.spoken)}"></span>')
        else:
            parts.append(render_text(span.text))
    return "".join(parts)


# ---------------------------------------------------------------------------
# 图形构造器（§9.3 首发图形）
# ---------------------------------------------------------------------------

_DIAGRAM_W = 960
_DIAGRAM_H = 420


def _svg_wrap(inner: str, alt: str, *, width: int = _DIAGRAM_W, height: int = _DIAGRAM_H) -> str:
    return (
        f'<svg class="diagram" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{_esc(alt)}" '
        f'focusable="false">{inner}</svg>')


def _svg_label(text: str, x: float, y: float, css_class: str, *,
               anchor: str = "middle", width: float = 240,
               color: str = "", rotate: bool = False) -> str:
    """SVG text for prose; HTML islands let the shared KaTeX runtime render math."""
    transform = f' transform="rotate(-90 {_num(x)} {_num(y)})"' if rotate else ""
    fill = f' fill="{color}"' if color else ""
    if not any(is_math for is_math, _ in math_parts(text)):
        return (f'<text x="{_num(x)}" y="{_num(y)}" class="{css_class}"'
                f'{fill} text-anchor="{anchor}"{transform}>{_esc(text)}</text>')
    height = 56
    left = x - (width if anchor == "end" else width / 2 if anchor == "middle" else 0)
    if not rotate:
        left = max(0, min(left, _DIAGRAM_W - width))
    top = max(0, min(y - 36, _DIAGRAM_H - height))
    align = {"middle": "center", "end": "right", "start": "left"}[anchor]
    return (f'<foreignObject x="{_num(left)}" y="{_num(top)}" '
            f'width="{_num(width)}" height="{height}"{transform}>'
            f'<div xmlns="http://www.w3.org/1999/xhtml" class="diagram-math-label {css_class}" '
            f'style="height:100%;display:flex;align-items:center;font-size:20px;line-height:1.3;text-align:{align};'
            f'color:{color or "var(--cc-text)"}"><span style="width:100%">{render_text(text)}</span></div></foreignObject>')


def _layout_flow(diagram: FlowDiagram) -> str:
    """确定性分层布局：Kahn 拓扑分层；环内节点并入最后一层。"""
    # Dense/long-label graphs use linked flow cards. Labels remain selectable,
    # wrap at readable size, and can continue by node instead of shrinking to 6px.
    if (len(diagram.nodes) > 4 or any(len(n.label) > 16 for n in diagram.nodes)
            or any(len(e.label or "") > 6 for e in diagram.edges)
            or any(is_math for text in [n.label for n in diagram.nodes]
                   + [e.label or "" for e in diagram.edges]
                   for is_math, _ in math_parts(text))):
        labels = {n.id: n.label for n in diagram.nodes}
        cards = []
        for node in diagram.nodes:
            links = [f'<li>→ {render_text(edge.label + "：" if edge.label else "")}'
                     f'{render_text(labels[edge.to])}</li>'
                     for edge in diagram.edges if edge.from_ == node.id]
            cards.append(f'<div class="flow-card"><strong>{render_text(node.label)}</strong>'
                         + (f'<ul>{"".join(links)}</ul>' if links else "") + '</div>')
        return (f'<div class="flow-map" role="group" aria-label="{_esc(diagram.alt)}">'
                + "".join(cards) + '</div>')
    ids = [n.id for n in diagram.nodes]
    incoming = {i: 0 for i in ids}
    children: dict[str, list[str]] = {i: [] for i in ids}
    for edge in diagram.edges:
        children[edge.from_].append(edge.to)
        incoming[edge.to] += 1
    layers: list[list[str]] = []
    frontier = [i for i in ids if incoming[i] == 0]
    placed: set[str] = set()
    while frontier:
        layers.append(frontier)
        placed.update(frontier)
        nxt: list[str] = []
        for node in frontier:
            for child in children[node]:
                incoming[child] -= 1
                if incoming[child] == 0 and child not in placed:
                    nxt.append(child)
        frontier = nxt
    leftover = [i for i in ids if i not in placed]
    if leftover:
        layers.append(leftover)

    label = {n.id: n.label for n in diagram.nodes}
    # Size the graph around its nodes; the old fixed 960px canvas made short
    # relationships mostly empty space and shrank their labels on half slides.
    pad, node_w, node_h = 24, 220, 124
    gap_x, gap_y = 64, 24
    max_len = max(map(len, layers))
    if diagram.direction.value == "horizontal":
        width = 2 * pad + len(layers) * node_w + (len(layers) - 1) * gap_x
        height = 2 * pad + max_len * node_h + (max_len - 1) * gap_y
        pos: dict[str, tuple[float, float]] = {}
        for li, layer in enumerate(layers):
            for ni, node in enumerate(layer):
                cx = pad + node_w / 2 + (node_w + gap_x) * li
                cy = height / 2 + (ni - (len(layer) - 1) / 2) * (node_h + gap_y)
                pos[node] = (cx, cy)
    else:
        width = 2 * pad + max_len * node_w + (max_len - 1) * gap_x
        height = 2 * pad + len(layers) * node_h + (len(layers) - 1) * gap_y
        pos = {}
        for li, layer in enumerate(layers):
            for ni, node in enumerate(layer):
                cx = width / 2 + (ni - (len(layer) - 1) / 2) * (node_w + gap_x)
                cy = pad + node_h / 2 + (node_h + gap_y) * li
                pos[node] = (cx, cy)

    parts: list[str] = []
    for edge in diagram.edges:
        x1, y1 = pos[edge.from_]
        x2, y2 = pos[edge.to]
        dx, dy = x2 - x1, y2 - y1
        if dx or dy:
            factor = min(node_w / (2 * abs(dx)) if dx else float("inf"),
                         node_h / (2 * abs(dy)) if dy else float("inf"))
            x1, y1 = x1 + dx * factor, y1 + dy * factor
            x2, y2 = x2 - dx * factor, y2 - dy * factor
        parts.append(
            f'<line x1="{_num(x1)}" y1="{_num(y1)}" x2="{_num(x2)}" '
            f'y2="{_num(y2)}" class="edge" marker-end="url(#arrow)"/>')
        if edge.label:
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2 - 8
            parts.append(
                f'<text x="{_num(mx)}" y="{_num(my)}" class="edge-label" '
                f'text-anchor="middle">{_esc(edge.label)}</text>')
    for node in diagram.nodes:
        cx, cy = pos[node.id]
        x = cx - node_w / 2
        y = cy - node_h / 2
        parts.append(
            f'<rect x="{_num(x)}" y="{_num(y)}" width="{node_w}" '
            f'height="{node_h}" rx="10" class="flow-node"/>')
        # Wrap long labels inside their node, not across neighboring nodes.
        chars_per_line = max(2, int((node_w - 18) / 32))
        lines = [node.label[i:i + chars_per_line]
                 for i in range(0, len(node.label), chars_per_line)]
        size = min(32, (node_h - 12) / max(1, len(lines)))
        tspans = "".join(
            f'<tspan x="{_num(cx)}" y="{_num(cy + (i - (len(lines) - 1) / 2) * size + size * .35)}">{_esc(line)}</tspan>'
            for i, line in enumerate(lines))
        parts.append(
            f'<text class="flow-node-label" data-flow-label="{_esc(node.label)}" style="font-size:{_num(size)}px" '
            f'text-anchor="middle">{tspans}</text>')
    defs = ('<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            '<path d="M 0 0 L 10 5 L 0 10 z" class="edge-arrow"/></marker></defs>')
    return _svg_wrap(defs + "".join(parts), diagram.alt, width=width, height=height)


def _nice_ticks(lo: float, hi: float, count: int = 5) -> list[float]:
    if not lo < hi:
        return [lo]
    raw = (hi - lo) / count
    magnitude = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for mult in (1, 2, 5, 10):
        step = magnitude * mult
        if step >= raw:
            break
    ticks = []
    value = math.ceil(lo / step) * step
    while value <= hi + step * 1e-9:
        ticks.append(round(value, 10))
        value += step
    return ticks


def _layout_cartesian_plot(diagram: CartesianPlot) -> str:
    pad = 64
    w = _DIAGRAM_W - 2 * pad
    h = _DIAGRAM_H - 2 * pad
    x0, x1 = diagram.x_range
    y0, y1 = diagram.y_range

    def px(x: float) -> float:
        return pad + (x - x0) / (x1 - x0) * w

    def py(y: float) -> float:
        return pad + h - (y - y0) / (y1 - y0) * h

    parts = [
        f'<rect x="{pad}" y="{pad}" width="{_num(w)}" height="{_num(h)}" '
        f'class="plot-frame"/>',
        _svg_label(diagram.x_label, _DIAGRAM_W / 2, _DIAGRAM_H - 16, "axis-label"),
        _svg_label(diagram.y_label, 18, _DIAGRAM_H / 2, "axis-label", rotate=True),
    ]
    for t in _nice_ticks(x0, x1):
        parts.append(
            f'<line x1="{_num(px(t))}" y1="{pad + h}" x2="{_num(px(t))}" '
            f'y2="{pad + h + 6}" class="tick"/>')
        parts.append(
            f'<text x="{_num(px(t))}" y="{pad + h + 24}" class="tick-label" '
            f'text-anchor="middle">{_num(t)}</text>')
    for t in _nice_ticks(y0, y1):
        parts.append(
            f'<line x1="{pad - 6}" y1="{_num(py(t))}" x2="{pad}" '
            f'y2="{_num(py(t))}" class="tick"/>')
        parts.append(
            f'<text x="{pad - 12}" y="{_num(py(t) + 4)}" class="tick-label" '
            f'text-anchor="end">{_num(t)}</text>')
    colors = ["var(--cc-accent)", "var(--cc-series-2,#2D6A4F)", "var(--cc-series-3,#B45309)"]
    legend = []
    for si, series in enumerate(diagram.series):
        color = colors[si % len(colors)]
        pts = " ".join(f"{_num(px(x))},{_num(py(y))}" for x, y in series.points)
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" '
                     f'stroke-width="3" class="series"/>')
        legend.append(f'<span class="plot-legend-item"><i style="background:{color}" aria-hidden="true"></i>'
                      f'<span>{render_text(series.label)}</span></span>')
    return (_svg_wrap("".join(parts), diagram.alt)
            + '<div class="plot-legend">' + ''.join(legend) + '</div>')


def _layout_force_diagram(diagram: ForceDiagram) -> str:
    pad = 48
    w = _DIAGRAM_W - 2 * pad
    h = _DIAGRAM_H - 2 * pad
    parts: list[str] = []
    # 地面参考线（受力图惯例）
    parts.append(
        f'<line x1="{pad}" y1="{_DIAGRAM_H - pad}" '
        f'x2="{_DIAGRAM_W - pad}" y2="{_DIAGRAM_H - pad}" class="ground"/>')
    for body in diagram.bodies:
        cx = pad + body.x * w
        cy = pad + body.y * h
        if body.shape == "box":
            parts.append(
                f'<rect x="{_num(cx - 44)}" y="{_num(cy - 30)}" width="88" '
                f'height="60" class="body-box"/>')
        else:
            parts.append(
                f'<circle cx="{_num(cx)}" cy="{_num(cy)}" r="14" '
                f'class="body-point"/>')
        parts.append(_svg_label(body.label, cx, cy - 40, "body-label"))
    arrow_scale = 150
    defs = ('<defs><marker id="farrow" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
            '<path d="M 0 0 L 10 5 L 0 10 z" class="edge-arrow"/></marker></defs>')
    for arrow in diagram.arrows:
        body = next(b for b in diagram.bodies if b.id == arrow.body_id)
        cx = pad + body.x * w
        cy = pad + body.y * h
        dx = arrow.dx * arrow_scale
        dy = arrow.dy * arrow_scale
        parts.append(
            f'<line x1="{_num(cx)}" y1="{_num(cy)}" x2="{_num(cx + dx)}" '
            f'y2="{_num(cy - dy)}" class="force" marker-end="url(#farrow)"/>')
        parts.append(_svg_label(arrow.label, cx + dx + 8, cy - dy - 6,
                                "force-label", anchor="start"))
    return _svg_wrap(defs + "".join(parts), diagram.alt)


def render_diagram(diagram: DiagramBlock) -> str:
    d = diagram.diagram
    if isinstance(d, FlowDiagram):
        svg = _layout_flow(d)
    elif isinstance(d, CartesianPlot):
        svg = _layout_cartesian_plot(d)
    elif isinstance(d, ForceDiagram):
        svg = _layout_force_diagram(d)
    else:  # pragma: no cover - 判别联合已闭合
        raise ValueError(f"未知图形类型: {type(d).__name__}")
    return (
        f'<div class="block diagram" data-block-id="{_esc(diagram.id)}">'
        f"{svg}</div>")


# ---------------------------------------------------------------------------
# Block 渲染
# ---------------------------------------------------------------------------

def render_paragraph(block: ParagraphBlock) -> str:
    return (f'<div class="block paragraph" data-block-id="{_esc(block.id)}">'
            f'<p>{render_spans(block.spans)}</p></div>')


def render_bullets(block: BulletsBlock) -> str:
    items = "".join(
        f"<li>{render_spans(item)}</li>" for item in block.items)
    return (f'<div class="block bullets" data-block-id="{_esc(block.id)}">'
            f"<ul>{items}</ul></div>")


def render_formula(block: FormulaBlock) -> str:
    label = (f'<span class="formula-label">{render_text(block.label)}</span>'
             if block.label else "")
    return (
        f'<div class="block formula" data-block-id="{_esc(block.id)}">'
        f'<div class="formula-box" data-katex="{_esc(block.latex)}" '
        f'role="math" aria-label="{_esc(block.spoken)}"></div>{label}</div>')


def render_image(block: ImageBlock, *, data_uri: str) -> str:
    if data_uri:
        media = (f'<img src="{data_uri}" alt="{_esc(block.alt)}" '
                 f'data-fit="{block.fit.value}">')
    else:
        # 上传图已清理/素材不可用：可见占位，不伪造图片（§16.4）
        media = (f'<div class="image-missing" role="img" '
                 f'aria-label="{_esc(block.alt)}">图片暂不可用</div>')
    return (
        f'<figure class="block image" data-block-id="{_esc(block.id)}">'
        f"{media}"
        f'<figcaption>{render_text(block.caption)}</figcaption></figure>')


def render_table(block: TableBlock) -> str:
    def cell_html(cell: str) -> str:
        numeric = ' class="numeric"' if re.fullmatch(r'[+−-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?%?', cell.strip()) else ''
        return f'<td{numeric}>{render_text(cell)}</td>'

    head = "".join(f"<th>{render_text(h)}</th>" for h in block.headers)
    rows = "".join(
        "<tr>" + "".join(cell_html(cell) for cell in row) + "</tr>"
        for row in block.rows)
    mark = ('<span class="constructed-mark">示例数据</span>'
            if block.constructed else "")
    return (
        f'<div class="block table" data-block-id="{_esc(block.id)}">'
        f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"
        f"{mark}</div>")


def render_steps(block: StepsBlock) -> str:
    items = "".join(
        f'<li><span class="step-label">{render_text(step.label)}</span>'
        f'<span class="step-body">{render_spans(step.spans)}</span></li>'
        for step in block.steps)
    return (f'<div class="block steps" data-block-id="{_esc(block.id)}">'
            f"<ol>{items}</ol></div>")


def render_checkpoint(block: CheckpointBlock, *, prompt: str,
                      kind: str) -> str:
    badge = "随堂思考" if kind == "reflect" else "随堂练习"
    return (
        f'<div class="block checkpoint" data-block-id="{_esc(block.id)}" '
        f'data-checkpoint-id="{_esc(block.checkpoint_id)}" '
        f'data-kind="{_esc(kind)}">'
        f'<div class="checkpoint-badge">{_esc(badge)}</div>'
        f'<p class="checkpoint-prompt">{render_text(prompt)}</p>'
        f'<div class="checkpoint-slot" data-checkpoint-slot="1"></div>'
        f"</div>")


def render_callout(block: CalloutBlock) -> str:
    return (
        f'<div class="block callout tone-{block.tone.value}" '
        f'data-block-id="{_esc(block.id)}">'
        f'<div class="callout-body">{render_spans(block.spans)}</div></div>')


def render_block(block: SlideBlock, *, assets_data: dict[str, str],
                 checkpoint_prompts: dict[str, tuple[str, str]]) -> str:
    """渲染一个 block；assets_data: asset_id → data URI。"""
    if isinstance(block, ParagraphBlock):
        return render_paragraph(block)
    if isinstance(block, BulletsBlock):
        return render_bullets(block)
    if isinstance(block, FormulaBlock):
        return render_formula(block)
    if isinstance(block, CodeBlock):
        caption = f'<figcaption>{render_text(block.caption)}</figcaption>' if block.caption else ""
        lines = "".join(f'<span class="code-line">{_esc(line)}</span>'
                        for line in block.code.splitlines(keepends=True))
        return (f'<figure class="block code" data-block-id="{_esc(block.id)}">'
                f'<div class="code-language">{_esc(block.language)}</div>'
                f'<pre><code>{lines}</code></pre>{caption}</figure>')
    if isinstance(block, ImageBlock):
        data_uri = assets_data.get(block.asset_id, "")
        return render_image(block, data_uri=data_uri)
    if isinstance(block, TableBlock):
        return render_table(block)
    if isinstance(block, StepsBlock):
        return render_steps(block)
    if isinstance(block, DiagramBlock):
        return render_diagram(block)
    if isinstance(block, CheckpointBlock):
        prompt, kind = checkpoint_prompts.get(block.checkpoint_id, ("", "reflect"))
        return render_checkpoint(block, prompt=prompt, kind=kind)
    if isinstance(block, CalloutBlock):
        return render_callout(block)
    raise ValueError(f"未知 block 类型: {type(block).__name__}")
