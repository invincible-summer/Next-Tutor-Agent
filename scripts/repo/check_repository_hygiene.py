#!/usr/bin/env python3
"""Repository hygiene / copyright guard.

Enforces the distribution policy: the repository ships source code, tests,
deployment templates, docs and explicitly-synthetic demo fixtures only.
Textbook sources, parsed/OCR text, chunks, knowledge graphs, vector indexes
and per-user runtime state must never be tracked — not at HEAD and not
anywhere in the object database.
"""
from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_PREFIXES = (
    "chat_history/",
    "knowledge/",
    "notes/",
    "students/",
    "users/",
    "uploads/",
    "acceptance-reports/",
    "docs/show/",
    "deploy/pages-demo-extra/",
    "frontend/public/demo/",
    "frontend/out/",
    "backend/traces/",
    "apps/web/public/demo/",
    "apps/web/out/",
    "services/api/traces/",
    "services/api/chat_history/",
    "services/api/knowledge/",
)
FORBIDDEN_EXACT = {
    "plan.md",
    "docs/The_Website_deployment_plan.md",
    "deploy/seed_demo_account.py",
}
FORBIDDEN_SUFFIXES = (
    ".pdf", ".epub", ".djvu", ".mobi", ".azw", ".azw3",
    ".orig.pdf", ".orig.epub", ".chunks.json",
)
# Derived-product naming conventions (paths and basenames).
FORBIDDEN_NAME_PARTS = (
    "ocr_cache", "ocr-cache", "extracted_text", "extracted-text",
    "vector_shard", "vector-shard", ".embedding.json",
)
MAX_FILE_BYTES = 5 * 1024 * 1024
# Fixture provenance keys that must never appear inside fixtures/demo.
FORBIDDEN_FIXTURE_KEYS = {
    "file_id", "volume_id", "page", "text_sha256", "chunk_id",
    "source_path", "file_ids",
}

TEXT_SUFFIXES = {".py", ".md", ".ts", ".tsx", ".js", ".mjs", ".json",
                 ".yml", ".yaml", ".sh", ".toml", ".txt", ".example"}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout


def load_allowlist() -> dict[str, str]:
    path = ROOT / "scripts/repo/repo-policy.toml"
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {k: str(v) for k, v in data.get("large_file_allowlist", {}).items()}


def check_tracked_files(problems: list[str]) -> None:
    allow = load_allowlist()
    for path in git("ls-files").splitlines():
        if not path:
            continue
        if path in FORBIDDEN_EXACT or path.startswith(FORBIDDEN_PREFIXES):
            problems.append(f"forbidden path tracked: {path}")
            continue
        if path.lower().endswith(FORBIDDEN_SUFFIXES):
            problems.append(f"forbidden suffix tracked: {path}")
            continue
        if any(part in path.lower() for part in FORBIDDEN_NAME_PARTS):
            problems.append(f"derived-product name tracked: {path}")
            continue
        f = ROOT / path
        if f.is_file():
            size = f.stat().st_size
            if size > MAX_FILE_BYTES:
                if path not in allow:
                    problems.append(
                        f"tracked file >5MB without allowlist entry: {path} ({size} bytes)")
                del allow[path]
    for leftover in allow:
        problems.append(f"repo-policy.toml allowlists a missing file: {leftover}")


def check_full_history(problems: list[str]) -> None:
    """Audit every object across all refs, not only HEAD."""
    objects = git("rev-list", "--objects", "--all")
    seen: set[str] = set()
    for line in objects.splitlines():
        if not line:
            continue
        parts = line.split(" ", 1)
        path = parts[1] if len(parts) > 1 else ""
        if not path:
            continue
        if path in seen:
            continue
        seen.add(path)
        if path in FORBIDDEN_EXACT or path.startswith(FORBIDDEN_PREFIXES):
            problems.append(f"forbidden path in history: {path}")
        elif path.lower().endswith(FORBIDDEN_SUFFIXES):
            problems.append(f"forbidden suffix in history: {path}")


def check_demo_fixtures(problems: list[str]) -> None:
    fixtures = ROOT / "fixtures" / "demo"
    if not fixtures.is_dir():
        problems.append("fixtures/demo/ missing")
        return
    readme = fixtures / "README.md"
    if not readme.is_file() or "synthetic" not in readme.read_text(encoding="utf-8").lower():
        problems.append("fixtures/demo/README.md must declare synthetic provenance")

    def walk(obj, path: str) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in FORBIDDEN_FIXTURE_KEYS:
                    problems.append(f"fixture provenance key '{k}' in {path}")
                walk(v, path)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, path)

    import json
    for f in sorted(fixtures.glob("*.json")):
        try:
            walk(json.loads(f.read_text(encoding="utf-8")), f.name)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"fixture not valid JSON ({f.name}): {exc}")
        # Synthetic ids only: no real textbook ids / publisher names.
        text = f.read_text(encoding="utf-8")
        for marker in ("人民教育出版社", "山东科学技术出版社", "普通高中教科书",
                       "人教A版", "usr_12e410b4e2"):
            if marker in text:
                problems.append(f"real-world identifier '{marker}' in fixture {f.name}")


def check_runtime_isolation(problems: list[str]) -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for needed in (".runtime/", "chat_history/", "knowledge/", "notes/",
                   "students/", "users/", "plan.md"):
        if needed not in gitignore:
            problems.append(f".gitignore missing runtime ignore rule: {needed}")


def main() -> int:
    problems: list[str] = []
    check_tracked_files(problems)
    check_full_history(problems)
    check_demo_fixtures(problems)
    check_runtime_isolation(problems)
    if problems:
        print("repository hygiene: FAILED")
        for p in problems[:60]:
            print(f"  - {p}")
        return 1
    print("repository hygiene: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
