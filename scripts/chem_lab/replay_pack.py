#!/usr/bin/env python3
"""Replay the chem-lab vectors and check them against the Python engine.

Usage:
    replay_pack.py [--experiment <id>] [--write]

Without flags every vector under content/vectors/ is verified (state_hashes,
final_hash, accepted mask, expected event kinds). ``--write`` re-runs each
vector and rewrites its hash fields — required after any engine-semantics
change, and the file must then be re-reviewed before commit.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

from app.chem_lab.catalog import Catalog  # noqa: E402
from app.chem_lab.engine import replay  # noqa: E402
from app.chem_lab.errors import ChemLabError  # noqa: E402


def iter_vectors(catalog: Catalog, experiment_filter: str | None):
    for key in catalog.experiment_keys():
        experiment_id = key.split("@", 1)[0]
        if experiment_filter and experiment_id != experiment_filter:
            continue
        for path in catalog.vector_files(experiment_id):
            yield experiment_id, path


def rewrite_vector(pack: dict, path: Path) -> tuple[int, int]:
    vector = json.loads(path.read_text(encoding="utf-8"))
    script = vector.get("script", [])
    state, events, hashes, accepted = replay.run_script(
        pack, script,
        mode=vector.get("mode", "guided"),
        session_seed=int(vector.get("session_seed", 0)))
    vector["state_hashes"] = hashes
    vector["final_hash"] = hashes[-1]
    vector["accepted"] = accepted
    path.write_text(json.dumps(vector, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(script), len(events)


def main() -> int:
    write = "--write" in sys.argv
    experiment_filter = None
    if "--experiment" in sys.argv:
        index = sys.argv.index("--experiment")
        experiment_filter = sys.argv[index + 1]
    try:
        catalog = Catalog()
    except ChemLabError as exc:
        print(f"内容目录加载失败 [{exc.code}]: {exc}")
        return 1

    failures = 0
    total = 0
    for experiment_id, path in iter_vectors(catalog, experiment_filter):
        total += 1
        try:
            pack = catalog.build_pack(experiment_id)
        except ChemLabError as exc:
            print(f"✗ {path.name}: 实验包构建失败 [{exc.code}]: {exc}")
            failures += 1
            continue
        if write:
            commands, events = rewrite_vector(pack, path)
            print(f"✎ {path.relative_to(catalog.content_dir)}: 重写 {commands} 条命令 / {events} 个事件")
            continue
        vector = json.loads(path.read_text(encoding="utf-8"))
        problems = replay.verify_vector(pack, vector)
        if problems:
            failures += 1
            print(f"✗ {path.relative_to(catalog.content_dir)}")
            for problem in problems:
                print(f"    {problem}")
        else:
            print(f"✓ {path.relative_to(catalog.content_dir)}")
    if total == 0:
        print("没有找到任何向量（--experiment 过滤是否正确？）")
        return 1
    if write:
        print("哈希已重写。请重新运行 validate_pack.py --write-manifest 并人工复核 diff。")
        return 0
    print(f"{'全部通过' if failures == 0 else '存在失败'}：{total - failures}/{total}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
