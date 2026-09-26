"""课堂质量门（plan.md §15.5，D02）。

确定性结构门 / 证据门 / 时长门在此实现；视觉门 = render 阶段的
Chromium 排版检查（render/check.py）；教学门 = 独立 reviewer LLM 的
issue 列表（pipeline review 阶段）。全部产出 ReviewIssue，汇总进
ReviewReport；发布最低条件：blocker 为零。
"""
from __future__ import annotations

import math
import re
from typing import Iterable

from ..schemas import classroom as sc
from . import limits

PlaceholderRe = re.compile(r"待补充|TBD|TODO|参见第[一二三四五六七八九十\d]+页|占位")
HtmlControlRe = re.compile(r"<[A-Za-z/][^>]*>|`+|\*{2}|\\(?:frac|sum|int|begin)")


def _issue(code: str, severity: str, reason: str, *,
           slide_id: str | None = None, field_path: str | None = None
           ) -> sc.ReviewIssue:
    return sc.ReviewIssue(code=code, severity=sc.Severity(severity),
                          slide_id=slide_id, field_path=field_path,
                          reason=reason[:600])


def _walk_spans(spans: Iterable) -> Iterable[str]:
    for span in spans:
        text = getattr(span, "text", None)
        if text:
            yield text


def _block_texts(block) -> list[str]:
    kind = getattr(block, "kind", "")
    out: list[str] = []
    if kind in ("paragraph", "callout"):
        out.extend(_walk_spans(block.spans))
    elif kind == "bullets":
        for item in block.items:
            out.extend(_walk_spans(item))
    elif kind == "steps":
        for step in block.steps:
            out.extend(_walk_spans(step.spans))
            out.append(step.label)
    elif kind == "formula":
        out.append(block.spoken)
        if block.label:
            out.append(block.label)
    elif kind == "image":
        out.extend([block.alt, block.caption])
    elif kind == "diagram":
        out.append(block.diagram.alt)
    return out


def structural_gate(slides: list[sc.SlideSpec],
                    templates: list[sc.CheckpointTemplate],
                    ) -> list[sc.ReviewIssue]:
    """§15.5 确定性结构门：ID 唯一/引用存在/顺序连续/数量合规/无占位/
    checkpoint 无答案泄漏/动作指向存在块/数值 finite/TTS 文本无控制符。"""
    issues: list[sc.ReviewIssue] = []
    slide_ids: set[str] = set()
    for slide in slides:
        sid = slide.slide_id
        if sid in slide_ids:
            issues.append(_issue("duplicate_id", "blocker",
                                 f"slide id 重复：{sid}"))
        slide_ids.add(sid)
        if slide.order < 1:
            issues.append(_issue("bad_order", "blocker",
                                 f"{sid} order 非法", slide_id=sid))
        block_ids = {b.id for b in slide.blocks}
        if len(block_ids) != len(slide.blocks):
            issues.append(_issue("duplicate_id", "blocker",
                                 f"{sid} 存在重复 block id", slide_id=sid))
        seg_ids: set[str] = set()
        for seg in slide.segments:
            if seg.segment_id in seg_ids:
                issues.append(_issue("duplicate_id", "blocker",
                                     f"{sid} segment id 重复",
                                     slide_id=sid))
            seg_ids.add(seg.segment_id)
            for ref in list(seg.show_block_ids) + list(seg.focus_block_ids):
                if ref not in block_ids:
                    issues.append(_issue(
                        "dangling_reference", "blocker",
                        f"{sid} 讲稿引用不存在的块 {ref}",
                        slide_id=sid, field_path="segments"))
            if HtmlControlRe.search(seg.display_text) \
                    or HtmlControlRe.search(seg.spoken_text):
                issues.append(_issue(
                    "tts_control_chars", "major",
                    f"{sid} 讲稿含 HTML/Markdown/未解析公式命令",
                    slide_id=sid, field_path="segments"))
            if PlaceholderRe.search(seg.spoken_text):
                issues.append(_issue(
                    "placeholder", "blocker",
                    f"{sid} 讲稿含未解析占位", slide_id=sid))
        for text in [slide.title, *_block_texts_multi(slide)]:
            if PlaceholderRe.search(text):
                issues.append(_issue(
                    "placeholder", "major",
                    f"{sid} 页面文本含占位", slide_id=sid))
    orders = sorted(s.order for s in slides)
    if orders and orders != list(range(1, len(orders) + 1)):
        issues.append(_issue("order_gap", "blocker", "页序不连续"))
    if len(slides) > limits.PAGE_PLAN_RANGES.get(30, (14, 24))[1] \
            and len(slides) > sc.MAX_SLIDES:
        issues.append(_issue("too_many_slides", "blocker", "页数超上限"))
    known_slides = set(slide_ids)
    template_ids: set[str] = set()
    for template in templates:
        if template.checkpoint_id in template_ids:
            issues.append(_issue("duplicate_id", "blocker",
                                 f"checkpoint id 重复 {template.checkpoint_id}"))
        template_ids.add(template.checkpoint_id)
        if template.slide_id not in known_slides:
            issues.append(_issue("dangling_reference", "blocker",
                                 f"checkpoint {template.checkpoint_id} "
                                 "引用不存在的页"))
        for slide in slides:
            if slide.slide_id != template.slide_id:
                continue
            on_page = [b for b in slide.blocks
                       if getattr(b, "checkpoint_id", None)
                       == template.checkpoint_id]
            if not on_page:
                issues.append(_issue(
                    "checkpoint_not_on_slide", "major",
                    f"checkpoint {template.checkpoint_id} 未在该页出现块"))
    issues.extend(_answer_leak_gate(slides, templates))
    return issues


