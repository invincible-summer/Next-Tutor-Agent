#!/usr/bin/env python
"""Offline seed-pack builder (M5.6) — LLM drafts, validator gates, human merges.

This is a BUILD-TIME tool, never imported by the runtime. It turns a curriculum
outline (考纲要点 / 教材目录, plain text) plus an LLM into a draft seed pack for
one (学段, 学科). The draft is validated by the deterministic
seed_packs/validate.py gate; only a passing draft is written to disk, and it
only reaches the runtime after a human reviews it and registers the module in
seed_packs/__init__.py (the review gate is deliberate: the runtime knowledge
graph must stay stable and reproducible — no LLM generation at serve time).

Usage (from backend/):
    python scripts/build_seed_pack.py --stage 初中 --subject 数学 \
        --outline seed_packs/curriculum/junior_math_outline.txt
    python scripts/build_seed_pack.py --stage 小学 --subject 英语 --dry-run
    python scripts/build_seed_pack.py --check seed_pack_drafts/pack_junior_math.py

Exit codes: 0 ok / 1 LLM or parse failure / 2 validation failed (no file written).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.agents.knowledge.seed_packs import STAGE_SUBJECTS, SUBJECT_TOKENS  # noqa: E402
from app.agents.knowledge.seed_packs.validate import validate_pack  # noqa: E402

STAGE_EN = {"小学": "primary", "初中": "junior", "高中": "senior", "本科": "undergrad"}
DRAFTS_DIR = _BACKEND / "seed_pack_drafts"

_PROMPT = """你是中国{stage}{subject}课程专家。请根据{source_desc}，为「{stage}{subject}」构建一份完整的知识点谱系，输出且仅输出一个 JSON 对象（不要输出任何其他文字）。

JSON 结构：
{{
  "chapters": [{{"id": "ch.{token}.<章节slug>", "name": "章节名"}}],
  "nodes": [{{"id": "{token}.<领域>.<知识点>", "name": "知识点名", "subject": "{subject}",
              "level": "{stage}", "difficulty": 1-5, "description": "一句话说明",
              "aliases": ["常见别称"], "common_errors": ["典型易错点"]}}],
  "edges": [{{"source": "...", "target": "...", "type": "..."}}],
  "contents": [{{"concept_id": "...", "definition": "...", "formula": "...",
                 "example": "...", "exercise_hint": "..."}}]
}}

