"""受控内容块渲染（plan.md §9.3）。

所有动态文本经 html.escape；公式交给固定 KaTeX 在 frame 内渲染
（trust=false）；SVG 仅由本项目图形构造器生成，不接受模型 XML。
图片在编译期以 data URI 内嵌（CSP img-src data:）。
"""
from __future__ import annotations

import html
import math
from typing import Iterable

from ...schemas.classroom import (
    BulletsBlock,
    CalloutBlock,
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


def render_spans(spans: Iterable[InlineSpan]) -> str:
    parts: list[str] = []
    for span in spans:
        if span.kind == "emphasis":
            parts.append(f'<em class="span-em">{_esc(span.text)}</em>')
        elif span.kind == "math":
            parts.append(
                f'<span class="span-math" data-katex="{_esc(span.latex)}" '
                f'role="math" aria-label="{_esc(span.spoken)}"></span>')
        else:
            parts.append(_esc(span.text))
    return "".join(parts)


# ---------------------------------------------------------------------------
# 图形构造器（§9.3 首发图形）
# ---------------------------------------------------------------------------

_DIAGRAM_W = 960
_DIAGRAM_H = 420


def _svg_wrap(inner: str, alt: str) -> str:
    return (
        f'<svg class="diagram" viewBox="0 0 {_DIAGRAM_W} {_DIAGRAM_H}" '
        f'role="img" aria-label="{_esc(alt)}" '
        f'focusable="false">{inner}</svg>')


def _layout_flow(diagram: FlowDiagram) -> str:
    """确定性分层布局：Kahn 拓扑分层；环内节点并入最后一层。"""
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
    pad, node_w, node_h = 40, 170, 54
    if diagram.direction.value == "horizontal":
        max_len = max(len(layer) for layer in layers)
        col_w = (_DIAGRAM_H - 2 * pad) / max(1, max_len)
        row_w = (_DIAGRAM_W - 2 * pad) / max(1, len(layers))
        pos: dict[str, tuple[float, float]] = {}
        for li, layer in enumerate(layers):
            span = col_w * len(layer)
            start = (_DIAGRAM_H - span) / 2 if len(layers) > 1 else pad
            for ni, node in enumerate(layer):
                cx = pad + row_w * li + row_w / 2
                cy = start + col_w * ni + min(col_w, 96) / 2 + 8
                pos[node] = (cx, cy)
    else:
        row_h = (_DIAGRAM_H - 2 * pad) / max(1, len(layers))
        pos = {}
        for li, layer in enumerate(layers):
            col_w = (_DIAGRAM_W - 2 * pad) / max(1, len(layer))
            for ni, node in enumerate(layer):
                cx = pad + col_w * ni + col_w / 2
                cy = pad + row_h * li + row_h / 2
                pos[node] = (cx, cy)

    parts: list[str] = []
    for edge in diagram.edges:
        x1, y1 = pos[edge.from_]
        x2, y2 = pos[edge.to]
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
        parts.append(
            f'<text x="{_num(cx)}" y="{_num(cy + 5)}" class="flow-node-label" '
            f'text-anchor="middle">{_esc(node.label)}</text>')
    defs = ('<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            '<path d="M 0 0 L 10 5 L 0 10 z" class="edge-arrow"/></marker></defs>')
    return _svg_wrap(defs + "".join(parts), diagram.alt)


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
        f'<text x="{_num(_DIAGRAM_W / 2)}" y="{_DIAGRAM_H - 16}" '
        f'class="axis-label" text-anchor="middle">{_esc(diagram.x_label)}</text>',
        f'<text x="18" y="{_num(_DIAGRAM_H / 2)}" class="axis-label" '
        f'text-anchor="middle" transform="rotate(-90 18 '
        f'{_num(_DIAGRAM_H / 2)})">{_esc(diagram.y_label)}</text>',
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
    colors = ["var(--cc-accent)", "#2D6A4F", "#B45309"]
    for si, series in enumerate(diagram.series):
        color = colors[si % len(colors)]
        pts = " ".join(f"{_num(px(x))},{_num(py(y))}" for x, y in series.points)
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" '
                     f'stroke-width="3" class="series"/>')
        lx, ly = series.points[-1]
        parts.append(
            f'<text x="{_num(px(lx))}" y="{_num(py(ly) - 10)}" '
            f'class="series-label" fill="{color}" '
            f'text-anchor="end">{_esc(series.label)}</text>')
    return _svg_wrap("".join(parts), diagram.alt)


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
        parts.append(
            f'<text x="{_num(cx)}" y="{_num(cy - 40)}" class="body-label" '
            f'text-anchor="middle">{_esc(body.label)}</text>')
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
        parts.append(
            f'<text x="{_num(cx + dx + 8)}" y="{_num(cy - dy - 6)}" '
            f'class="force-label">{_esc(arrow.label)}</text>')
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
    label = (f'<span class="formula-label">{_esc(block.label)}</span>'
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
        f'<figcaption>{_esc(block.caption)}</figcaption></figure>')


def render_table(block: TableBlock) -> str:
    head = "".join(f"<th>{_esc(h)}</th>" for h in block.headers)
    rows = "".join(
        "<tr>" + "".join(f"<td>{_esc(cell)}</td>" for cell in row) + "</tr>"
        for row in block.rows)
    mark = ('<span class="constructed-mark">示例数据</span>'
            if block.constructed else "")
    return (
        f'<div class="block table" data-block-id="{_esc(block.id)}">'
        f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"
        f"{mark}</div>")


def render_steps(block: StepsBlock) -> str:
    items = "".join(
        f'<li><span class="step-label">{_esc(step.label)}</span>'
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
        f'<p class="checkpoint-prompt">{_esc(prompt)}</p>'
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
