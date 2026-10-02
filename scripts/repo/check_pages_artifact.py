#!/usr/bin/env python3
"""GitHub Pages demo artifact contract (copyright / size / manifest).

Validates the exported ``apps/web/public/demo`` snapshot before it ships:
synthetic-only content, complete manifest, no textbook binaries, no external
textbook links, no private provider payloads, and a hard size budget.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FORBIDDEN_SUFFIXES = (".pdf", ".epub", ".djvu", ".mobi", ".azw", ".azw3",
                      ".orig.pdf", ".orig.epub", ".chunks.json")
# Private provider/credential fields that must never reach a public artifact.
PRIVATE_KEYS = {"thinking", "reasoning_content", "password_hash", "trace_ids",
                "raw_response", "raw_prompt", "api_key"}
# Real-world identifiers that must not appear anywhere in the artifact.
REAL_WORLD_MARKERS = ("usr_12e410b4e2", "人民教育出版社",
                      "山东科学技术出版社", "普通高中教科书", "人教A版")
REQUIRED_ROUTE_KEYS = ("sessions", "notes", "workspaces", "lessons")
REQUIRED_RESPONSE_PATHS = ("/sidebar", "/library", "/textbooks",
                           "/knowledge/catalog", "/notes/vault")
DEFAULT_MAX_BYTES = 100_000_000


def iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            yield path


def scan_json_for_private(value, path: str, problems: list[str]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if k in PRIVATE_KEYS:
                problems.append(f"private field '{k}' in {path}")
            scan_json_for_private(v, path, problems)
    elif isinstance(value, list):
        for item in value:
            scan_json_for_private(item, path, problems)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path,
                        help="exported demo directory (apps/web/public/demo)")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    args = parser.parse_args()
    root: Path = args.artifact
    problems: list[str] = []

    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        print(f"pages artifact: manifest.json missing under {root}")
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if manifest.get("schema_version") != 1:
        problems.append("manifest schema_version must be 1")
    if manifest.get("demo_id") != "usr_pagesdemo01":
        problems.append("manifest demo_id must be the synthetic demo account")
    for route in REQUIRED_ROUTE_KEYS:
        entries = manifest.get("routes", {}).get(route, [])
        if not entries:
            problems.append(f"manifest routes.{route} is empty")

    referenced: set[str] = set()

    def register(rel: str, where: str) -> None:
        target = root / rel
        if not target.is_file():
            problems.append(f"manifest references missing file: {rel} ({where})")
            return
        if rel in referenced:
            problems.append(f"manifest references file twice: {rel}")
        referenced.add(rel)

    responses: dict = manifest.get("responses", {})
    assets: dict = manifest.get("assets", {})
    if not responses:
        problems.append("manifest has no responses")
    for path, rel in responses.items():
        if not isinstance(rel, str) or not rel.startswith("responses/"):
            problems.append(f"bad responses entry for {path}: {rel}")
            continue
        register(rel, f"response {path}")
        if not path.startswith("/"):
            problems.append(f"response key is not an API path: {path}")
    for path, entry in assets.items():
        url = entry.get("url") if isinstance(entry, dict) else None
        if not isinstance(url, str) or not url.startswith("assets/"):
            problems.append(f"bad assets entry for {path}: {entry}")
            continue
        register(url, f"asset {path}")

    for required in REQUIRED_RESPONSE_PATHS:
        if required not in responses:
            problems.append(f"required response missing from manifest: {required}")

    total = 0
    for path in iter_files(root):
        rel = path.relative_to(root).as_posix()
        if rel not in referenced:
            problems.append(f"unreferenced file in artifact: {rel}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name.lower().endswith(FORBIDDEN_SUFFIXES):
            problems.append(f"forbidden file type in artifact: {rel}")
        total += path.stat().st_size
        if path.suffix == ".json" and rel.startswith("responses/"):
            try:
                scan_json_for_private(json.loads(path.read_text(encoding="utf-8")),
                                      rel, problems)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"response is not valid JSON ({rel}): {exc}")
        if path.suffix in {".json", ".txt", ".md", ".html"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            if "raw.githubusercontent.com" in text:
                problems.append(f"external textbook link in {rel}")
            for marker in REAL_WORLD_MARKERS:
                if marker in text:
                    problems.append(f"real-world identifier '{marker}' in {rel}")

    if total > args.max_bytes:
        problems.append(f"artifact exceeds size budget: {total} > {args.max_bytes} bytes")

    if problems:
        print("pages artifact: FAILED")
        for p in problems[:60]:
            print(f"  - {p}")
        return 1
    print(f"pages artifact: ok ({len(responses)} responses, "
          f"{len(assets)} assets, {total / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
