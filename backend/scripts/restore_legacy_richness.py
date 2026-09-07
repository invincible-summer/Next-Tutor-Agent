#!/usr/bin/env python
"""Restore legacy-node richness into the expanded seed packs (M5.6 repair).

The 21 legacy node ids are referenced by student mastery data AND by M5's own
enrichment surface (aliases drive match_concept/BM25, common_errors drive
[知识智能·易错点], seed contents drive ContentResolver). The pack expansion
(考纲提取) preserved ids/names/difficulties via --keep but rewrote the
descriptive fields. This script merges the ORIGINAL curated richness back:

  - aliases / common_errors: union (legacy first, new kept)
  - description: keep the pack's if present, else legacy
  - CONTENTS for the 4 highest-leverage concepts: legacy verbatim
  - the 5 legacy richer edges (RELATED/MISCONCEPTION): appended if missing

Idempotent. Run from backend/:  python scripts/restore_legacy_richness.py
"""
from __future__ import annotations

import importlib.util
import pprint
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

PACKS = _BACKEND / "app" / "agents" / "knowledge" / "seed_packs"

# legacy id -> (aliases, common_errors, description)
LEGACY: dict[str, tuple[list[str], list[str], str]] = {
    "math.function.definition": (["函数", "定义域"], ["把对应关系当成一一对应"],
                                 "映射关系：每个自变量对应唯一因变量；定义域是自变量取值范围。"),
    "math.function.monotonicity": (["单调性", "单调递增", "单调递减"], ["忽略单调性必须限定区间"],
                                   "函数在区间上递增或递减的性质。"),
    "math.function.extremum": (["极值", "极大值", "极小值"], [],
                               "局部最大/最小值，极值点处导数为零或不存在。"),
    "math.calculus.limit": (["极限", "limit"], ["把极限值当成函数在该点的值"],
                            "变量趋近某值时的趋势；微积分的基石。"),
    "math.calculus.derivative": (["微商", "derivative", "变化率"],
                                 ["把导数当成函数值而非变化率", "混淆导数与差商"],
                                 "函数在某点的瞬时变化率，几何意义是切线斜率。"),
    "math.calculus.integral": (["定积分", "不定积分", "integral"], ["忽略积分常数", "混淆定积分上下限"],
                               "导数的逆运算；几何意义是曲线下面积。"),
    "math.geometry.plane": (["平面几何"], [], ""),
    "math.geometry.vector": (["矢量", "vector"], [], "有大小和方向的量。"),
    "math.geometry.solid": (["立体几何"], [], ""),
    "physics.kinematics.velocity": (["速度", "加速度", "velocity"], ["混淆平均速度与瞬时速度"],
                                    "描述运动状态变化：速度是位移变化率，加速度是速度变化率。"),
    "physics.dynamics.newton_second": (["牛顿第二", "F=ma"], ["忘记求合力，只代入单个力"],
                                       "F=ma：合外力等于质量乘以加速度。"),
    "physics.dynamics.friction": (["摩擦力", "受力分析"], [], ""),
    "physics.mechanics.gravity": (["重力"], [], ""),
    "physics.fluid.buoyancy": (["浮力", "阿基米德"], [], ""),
    "physics.mechanics.energy": (["功", "动能", "势能"], [], ""),
    "physics.mechanics.momentum": (["动量", "冲量"], [], ""),
    "chemistry.atom": (["原子"], [], ""),
    "chemistry.bond": (["化学键", "共价键", "离子键"], [], ""),
    "chemistry.reaction": (["化学反应"], [], ""),
    "biology.cell": (["细胞"], [], ""),
    "biology.photosynthesis": (["光合作用"], [], ""),
}

LEGACY_CONTENTS: dict[str, dict] = {
    "math.calculus.derivative": {
        "concept_id": "math.calculus.derivative",
        "definition": "导数刻画函数在某点的瞬时变化率，几何上等于切线斜率。",
        "formula": "f'(x) = lim_{dx->0} [f(x+dx) - f(x)] / dx",
        "example": "自由落体位移 s(t)=1/2 g t^2，速度 v=s'(t)=g t。",
        "exercise_hint": "用定义法求 f(x)=x^2 在 x=1 处的导数。",
        "source": "seed"},
    "physics.dynamics.newton_second": {
        "concept_id": "physics.dynamics.newton_second",
        "definition": "物体加速度与所受合外力成正比、与质量成反比，方向与合力相同。",
        "formula": "F = m * a",
        "example": "1 kg 物体受 2 N 水平合力，加速度 a = 2 m/s^2。",
        "exercise_hint": "斜面上物体，画受力图后用 F=ma 求加速度。",
        "source": "seed"},
    "math.calculus.limit": {
        "concept_id": "math.calculus.limit",
        "definition": "当自变量无限趋近某值时，函数值趋近的那个确定值。",
        "formula": "lim_{x->a} f(x) = L",
        "example": "lim_{x->0} sin(x)/x = 1（重要极限）。",
        "exercise_hint": "求 lim_{x->2} (x^2-4)/(x-2)。",
        "source": "seed"},
    "physics.kinematics.velocity": {
        "concept_id": "physics.kinematics.velocity",
        "definition": "速度是位移对时间的变化率；加速度是速度对时间的变化率。",
        "formula": "v = ds/dt， a = dv/dt",
        "example": "匀加速直线运动 v(t) = v0 + a t。",
        "exercise_hint": "已知 s(t)=5t^2，求 t=2 时的瞬时速度。",
        "source": "seed"},
}