def _block_texts_multi(slide: sc.SlideSpec) -> list[str]:
    out: list[str] = []
    for block in slide.blocks:
        out.extend(_block_texts(block))
    return out


def _answer_leak_gate(slides: list[sc.SlideSpec],
                      templates: list[sc.CheckpointTemplate],
                      ) -> list[sc.ReviewIssue]:
    """正式题答案不得出现在页面/讲稿/预取文本（§15.5 结构门一部分）。

    issue 带 slide_id（经 checkpoint 块反查所属页），按页修复才能定位
    到泄露页只重修该页，而不是整课重试。"""
    issues: list[sc.ReviewIssue] = []
    checkpoint_slide: dict[str, str] = {}
    for slide in slides:
        for block in slide.blocks:
            cid = getattr(block, "checkpoint_id", None)
            if cid:
                checkpoint_slide[cid] = slide.slide_id
    corpus: list[str] = []
    for slide in slides:
        corpus.append(slide.title)
        corpus.extend(_block_texts_multi(slide))
        for seg in slide.segments:
            corpus.extend([seg.display_text, seg.spoken_text])
    joined = "\n".join(corpus)
    for template in templates:
        answer = str(
            (template.verified_question_template or {}).get("answer") or "")
        if answer and len(answer) >= 2 and answer in joined:
            issues.append(_issue(
                "answer_leak", "blocker",
                f"checkpoint {template.checkpoint_id} 的答案出现在课件文本："
                f"请重写该页讲稿，移除答案内容（{answer[:60]}），把解释"
                f"留到学生作答之后",
                slide_id=checkpoint_slide.get(template.checkpoint_id)))
    return issues


def evidence_gate(revision_brief: sc.LessonBrief,
                  objectives: list[sc.Objective],
                  slides: list[sc.SlideSpec],
                  sources: list[sc.SourceRecord],
                  ) -> list[sc.ReviewIssue]:
    """§15.5 证据门：strict 模式核心目标有教材证据；引用来源都来自
    本 job evidence bank；web 事实标 source；strict 不借网络补教材。"""
    issues: list[sc.ReviewIssue] = []
    source_ids = {s.source_id for s in sources}
    textbook_ids = {s.source_id for s in sources
                    if s.kind in (sc.SourceKind.textbook,
                                  sc.SourceKind.workspace_file,
                                  sc.SourceKind.session_file)}
    strict = revision_brief.source_policy == sc.SourcePolicy.strict_textbook
    for slide in slides:
        for claim in slide.claims:
            for ref in claim.source_ids:
                if ref not in source_ids:
                    issues.append(_issue(
                        "unknown_source", "blocker",
                        f"{slide.slide_id} claim 引用了未知来源 {ref}",
                        slide_id=slide.slide_id, field_path="claims"))
            if strict and claim.kind == sc.ClaimKind.web_fact:
                issues.append(_issue(
                    "strict_web_claim", "blocker",
                    "strict_textbook 模式不得以网络事实支撑教学结论",
                    slide_id=slide.slide_id, field_path="claims"))
            if claim.kind in (sc.ClaimKind.textbook_fact,
                              sc.ClaimKind.web_fact) \
                    and not claim.source_ids:
                issues.append(_issue(
                    "no_evidence", "blocker",
                    f"{slide.slide_id} 教材/网络事实缺 claim 级来源",
                    slide_id=slide.slide_id, field_path="claims"))
    if strict:
        for obj in objectives:
            supported = any(
                claim.kind == sc.ClaimKind.textbook_fact
                and claim.source_ids
                and set(claim.source_ids) <= textbook_ids
                for slide in slides for claim in slide.claims)
            if not supported and obj.evidence_status in (
                    sc.ObjectiveEvidenceStatus.supported,
                    sc.ObjectiveEvidenceStatus.partial):
                issues.append(_issue(
                    "objective_no_textbook_evidence", "blocker",
                    f"目标 {obj.objective_id} 无教材证据支撑"))
    return issues


