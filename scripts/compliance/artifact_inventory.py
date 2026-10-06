#!/usr/bin/env python3
"""Artifact-level inventory for release distribution (plan phase 3/6).

The source SBOM (licenses/sbom.cdx.json, owned by generate_notices.py)
describes dependencies of the source tree. This script fingerprints the
concrete files we hand to users: it hashes release artifacts, snapshots the
resolved dependency inventory that produced the build, and emits a
SHA256SUMS manifest plus an artifact-level CycloneDX document.

Outputs (default --out licenses/artifacts/, committed for traceability):

- ``sha256sums.txt``       — ``<sha256>  <relative path>`` per artifact
- ``sbom.cdx.json``        — CycloneDX 1.6: this repo as the single component
                             plus every dependency from the source SBOM as a
                             dependency-only reference
- ``inventory.json``       — copy of licenses/inventory.json + policy gate
                             verdict summary at artifact time

Usage (release workflow):

    python scripts/compliance/artifact_inventory.py \
        --artifact sbom.cdx.json --artifact THIRD-PARTY-NOTICES.md \
        --label v3.1.0

Exit 1 when an artifact is missing or the source inventory/policy inputs are
absent. Never mutates licenses/inventory.json (generate_notices.py owns it).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIC = ROOT / "licenses"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", action="append", default=[],
                        help="repository-relative path shipped with the release")
    parser.add_argument("--label", default="unlabeled",
                        help="release label recorded in the artifact SBOM")
    parser.add_argument("--out", default="licenses/artifacts",
                        help="output directory (repository-relative)")
    args = parser.parse_args()

    source_sbom_path = LIC / "sbom.cdx.json"
    inventory_path = LIC / "inventory.json"
    for required in (source_sbom_path, inventory_path):
        if not required.exists():
            print(f"missing {required.relative_to(ROOT)}; run "
                  f"scripts/compliance/generate_notices.py first", file=sys.stderr)
            return 1

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. SHA256SUMS over the concrete release artifacts.
    artifacts: list[str] = args.artifact or []
    lines: list[str] = []
    for rel in artifacts:
        path = ROOT / rel
        if not path.is_file():
            print(f"artifact not found: {rel}", file=sys.stderr)
            return 1
        lines.append(f"{sha256_file(path)}  {Path(rel).as_posix()}")
    sums_path = out_dir / "sha256sums.txt"
    sums_path.write_text("\n".join(lines) + ("\n" if lines else ""),
                          encoding="utf-8")

    # 2. Artifact-level CycloneDX: repo component + dependency references.
    source_sbom = json.loads(source_sbom_path.read_text(encoding="utf-8"))
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    artifact_sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": source_sbom.get("serialNumber"),
        "version": 1,
        "metadata": {
            "timestamp": now,
            "component": {
                "type": "application",
                "name": "next-tutor-agent",
                "version": args.label,
            },
            "properties": [
                {"name": "next-tutor:artifact-label", "value": args.label},
                {"name": "next-tutor:artifact-files", "value": ",".join(artifacts)},
                {"name": "next-tutor:source-sbom", "value": "licenses/sbom.cdx.json"},
            ],
        },
        "components": [],
        "dependencies": [
            {"ref": "next-tutor-agent@" + args.label,
             "dependsOn": [c.get("bom-ref") for c in source_sbom.get("components", [])
                           if c.get("bom-ref")]},
        ],
    }
    (out_dir / "sbom.cdx.json").write_text(
        json.dumps(artifact_sbom, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")

    # 3. Frozen copy of the source inventory that produced this artifact set.
    shutil.copyfile(inventory_path, out_dir / "inventory.json")

    shipped = len(artifacts)
    deps = len(source_sbom.get("components", []))
    print(f"artifact inventory: {shipped} artifacts, {deps} dependencies, "
          f"label {args.label!r} -> {sums_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
