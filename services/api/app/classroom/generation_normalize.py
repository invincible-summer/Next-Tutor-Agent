"""模型输出进入严格 schema 前的无损整理；只处理格式，不补写教学内容。"""
from __future__ import annotations

import re
import math
from typing import Any

from pydantic import ValidationError

from app.classroom import storage as store
from ..schemas import classroom as sc
from .validation import normalize_slide_spans


def _split_text(text: str, cap: int) -> list[str]:
    """优先按句切分，保留全部字符，长句才按上限拆开。"""
    pieces = []
    while len(text) > cap:
        ends = [m.end() for m in re.finditer(r"[。！？!?；;\n]|[.,] | ", text[:cap])]
        cut = ends[-1] if ends and ends[-1] >= cap // 2 else cap
        pieces.append(text[:cut])
        text = text[cut:]
    if text:
        pieces.append(text)
    return pieces


def _normalize_flow_labels(block: dict[str, Any]) -> None:
    """标签超长的合法流程转为编号正文；保留全文和全部有向边。"""
    if set(block) - {"kind", "id", "diagram"}:
        return
    diagram = block.get("diagram")
    if not isinstance(diagram, dict) or diagram.get("type") != "flow":
        return
    nodes, edges = diagram.get("nodes"), diagram.get("edges", [])
    if (not isinstance(nodes, list) or not isinstance(edges, list)
            or not all(isinstance(item, dict) for item in [*nodes, *edges])):
        return
    if not any(isinstance(item.get("label"), str) and len(item["label"]) > 40
               for item in [*nodes, *edges]):
        return

    # 缩短副本仅用于检查图结构；不把截断文本用于输出。悬空边、重复节点、
    # 未知字段等仍交给严格校验和模型修复，不能通过转正文掩盖。
    probe = {**diagram}
    for key, items in (("nodes", nodes), ("edges", edges)):
        probe[key] = [{**item, **({"label": item["label"][:40]}
                                 if isinstance(item.get("label"), str) else {})}
                      for item in items]
    try:
        sc.FlowDiagram.model_validate(probe)
    except ValidationError:
        return

    numbers = {node["id"]: index for index, node in enumerate(nodes, 1)}
    lines = [diagram["alt"], *[f"{index}. {node['label']}"
                              for index, node in enumerate(nodes, 1)]]
    for edge in edges:
        relation = f"{numbers[edge['from']]} → {numbers[edge['to']]}"
        if edge.get("label"):
            relation += f"：{edge['label']}"
        lines.append(relation)
    block.update(kind="paragraph", spans=[{"kind": "text", "text": text}
                 for text in _split_text("\n".join(lines), sc.MAX_INLINE_TEXT)])
    del block["diagram"]


