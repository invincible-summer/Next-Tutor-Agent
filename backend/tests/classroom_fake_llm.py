"""确定性 fake 课堂 LLM（plan.md D02 退出门：从 Brief 生成完整教材课程）。

按 system prompt 中的任务标记分发：大纲 / 单页写作 / 单页修复 / 整课复核 /
检索计划。输出严格匹配管线解析模型与布局 slot 约束，可全程离线跑通
九阶段。变体开关：bogus_source（写未知 src id 触发证据门）、
bad_json（输出非法 JSON 触发内容门）。
"""
from __future__ import annotations

import json
import re
from typing import Any

_USAGE = {"prompt_tokens": 500, "completion_tokens": 800, "total_tokens": 1300}

_LAYOUTS_WITH_BULLETS = {"key_points", "summary"}


class FakeClassroomLLM:
    def __init__(self, *, bogus_source: bool = False,
                 bad_json: bool = False) -> None:
        self.calls: list[str] = []
        self.bogus_source = bogus_source
        self.bad_json = bad_json

    async def complete(self, messages: list[dict], **_: Any):
        system = messages[0]["content"]
        user = messages[-1]["content"]
        if self.bad_json:
            return "这不是 JSON{", dict(_USAGE)
        payload = _split_payload(user)
        if "任务：课程大纲" in system:
            out = self._outline(payload)
        elif "任务：单页修复" in system:
            out = self._repair(payload)
        elif "任务：单页写作" in system:
            out = self._slide(payload, user)
        elif "任务：整课复核" in system:
            out = {"issues": [], "summary": "复核通过（fake）"}
        elif "任务：检索计划" in system:
            out = {"queries": [], "skipped": []}
        else:
            out = {"issues": []}
        self.calls.append(system[:12])
        return json.dumps(out, ensure_ascii=False), dict(_USAGE)

    # ------------------------------------------------------------------ 大纲

    def _outline(self, payload: dict) -> dict:
        brief = payload.get("brief") or {}
        topic = brief.get("topic", "综合主题")
        goals = brief.get("goals") or [f"理解{topic}的核心概念"]
        pages = [{
            "order": 1, "title": f"{topic}导学",
            "layout": "title", "objective_ids": [], "budget_seconds": 60,
            "key_points": [f"这节课我们要弄清楚{topic}"],
        }]
        for index, goal in enumerate(goals, start=1):
            pages.append({
                "order": index + 1, "title": goal[:40],
                "layout": "key_points",
                "objective_ids": [f"objective_{index}"],
                "budget_seconds": 150,
                "key_points": [f"{goal}的要点一", f"{goal}的要点二",
                               f"{goal}的要点三"],
            })
        while len(pages) < 6:  # 补足教学体量
            pages.append({
                "order": len(pages) + 1, "title": f"{topic}深化练习",
                "layout": "key_points",
                "objective_ids": ["objective_1"], "budget_seconds": 120,
                "key_points": [f"{topic}常见误区", f"{topic}应用情境"],
            })
        pages.append({
            "order": len(pages) + 1, "title": "停一停，检查理解",
            "layout": "checkpoint", "objective_ids": [], "budget_seconds": 60,
            "key_points": [],
        })
        pages.append({
            "order": len(pages) + 1, "title": "本课小结",
            "layout": "summary",
            "objective_ids": [f"objective_{i + 1}" for i in range(len(goals))],
            "budget_seconds": 90,
            "key_points": [f"{topic}的三条核心结论", "下一步学什么"],
        })
        return {
            "objectives": [
                {"objective_id": f"objective_{i + 1}", "text": goal,
                 "evidence_status": "supported" if i == 0 else "partial"}
                for i, goal in enumerate(goals)
            ] or [{"objective_id": "objective_1", "text": f"理解{topic}",
                   "evidence_status": "supported"}],
            "pages": pages,
            "glossary": [
                {"term": topic[:12], "definition": f"{topic}的核心定义。",
                 "spoken_hint": "先记住条件再记结论"},
            ],
            "scope_note": "fake 范围说明",
            "uncovered_note": "",
            "total_budget_seconds": sum(p["budget_seconds"] for p in pages),
        }

    # ------------------------------------------------------------------ 单页

    def _slide(self, payload: dict, raw_user: str) -> dict:
        plan = payload.get("page_plan") or {}
        layout = plan.get("layout") or payload.get("layout") or "key_points"
        title = (plan.get("title") or "页面")[:36]
        slide_id = payload.get("slide_id") or "s_" + "0" * 12
        order = payload.get("order") or 1
        # 页内确定性 24-hex ID（blk_/seg_ 模式：23 位十六进制 + 序号）
        b1 = f"blk_{order:023x}1"
        b2 = f"blk_{order:023x}2"
        b3 = f"blk_{order:023x}3"
        source_ids = re.findall(r'source_id="(src_[0-9a-f]{24})"', raw_user)
        first_source = source_ids[0] if source_ids else None
        blocks: list[dict] = []
        claims: list[dict] = []
        if layout == "title":
            blocks.append({"kind": "paragraph", "id": b1,
                           "spans": [{"kind": "text",
                                      "text": f"今天这节课，我们一起来研究{title}。"}]})
        elif layout == "checkpoint":
            blocks.append({"kind": "paragraph", "id": b1,
                           "spans": [{"kind": "text",
                                      "text": "先别翻页，回想一下刚才的推导。"}]})
        elif layout in _LAYOUTS_WITH_BULLETS:
            items = []
            for point in plan.get("key_points") or [f"{title}的要点"]:
                items.append([{"kind": "text", "text": point[:40]}])
                claims.append({
                    "block_id": b2, "claim_kind": "author_explanation",
                    "text": point[:60], "source_id": None})
            blocks.append({"kind": "bullets", "id": b2, "items": items})
            blocks.append({"kind": "paragraph", "id": b3,
                           "spans": [{"kind": "text",
                                      "text": f"把{title}的结论用自己的话讲一遍。"}]})
            if self.bogus_source:
                claims.append({
                    "block_id": b3, "claim_kind": "textbook_fact",
                    "text": f"{title}的定义来自教材。",
                    "source_id": "src_" + "f" * 24})
            elif first_source:
                claims.append({
                    "block_id": b3, "claim_kind": "textbook_fact",
                    "text": f"{title}的定义来自教材。",
                    "source_id": first_source})
        else:
            blocks.append({"kind": "paragraph", "id": b1,
                           "spans": [{"kind": "text",
                                      "text": f"{title}：核心内容讲解。"}]})
        spoken_1 = (f"我们先看这一页，{title}。这一页的目标很明确，"
                    f"请大家先建立一个整体印象，我再逐步展开讲清楚"
                    f"它背后的道理和适用条件，讲完会留时间消化。")
        spoken_2 = (f"接着我们把要点逐条过一遍，注意每一条成立的"
                    f"前提。{title}这部分最容易忽略条件，我在这里"
                    f"停一下，大家对照刚才的讲解再想一想。")
        segments = [{
            "segment_id": f"seg_{order:023x}1",
            "role": "explain", "display_text": spoken_1[:120],
            "spoken_text": spoken_1[:240],
            "show_block_ids": [b["id"] for b in blocks][:2],
            "focus_block_ids": [], "pause_after_ms": 200,
            "source_ids": [first_source] if first_source else [],
            "estimated_ms": 15000}]
        if len(blocks) > 1:
            segments.append({
                "segment_id": f"seg_{order:023x}2",
                "role": "summary", "display_text": spoken_2[:120],
                "spoken_text": spoken_2[:240],
                "show_block_ids": [b["id"] for b in blocks],
                "focus_block_ids": [blocks[-1]["id"]],
                "pause_after_ms": 0, "source_ids": [],
                "estimated_ms": 15000})
        return {
            "slide": {
                "slide_id": slide_id, "order": order, "title": title,
                "learning_objective_ids": plan.get("objective_ids", []),
                "layout": layout, "blocks": blocks, "segments": segments,
                "claims": [], "source_ids":
                    [first_source] if first_source else [],
                "transition": "auto", "estimated_seconds": 90,
            },
            "claims": claims,
        }

    # ------------------------------------------------------------------ 修复

    def _repair(self, payload: dict) -> dict:
        original = (payload.get("original") or {}).get("slide") \
            or payload.get("original") or {}
        fixed = json.loads(json.dumps(original))
        for seg in fixed.get("segments", []):
            seg["spoken_text"] = seg.get("spoken_text", "")[:160]
        blocks = fixed.get("blocks", [])
        # 只裁可选块：checkpoint 块是布局必需，绝不能丢
        while len(blocks) > 1 and blocks[-1].get("kind") == "checkpoint":
            break
        if len(blocks) > 1:
            blocks.pop()
        for seg in fixed.get("segments", []):
            seg["show_block_ids"] = [b["id"] for b in blocks]
            seg["focus_block_ids"] = []
        return {"slide": fixed, "claims": []}


def _split_payload(user: str) -> dict:
    head = user.split("\n\n", 1)[0]
    try:
        return json.loads(head)
    except json.JSONDecodeError:
        return {}