LEGACY_EDGES: list[dict] = [
    {"source": "math.calculus.derivative", "target": "math.function.extremum", "type": "related"},
    {"source": "math.calculus.derivative", "target": "math.geometry.vector", "type": "related"},
    {"source": "math.calculus.derivative", "target": "math.calculus.integral", "type": "misconception"},
    {"source": "physics.mechanics.momentum", "target": "physics.mechanics.energy", "type": "related"},
    {"source": "physics.mechanics.gravity", "target": "physics.dynamics.friction", "type": "related"},
]

PACK_BY_TOKEN = {"math": "pack_senior_math", "physics": "pack_senior_physics",
                 "chemistry": "pack_junior_chemistry", "biology": "pack_junior_biology"}


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _union(first: list, rest: list) -> list:
    out = list(first)
    for x in rest:
        if x not in out:
            out.append(x)
    return out


def _rewrite(path: Path, mod, nodes, edges, contents) -> None:
    header = f'''"""Seed pack (registered): {mod.STAGE}{mod.SUBJECT} — 考纲提取 + legacy 富字段已合并。

Generated offline from 考纲, machine-validated by seed_packs/validate.py,
legacy richness merged by restore_legacy_richness.py.

Edit by hand with care: node ids are referenced by student mastery data;
PREREQUISITE edges feed the learning-order DAG.
"""
from __future__ import annotations

from typing import Any

STAGE = {mod.STAGE!r}
SUBJECT = {mod.SUBJECT!r}

'''
    body = []
    body.append("CHAPTERS: list[dict[str, Any]] = "
                + pprint.pformat(mod.CHAPTERS, width=100, sort_dicts=False) + "\n")
    body.append("NODES: list[dict[str, Any]] = "
                + pprint.pformat(nodes, width=100, sort_dicts=False) + "\n")
    body.append("EDGES: list[dict[str, Any]] = "
                + pprint.pformat(edges, width=100, sort_dicts=False) + "\n")
    body.append("CONTENTS: list[dict[str, Any]] = "
                + pprint.pformat(contents, width=100, sort_dicts=False) + "\n")
    path.write_text(header + "\n".join(body), encoding="utf-8")


def main() -> int:
    from app.agents.knowledge.seed_packs.validate import validate_pack

    patched = 0
    for token, module_name in PACK_BY_TOKEN.items():
        path = PACKS / f"{module_name}.py"
        mod = _load(path)
        nodes = [dict(n) for n in mod.NODES]
        edges = [dict(e) for e in mod.EDGES]
        contents = [dict(c) for c in mod.CONTENTS]

        legacy_ids = [i for i in LEGACY if i.split(".")[0] == token]
        by_id = {n["id"]: n for n in nodes}
        other_names = {n["name"] for n in nodes}
        for lid in legacy_ids:
            if lid not in by_id:
                print(f"[FATAL] {module_name} 缺 legacy 节点 {lid}", file=sys.stderr)
                return 1
            aliases, errors, desc = LEGACY[lid]
            node = by_id[lid]
            # drop legacy aliases that are now another node's NAME in this
            # pack (e.g. 功 is its own node in the expanded physics pack);
            # the name match resolves there anyway.
            safe = [a for a in aliases if a not in other_names or a == node["name"]]
            node["aliases"] = _union(safe, node.get("aliases") or [])
            # cap at the legacy size so appended MISCONCEPTION-edge hints are
            # not pushed out of context_builder's errs[:3] slice
            node["common_errors"] = _union(errors, node.get("common_errors") or [])[:2]
            if not node.get("description") and desc:
                node["description"] = desc

        have = {(e["source"], e["target"], e["type"]) for e in edges}
        for le in LEGACY_EDGES:
            if le["source"].split(".")[0] == token and \
                    (le["source"], le["target"], le["type"]) not in have:
                edges.append(dict(le))

        content_ids = {c["concept_id"] for c in contents}
        for cid, legacy_c in LEGACY_CONTENTS.items():
            if cid.split(".")[0] != token:
                continue
            if cid in content_ids:
                contents[:] = [legacy_c if c["concept_id"] == cid else c
                               for c in contents]
            else:
                contents.append(dict(legacy_c))

        errors = validate_pack(
            {"chapters": mod.CHAPTERS, "nodes": nodes, "edges": edges,
             "contents": contents},
            stage=mod.STAGE, subject=mod.SUBJECT, check_registry_collision=False)
        if errors:
            print(f"[FAIL] {module_name} 合并后校验未通过:", file=sys.stderr)
            for e in errors[:20]:
                print(f"  - {e}", file=sys.stderr)
            return 1
        _rewrite(path, mod, nodes, edges, contents)
        patched += 1
        print(f"[OK] {module_name}: {len(legacy_ids)} 个 legacy 节点富字段已合并")

    print(f"done ({patched} packs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