硬性规则（违反将被机器校验拒绝）：
1. 节点 40-{max_nodes} 个，覆盖该学段该学科考纲的全部核心知识点；id 全小写英文蛇形，必须以 "{token}." 开头。
2. 章节 5-12 个，id 形如 "ch.{token}.<slug>"；每个节点用一条 {{"source": "<节点id>", "target": "<章节id>", "type": "part_of"}} 边归入章节。
3. type 只能是 prerequisite / related / part_of / application / misconception。
4. prerequisite 表示「学 target 前必须先学 source」，只能在概念之间，且全图不得成环。
5. 每个节点 level 必须填 "{stage}"，subject 必须填 "{subject}"；difficulty 为 1(最基础) 到 5(最难)。
6. 别名不得与其他节点的名字或别名重复。
7. contents 只给 5-10 个最高杠杆的核心概念。
"""


def _build_prompt(stage: str, subject: str, outline: str, max_nodes: int,
                  keep_nodes: list[dict] | None = None,
                  keep_prereqs: set[tuple[str, str]] | None = None) -> str:
    token = SUBJECT_TOKENS[subject]
    if outline.strip():
        source_desc = f"以下考纲/教材目录：\n\n{outline.strip()}\n"
    else:
        source_desc = "你对中国课程标准与主流教材目录的了解"
    prompt = _PROMPT.format(stage=stage, subject=subject, token=token,
                            max_nodes=max_nodes, source_desc=source_desc)
    if keep_nodes:
        lines = ["", "另外，以下节点已存在且被学生掌握度数据引用，必须满足：",
                 "8. 以下节点在 nodes 中原样包含（id/name/difficulty 一字不改，"
                 "description/aliases/common_errors 可补充）："]
        for n in keep_nodes:
            lines.append(f"   - {n['id']} | {n['name']} | difficulty {n['difficulty']}")
        if keep_prereqs:
            lines.append("9. 以下 prerequisite 边必须原样出现在 edges 中：")
            for s, t in sorted(keep_prereqs):
                lines.append(f"   - {s} -> {t}")
        prompt += "\n".join(lines) + "\n"
    return prompt


def _extract_json(raw: str) -> dict:
    """Tolerant JSON extraction for LLM output.

    Repo-standard `\{.*\}` extraction first; on failure, repair a TRUNCATED
    document (max_tokens cut) by cutting back to the last fully-closed
    array element and closing the remaining open brackets. strict=False
    tolerates raw control characters (e.g. newlines) inside strings.
    """
    m = re.search(r"\{.*\}", raw, re.DOTALL) or re.search(r"\{.*", raw, re.DOTALL)
    if not m:
        raise ValueError("LLM 输出中未找到 JSON 对象")
    text = m.group(0)
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        pass
    repaired = _repair_truncated(text)
    if repaired is None:
        raise ValueError("JSON 解析失败且无法修复（非截断型错误）")
    return json.loads(repaired, strict=False)


def _repair_truncated(text: str) -> str | None:
    """Cut at the last complete array element boundary, then close brackets."""
    stack: list[str] = []
    in_str = False
    esc = False
    last_good = -1  # index just past the last point where an element closed
    pairs = {"}": "{", "]": "["}
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack or stack[-1] != pairs[ch]:
                return None
            stack.pop()
            # a complete element of a NON-root array/object just closed
            if len(stack) >= 2:
                last_good = i + 1
    if last_good <= 0 or not stack:
        return None
    closers = {"{": "}", "[": "]"}
    return text[:last_good] + "".join(closers[b] for b in reversed(stack))


def _normalize_spec(spec: dict, keep_nodes: list[dict],
                    keep_prereqs: set[tuple[str, str]]) -> dict:
    """Deterministic repairs applied BEFORE validation (never relax the gate):
      - hyphen -> underscore in every id reference (LLMs love kebab-case);
      - keep nodes forced verbatim (id/name/difficulty authoritative from the
        legacy pack; other fields from the draft if present, else legacy);
      - keep PREREQUISITE edges appended when the LLM omitted them.
    """
    def fix(v: Any) -> Any:
        return v.replace("-", "_") if isinstance(v, str) else v

    for key in ("nodes", "chapters"):
        for item in spec.get(key) or []:
            item["id"] = fix(item.get("id", ""))
    for e in spec.get("edges") or []:
        e["source"], e["target"] = fix(e.get("source", "")), fix(e.get("target", ""))
    for c in spec.get("contents") or []:
        c["concept_id"] = fix(c.get("concept_id", ""))

    nodes = spec.setdefault("nodes", [])
    by_id = {n.get("id"): n for n in nodes}
    for kn in keep_nodes:
        cur = by_id.get(kn["id"])
        if cur is None:
            nodes.append(dict(kn))
        else:
            cur["name"] = kn["name"]
            cur["difficulty"] = kn["difficulty"]
    edges = spec.setdefault("edges", [])
    have = {(e.get("source"), e.get("target"), e.get("type")) for e in edges}
    for s, t in sorted(keep_prereqs):
        if (s, t, "prerequisite") not in have:
            edges.append({"source": s, "target": t, "type": "prerequisite"})
    return spec
    return f"pack_{STAGE_EN[stage]}_{SUBJECT_TOKENS[subject]}"


def _write_pack(spec: dict, *, stage: str, subject: str, outline_path: str,
                out_path: Path) -> None:
    import pprint
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = f'''"""DRAFT seed pack: {stage}{subject} — generated offline, NEEDS HUMAN REVIEW.

Generated by scripts/build_seed_pack.py at {stamp}
Outline source: {outline_path or "(LLM 内置知识，未提供考纲文件)"}

This file is NOT active until a human:
  1. reviews every node/edge for pedagogical correctness,
  2. moves it to app/agents/knowledge/seed_packs/{_spec_module_name(stage, subject)}.py,
  3. registers it in seed_packs/__init__.py PACK_MODULES.
