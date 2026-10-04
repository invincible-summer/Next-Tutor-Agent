#!/usr/bin/env python3
"""Backend CI shard planner.

``ci.yml`` cannot enumerate the backend unittest tree itself, so this guard
walks ``services/api/tests/**/test_*.py``, groups the packages into CI shards
and prints a GitHub Actions matrix (one JSON line, consumed via
``fromJSON(needs.backend-shards.outputs.matrix)``).

Two invariants keep the matrix honest:

1. every discovered test package must be assigned to exactly one shard — a new
   domain fails the planner loudly instead of silently never running in CI;
2. packages whose tests invoke the real Node/Chromium renderer (classroom
   render pipeline, illustration PNG preview, diagram library checks, the
   guest-access suite that compiles illustration scenes, and the site
   assistant's lesson generate/retry/export actions that run the classroom
   pipeline) live in ``full`` shards; see docs/development/testing.md
   ("环境与准备").

Usage::

    python3 scripts/repo/plan_backend_shards.py            # JSON matrix
    python3 scripts/repo/plan_backend_shards.py --print     # human-readable
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "services/api" / "tests"

# shard name -> (setup flavor, [package dirs under services/api/tests])
# full  = python + node + pnpm + Playwright Chromium + classroom assets
# python = python test dependencies only
SHARDS: dict[str, tuple[str, list[str]]] = {
    # Real classroom renderer: node checker + offline courseware assets.
    "classroom": ("full", ["classroom"]),
    # Diagram library browser checks and v2 illustration PNG pipelines.
    "diagrams": ("full", ["diagrams", "illustration"]),
    # Non-agents backend domains; identity's guest suite compiles real
    # illustration scenes, so the whole platform shard keeps full setup.
    "platform": ("full", ["api", "core", "identity", "notes", "voice"]),
    "knowledge": ("python", ["agents/knowledge", "agents/memory",
                             "agents/evaluation", "agents/ux_intelligence"]),
    "assessment": ("python", ["agents/assessment", "agents/student_model",
                              "agents/teaching_engine",
                              "agents/learning_orchestration",
                              "agents/skill_runtime"]),
    # The site assistant's lesson actions (generate/retry/export) run the
    # real classroom pipeline, so this shard needs the renderer toolchain.
    "supervisor": ("full", ["agents/supervisor", "agents/site_assistant"]),
}

def discover() -> dict[str, list[str]]:
    """Map every package dir containing tests to its module names."""
    packages: dict[str, list[str]] = {}
    for path in sorted(TESTS.rglob("test_*.py")):
        relative = path.relative_to(TESTS).with_suffix("")
        parts = relative.parts
        if len(parts) == 1:
            key = "(root)"
        elif parts[0] == "agents":
            key = "agents/" + parts[1]
        else:
            key = parts[0]
        module = "tests." + ".".join(parts)
        packages.setdefault(key, []).append(module)
    return packages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print", dest="human", action="store_true",
                        help="print a human-readable shard table instead of JSON")
    args = parser.parse_args()

    packages = discover()
    assignment: dict[str, str] = {}
    for shard, (_, dirs) in SHARDS.items():
        for package in dirs:
            if package in assignment:
                print(f"shard planner: package {package!r} assigned twice "
                      f"({assignment[package]} and {shard})", file=sys.stderr)
                return 1
            assignment[package] = shard

    missing = sorted(set(packages) - set(assignment))
    if missing:
        print("shard planner: unassigned test packages (add them to SHARDS "
              "in scripts/repo/plan_backend_shards.py):", file=sys.stderr)
        for package in missing:
            print(f"  {package} ({len(packages[package])} modules)", file=sys.stderr)
        return 1
    dropped = sorted(set(assignment) - set(packages))
    if dropped:
        print("shard planner: assigned packages with no test files (remove "
              "them from SHARDS):", file=sys.stderr)
        for package in dropped:
            print(f"  {package}", file=sys.stderr)
        return 1

    matrix = []
    for shard, (setup, dirs) in SHARDS.items():
        modules: list[str] = []
        for package in dirs:
            modules.extend(packages[package])
        matrix.append({
            "shard": shard,
            "setup": setup,
            "suites": " ".join(modules),
            "files": len(modules),
        })

    if args.human:
        for entry in matrix:
            print(f"{entry['shard']:<12} {entry['setup']:<7} "
                  f"{entry['files']:>3} files  {entry['suites']}")
        return 0
    # Consumed as `matrix: ${{ fromJSON(...) }}` in ci.yml: an include-only
    # matrix, one entry per shard.
    print(json.dumps({"include": matrix}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