def normalize_authored_slide(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    if "slide" not in payload and "blocks" in payload and "segments" in payload:
        payload = {"slide": payload}
    slide = payload.get("slide")
    if not isinstance(slide, dict):
        return payload
    blocks = slide.get("blocks")
    segments = slide.get("segments")
    if not isinstance(blocks, list) or not isinstance(segments, list):
        return payload

    # 页内 ID 由服务端签发，模型可以用 b1/s1 等短引用。来源、资产 ID
    # 仍必须来自授权输入，不在此生成或猜测。
    block_ids: dict[str, str] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        old = block.get("id")
        if isinstance(old, str) and not re.fullmatch(r"blk_[0-9a-f]{24}", old):
            block["id"] = block_ids.setdefault(old, store.new_id("blk"))
        kind = block.get("kind")
        diagram = block.get("diagram")
        if kind == "diagram":
            _normalize_flow_labels(block)
        if kind == "diagram" and isinstance(diagram, dict) and diagram.get("type") == "cartesian_plot":
            # A model may give valid points but forget to enlarge the viewport.
            # Expand its requested domain; never clip or alter sampled data.
            series = diagram.get("series", [])
            points = [point for item in series if isinstance(item, dict)
                      for point in item.get("points", []) if isinstance(point, (list, tuple)) and len(point) == 2] if isinstance(series, list) else []
            for axis, key in enumerate(("x_range", "y_range")):
                domain = diagram.get(key)
                values = [point[axis] for point in points]
                if (isinstance(domain, (list, tuple)) and len(domain) == 2 and values
                        and all(isinstance(v, (int, float)) and math.isfinite(v) for v in [*domain, *values])
                        and domain[0] < domain[1]):
                    diagram[key] = [min(domain[0], *values), max(domain[1], *values)]
        if kind == "paragraph":
            spans = block.get("spans", [])
            if spans and all(isinstance(span, dict) and span.get("kind") == "text"
                             and isinstance(span.get("text"), str) for span in spans):
                text = "".join(span.get("text", "") for span in spans)
                fence = re.fullmatch(r"\s*```([a-zA-Z0-9_+#.-]{0,30})\n([\s\S]+?)\n```\s*", text)
                if fence:
                    block.update(kind="code", language=fence[1] or "text", code=fence[2])
                    del block["spans"]
        items = block.get("items")
        if kind == "bullets" and isinstance(items, list) and items and all(
                isinstance(item, list) and item
                and all(isinstance(span, dict) for span in item) for item in items):
            too_long = any(isinstance(span.get("text"), str)
                           and not sc.check_mixed_text(span["text"], 40, 22)
                           for item in items for span in item)
            if len(items) > 5 or too_long:
                # 完整解释不适合短要点限制时，转为正文，保留顺序和全部文字。
                block["kind"] = "paragraph"
                block["spans"] = [span for i, item in enumerate(items)
                                  for span in [{"kind": "text", "text": "• " if i == 0 else "\n• "}, *item]]
                del block["items"]

    composition = slide.get("composition")
    if isinstance(composition, dict):
        for key in ("focal_block_id", "emphasis_block_id"):
            ref = composition.get(key)
            if isinstance(ref, str):
                composition[key] = block_ids.get(ref, ref)
        refs = composition.get("wide_block_ids")
        if isinstance(refs, list):
            composition["wide_block_ids"] = [block_ids.get(ref, ref) if isinstance(ref, str)
                                             else ref for ref in refs]

    segment_ids: dict[str, list[str]] = {}
    normalized_segments = []
    for segment in segments:
        if not isinstance(segment, dict):
            normalized_segments.append(segment)
            continue
        old = segment.get("segment_id")
        if isinstance(old, str) and not re.fullmatch(r"seg_[0-9a-f]{24}", old):
            segment["segment_id"] = store.new_id("seg")
        for key in ("show_block_ids", "focus_block_ids"):
            refs = segment.get(key)
            if isinstance(refs, list):
                segment[key] = [block_ids.get(ref, ref) if isinstance(ref, str)
                                else ref for ref in refs]
        spoken = segment.get("spoken_text")
        display = segment.get("display_text")
        parts = [segment]
        if isinstance(spoken, str) and spoken and isinstance(display, str) and display:
            speech = _split_text(spoken, sc.MAX_SPOKEN)
            captions = _split_text(display, 480)
            if len(captions) > len(speech) and len(spoken) >= len(captions):
                speech = _split_text(spoken, max(1, len(spoken) // len(captions)))
            if max(len(speech), len(captions)) > 1 and len(speech) >= len(captions):
                parts = [{**segment,
                          "segment_id": segment.get("segment_id") if i == 0 else store.new_id("seg"),
                          "spoken_text": text,
                          "display_text": captions[min(i, len(captions) - 1)],
                          "pause_after_ms": segment.get("pause_after_ms", 0) if i == len(speech) - 1 else 0,
                          "estimated_ms": 0}
                         for i, text in enumerate(speech)]
        normalized_segments.extend(parts)
        if isinstance(old, str):
            segment_ids[old] = [p.get("segment_id") for p in parts]
    slide["segments"] = normalized_segments
    for claims in (payload.get("claims", []), slide.get("claims", [])):
        if not isinstance(claims, list):
            continue
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            ref = claim.get("block_id")
            if isinstance(ref, str):
                claim["block_id"] = block_ids.get(ref, ref)
            for key, mapping in (("block_ids", block_ids), ("segment_ids", segment_ids)):
                refs = claim.get(key)
                if isinstance(refs, list) and all(isinstance(ref, str) for ref in refs):
                    claim[key] = [item for ref in refs for item in
                                  (mapping.get(ref, [ref]) if key == "segment_ids"
                                   else [mapping.get(ref, ref)])]
    return normalize_slide_spans(payload)