"""
from __future__ import annotations

from typing import Any

STAGE = {stage!r}
SUBJECT = {subject!r}

'''
    body = []
    for key in ("CHAPTERS", "NODES", "EDGES", "CONTENTS"):
        value = spec.get(key.lower()) or []
        body.append(f"{key}: list[dict[str, Any]] = "
                    + pprint.pformat(value, width=100, sort_dicts=False) + "\n")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(header + "\n".join(body), encoding="utf-8")


def _load_spec_from_pack_file(path: Path) -> dict:
    """Import a draft/pack .py and return its spec dict (for --check)."""
    import importlib.util
    module_name = path.stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    pack_spec = {"chapters": getattr(mod, "CHAPTERS", []),
                 "nodes": getattr(mod, "NODES", []),
                 "edges": getattr(mod, "EDGES", []),
                 "contents": getattr(mod, "CONTENTS", [])}
    return pack_spec, getattr(mod, "STAGE", ""), getattr(mod, "SUBJECT", "")


async def _generate(args: argparse.Namespace) -> int:
    from app.core.llm_async import get_llm

    outline = ""
    if args.outline:
        outline = Path(args.outline).read_text(encoding="utf-8")

    keep_nodes: list[dict] = []
    keep_prereqs: set[tuple[str, str]] = set()
    if args.keep:
        import importlib
        mod = importlib.import_module(
            f"app.agents.knowledge.seed_packs.{args.keep}")
        keep_nodes = [dict(n) for n in mod.NODES]
        keep_prereqs = {(e["source"], e["target"]) for e in mod.EDGES
                        if e.get("type") == "prerequisite"}

    prompt = _build_prompt(args.stage, args.subject, outline, args.max_nodes,
                           keep_nodes=keep_nodes or None,
                           keep_prereqs=keep_prereqs or None)

    llm = get_llm()
    raw, _usage = await llm.complete(
        [{"role": "user", "content": prompt}], max_tokens=16000)
    try:
        spec = _extract_json(raw)
    except Exception as exc:
        print(f"[FAIL] LLM 输出解析失败: {exc}", file=sys.stderr)
        return 1

    errors = validate_pack(spec, stage=args.stage, subject=args.subject,
                           max_nodes=args.max_nodes,
                           check_registry_collision=not args.no_registry_check,
                           keep_nodes=keep_nodes or None,
                           keep_prereqs=keep_prereqs or None)
    if errors:
        print(f"[FAIL] 校验未通过（{len(errors)} 项），未写出任何文件：", file=sys.stderr)
        for e in errors[:50]:
            print(f"  - {e}", file=sys.stderr)
        return 2

    n_nodes = len(spec.get("nodes") or [])
    n_edges = len(spec.get("edges") or [])
    if args.dry_run:
        print(json.dumps(spec, ensure_ascii=False, indent=2))
        print(f"[OK] dry-run：{n_nodes} 节点 / {n_edges} 边，校验通过，未写文件")
        return 0

    out_path = Path(args.output) if args.output else \
        DRAFTS_DIR / f"{_spec_module_name(args.stage, args.subject)}.py"
    _write_pack(spec, stage=args.stage, subject=args.subject,
                outline_path=args.outline or "", out_path=out_path)
    print(f"[OK] {n_nodes} 节点 / {n_edges} 边，校验通过，草稿已写入 {out_path}")
    print("下一步：人工 review 后移入 app/agents/knowledge/seed_packs/ 并在 __init__.py 注册。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="离线生成 (学段×学科) 知识种子包草稿")
    ap.add_argument("--stage", choices=list(STAGE_SUBJECTS), help="学段")
    ap.add_argument("--subject", choices=list(SUBJECT_TOKENS), help="学科")
    ap.add_argument("--outline", default="", help="考纲/教材目录文本文件（可选）")
    ap.add_argument("--output", default="", help="草稿输出路径（默认 seed_pack_drafts/）")
    ap.add_argument("--max-nodes", type=int, default=120, help="节点数上限")
    ap.add_argument("--dry-run", action="store_true", help="只打印 spec，不写文件")
    ap.add_argument("--no-registry-check", action="store_true",
                    help="跳过与已注册包的 id 冲突检查")
    ap.add_argument("--keep", metavar="PACK_MODULE",
                    help="保留指定已注册包的节点/前置边（扩充替换该包时使用，"
                         "如 pack_senior_math）")
    ap.add_argument("--check", metavar="PACK.py", help="只校验一个已有 pack 文件，不调 LLM")
    args = ap.parse_args()

    if args.check:
        try:
            spec, stage, subject = _load_spec_from_pack_file(Path(args.check))
        except Exception as exc:
            print(f"[FAIL] 读取失败: {exc}", file=sys.stderr)
            return 1
        if not stage or not subject:
            print("[FAIL] 文件缺 STAGE/SUBJECT", file=sys.stderr)
            return 2
        errors = validate_pack(spec, stage=stage, subject=subject,
                               check_registry_collision=False)
        if errors:
            print(f"[FAIL] {args.check} 校验未通过（{len(errors)} 项）：", file=sys.stderr)
            for e in errors[:50]:
                print(f"  - {e}", file=sys.stderr)
            return 2
        print(f"[OK] {args.check} 校验通过（{stage}{subject}，"
              f"{len(spec['nodes'])} 节点 / {len(spec['edges'])} 边）")
        return 0

    if not args.stage or not args.subject:
        ap.error("--stage 和 --subject 必填（或使用 --check）")
    allowed = STAGE_SUBJECTS.get(args.stage, ())
    if allowed and args.subject not in allowed:
        ap.error(f"{args.stage}不开设{args.subject}（{args.stage}仅: {'、'.join(allowed)}）")

    return asyncio.run(_generate(args))


if __name__ == "__main__":
    raise SystemExit(main())
