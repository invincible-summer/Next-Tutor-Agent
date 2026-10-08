#!/usr/bin/env python3
"""Validate the chem-lab content directory and (re)generate manifest.json.

Usage:
    validate_pack.py [--write-manifest]

``--write-manifest`` also refreshes ``content/packs/*.json`` — the runtime
pack snapshots produced by ``Catalog.build_pack`` (schema defaults applied).
The TypeScript domain engine's replay-vector tests consume those snapshots
so pydantic defaults stay single-sourced in Python.

Exit code 0 when the catalog loads cleanly (schema, manifest hashes, closed
DSL, cross references); 1 with a readable error otherwise.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

from app.chem_lab.catalog import CONTENT_DIR, Catalog  # noqa: E402
from app.chem_lab.errors import ChemLabError  # noqa: E402

MANIFEST_NAME = "manifest.json"


def rebuild_manifest(content_dir: Path) -> dict:
    files: dict[str, str] = {}
    for path in sorted(content_dir.rglob("*.json")):
        relpath = path.relative_to(content_dir).as_posix()
        if relpath == MANIFEST_NAME:
            continue
        files[relpath] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"schema_version": 1, "content_version": "1.0.0", "files": files}
    (content_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return manifest


def rebuild_runtime_packs(catalog: Catalog, content_dir: Path) -> list[str]:
    """Write one runtime-pack snapshot per experiment (build_pack output)."""
    packs_dir = content_dir / "packs"
    packs_dir.mkdir(exist_ok=True)
    wanted = set()
    for key in sorted(catalog.experiments):
        pack = catalog.experiments[key]
        name = f"{pack.id}@{pack.pack_version}.json"
        wanted.add(name)
        runtime = catalog.build_pack(pack.id, pack.pack_version)
        (packs_dir / name).write_text(
            json.dumps(runtime, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    for stale in packs_dir.glob("*.json"):
        if stale.name not in wanted:
            stale.unlink()
    return sorted(wanted)


def main() -> int:
    write_manifest = "--write-manifest" in sys.argv
    if write_manifest:
        # Pass 1: cover the current sources so the catalog loads; then write
        # fresh runtime packs and rebuild the manifest to cover them too.
        rebuild_manifest(CONTENT_DIR)
        try:
            catalog = Catalog()
        except ChemLabError as exc:
            print(f"校验失败 [{exc.code}]: {exc}")
            return 1
        packs = rebuild_runtime_packs(catalog, CONTENT_DIR)
        manifest = rebuild_manifest(CONTENT_DIR)
        print(f"manifest.json 已重建：{len(manifest['files'])} 个文件"
              f"（含 {len(packs)} 个 runtime pack 快照）")
    try:
        catalog = Catalog()
    except ChemLabError as exc:
        print(f"校验失败 [{exc.code}]: {exc}")
        return 1
    summaries = catalog.list_summaries()
    print(f"内容目录校验通过：{CONTENT_DIR.relative_to(REPO_ROOT)}")
    print(f"  species={len(catalog.species)} rules={len(catalog.rules)} "
          f"equipment={len(catalog.equipment)} concepts={len(catalog.concepts)} "
          f"experiments={len(catalog.experiments)}")
    for summary in summaries:
        vector_count = len(catalog.vector_files(summary["id"]))
        print(f"  - {summary['id']}@{summary['pack_version']} "
              f"hash={summary['pack_hash'][:12]}… vectors={vector_count}")
    if write_manifest:
        # Re-validate against the freshly written manifest.
        try:
            Catalog()
        except ChemLabError as exc:
            print(f"重建后复检失败 [{exc.code}]: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