def duration_gate(duration_minutes: int, slides: list[sc.SlideSpec],
                  language: str = "zh",
                  ) -> tuple[list[sc.ReviewIssue], int]:
    """§15.5 时长门：中文 180–220 字/分钟、英文 120–160 词/分钟。
    返回 (issues, 预估秒数)。±25% 外先提示（major），留给 review 决策。"""
    total_chars = 0
    total_words = 0
    for slide in slides:
        for seg in slide.segments:
            text = seg.spoken_text
            cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
            latin = len(re.findall(r"[A-Za-z]+", text))
            total_chars += cjk + max(0, len(text) - cjk - latin)
            total_words += latin
    if language == "en":
        low, high = limits.SPEECH_RATE_EN_WPM
        seconds = int((total_words / ((low + high) / 2)) * 60)
    else:
        low, high = limits.SPEECH_RATE_ZH_CPM
        seconds = int((total_chars / ((low + high) / 2)) * 60)
    target = duration_minutes * 60
    tolerance = limits.DURATION_TOLERANCE
    issues: list[sc.ReviewIssue] = []
    if target > 0:
        ratio = seconds / target
        if not math.isclose(ratio, 1.0, abs_tol=tolerance):
            issues.append(_issue(
                "duration_off_target", "major",
                f"讲稿预估 {seconds // 60} 分钟，目标 {duration_minutes} "
                f"分钟（±25% 外），需压缩/拆课或向用户说明预估变化"))
    return issues, seconds


def combine_reports(*groups: list[sc.ReviewIssue]) -> sc.ReviewReport:
    issues = [i for group in groups for i in group][:64]
    blockers = sum(1 for i in issues if i.severity == sc.Severity.blocker)
    majors = sum(1 for i in issues if i.severity == sc.Severity.major)
    summary = f"确定性门：{blockers} blocker / {majors} major"
    return sc.ReviewReport(issues=issues, summary=summary[:600])


def has_blocker(issues: Iterable[sc.ReviewIssue]) -> bool:
    return any(i.severity == sc.Severity.blocker for i in issues)


# ---------------------------------------------------------------------------
# slide 载荷 span 规范化（严格校验前的无损修复）
#
# 真实 LLM 偶发把一段文字切成超过 schema 上限的 span 序列（如 paragraph
# 9 个 text span，上限 8）。这类机械违规可以确定性修复：相邻同 kind 且
# 拼接后仍满足单 span 长度上限的合并（text/emphasis 拼接、math 以空格
# 连接 latex）；仍超上限再从尾部折叠。无法安全合并时保持原样，交给
# 严格校验 + 一次修复重试（不静默丢内容）。
# ---------------------------------------------------------------------------

_SPAN_CAPS = {"paragraph": 8, "callout": 8, "steps": 6, "bullets": 8}


def _mergeable(a: dict, b: dict) -> bool:
    ka, kb = a.get("kind"), b.get("kind")
    if ka == kb == "text" or ka == kb == "emphasis":
        return len(a.get("text") or "") + len(b.get("text") or "") \
            <= sc.MAX_INLINE_TEXT
    if ka == kb == "math":
        return len(a.get("latex") or "") + len(b.get("latex") or "") + 1 \
            <= sc.MAX_LATEX \
            and len(a.get("spoken") or "") + len(b.get("spoken") or "") + 1 \
            <= sc.MAX_SPOKEN
    return False


def _merge_into(a: dict, b: dict) -> None:
    if a.get("kind") == "math":
        a["latex"] = f"{a.get('latex', '')} {b.get('latex', '')}"
        a["spoken"] = f"{a.get('spoken', '')}，{b.get('spoken', '')}"
    else:
        a["text"] = f"{a.get('text', '')}{b.get('text', '')}"


def _normalize_span_list(spans, cap: int):
    if not isinstance(spans, list) or len(spans) <= cap:
        return spans
    if not all(isinstance(sp, dict) for sp in spans):
        return spans  # 结构异常交给严格校验给出准确错误
    merged: list[dict] = []
    for span in spans:
        if merged and _mergeable(merged[-1], span):
            _merge_into(merged[-1], span)
        else:
            merged.append(dict(span))
    while len(merged) > cap:
        last = merged.pop()
        if merged and _mergeable(merged[-1], last):
            _merge_into(merged[-1], last)
        else:
            merged.append(last)
            break
    return merged


def normalize_slide_spans(payload):
    """in-place 规范化 _SlideModel 原始载荷中各 block 的 span 序列。"""
    slide = payload.get("slide") if isinstance(payload, dict) else None
    blocks = slide.get("blocks") if isinstance(slide, dict) else None
    if not isinstance(blocks, list):
        return payload
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("kind")
        if kind in ("paragraph", "callout"):
            block["spans"] = _normalize_span_list(
                block.get("spans"), _SPAN_CAPS[kind])
        elif kind == "steps":
            steps = block.get("steps")
            if isinstance(steps, list):
                for step in steps:
                    if isinstance(step, dict):
                        step["spans"] = _normalize_span_list(
                            step.get("spans"), _SPAN_CAPS["steps"])
        elif kind == "bullets":
            items = block.get("items")
            if isinstance(items, list):
                for idx, item in enumerate(items):
                    if isinstance(item, list):
                        items[idx] = _normalize_span_list(
                            item, _SPAN_CAPS["bullets"])
    return payload
