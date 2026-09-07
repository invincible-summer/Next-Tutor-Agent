"""Seed skill graph: a curated directed DAG of common K-12 skills.

Each node is {id, name, subject, prerequisites, difficulty}. The graph is
intentionally small but real -- it covers the dependency chains that the
adaptation engine needs to act on (e.g. "to learn derivative you first need
function monotonicity"). This is NOT a curriculum; it is the minimum the
student-intelligence layer must know about to make adaptive decisions.

The graph is extensible: manager.py merges any seed node with auto-derived
nodes (concepts seen in conversation that are not in the seed), so an
unseeded concept still gets tracked -- it just has no prerequisite links
until a later (optional) LLM-assisted graph expansion (V4).

Node ids are dotted paths: <subject>.<area>.<skill>, e.g.
"math.function.monotonicity".
"""
from __future__ import annotations

from typing import Any

# difficulty: 1 (easy/foundational) .. 5 (hard/advanced)
_SEED: list[dict[str, Any]] = [
    # --- math: function chain (classic prerequisite story) ---
    {"id": "math.function.definition", "name": "函数定义与定义域",
     "subject": "数学", "prerequisites": [], "difficulty": 1},
    {"id": "math.function.monotonicity", "name": "函数单调性",
     "subject": "数学", "prerequisites": ["math.function.definition"], "difficulty": 2},
    {"id": "math.function.extremum", "name": "函数极值",
     "subject": "数学", "prerequisites": ["math.function.monotonicity"], "difficulty": 3},
    {"id": "math.calculus.limit", "name": "极限思想",
     "subject": "数学", "prerequisites": ["math.function.definition"], "difficulty": 3},
    {"id": "math.calculus.derivative", "name": "导数",
     "subject": "数学",
     "prerequisites": ["math.function.monotonicity", "math.calculus.limit"], "difficulty": 4},
    {"id": "math.calculus.integral", "name": "积分",
     "subject": "数学", "prerequisites": ["math.calculus.derivative"], "difficulty": 5},

    # --- math: geometry / vectors ---
    {"id": "math.geometry.plane", "name": "平面几何基础",
     "subject": "数学", "prerequisites": [], "difficulty": 1},
    {"id": "math.geometry.vector", "name": "向量",
     "subject": "数学", "prerequisites": ["math.geometry.plane"], "difficulty": 3},
    {"id": "math.geometry.solid", "name": "空间几何",
     "subject": "数学",
     "prerequisites": ["math.geometry.vector", "math.geometry.plane"], "difficulty": 4},

    # --- physics: mechanics chain (the "Newton -> forces -> motion" story) ---
    {"id": "physics.kinematics.velocity", "name": "速度与加速度",
     "subject": "物理", "prerequisites": [], "difficulty": 2},
    {"id": "physics.dynamics.newton_second", "name": "牛顿第二定律",
     "subject": "物理", "prerequisites": ["physics.kinematics.velocity"], "difficulty": 3},
    {"id": "physics.dynamics.friction", "name": "摩擦力与受力分析",
     "subject": "物理",
     "prerequisites": ["physics.dynamics.newton_second"], "difficulty": 3},
    {"id": "physics.mechanics.gravity", "name": "重力",
     "subject": "物理", "prerequisites": ["physics.dynamics.newton_second"], "difficulty": 2},
    {"id": "physics.fluid.buoyancy", "name": "浮力",
     "subject": "物理",
     "prerequisites": ["physics.dynamics.newton_second", "physics.mechanics.gravity"],
     "difficulty": 3},
    {"id": "physics.mechanics.energy", "name": "功与能",
     "subject": "物理", "prerequisites": ["physics.dynamics.newton_second"], "difficulty": 4},
    {"id": "physics.mechanics.momentum", "name": "动量与冲量",
     "subject": "物理",
     "prerequisites": ["physics.dynamics.newton_second"], "difficulty": 4},

    # --- chemistry: a small reaction chain ---
    {"id": "chemistry.atom", "name": "原子结构",
     "subject": "化学", "prerequisites": [], "difficulty": 2},
    {"id": "chemistry.bond", "name": "化学键",
     "subject": "化学", "prerequisites": ["chemistry.atom"], "difficulty": 3},
    {"id": "chemistry.reaction", "name": "化学反应",
     "subject": "化学", "prerequisites": ["chemistry.bond"], "difficulty": 3},

    # --- biology ---
    {"id": "biology.cell", "name": "细胞", "subject": "生物", "prerequisites": [], "difficulty": 2},
    {"id": "biology.photosynthesis", "name": "光合作用",
     "subject": "生物", "prerequisites": ["biology.cell"], "difficulty": 3},
]


def seed_nodes() -> list[dict[str, Any]]:
    """Return a deep copy of the seed node list (callers may mutate freely)."""
    import copy
    return [dict(n, prerequisites=list(n["prerequisites"])) for n in _SEED]
