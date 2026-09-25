#!/usr/bin/env python3
"""Repository hygiene guard for the sanitized release snapshot.

This release is a copyright-sanitized reconstruction: textbook sources, parsed
text, chunks, knowledge graphs, vector artifacts and demo runtime data are
intentionally absent from the repository and must never be tracked.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

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
)
FORBIDDEN_EXACT = {"plan.md", "docs/The_Website_deployment_plan.md"}
FORBIDDEN_SUFFIXES = (".pdf", ".epub", ".djvu", ".mobi", ".azw", ".azw3", ".orig.pdf", ".orig.epub", ".chunks.json")


def check_no_runtime_data_tracked() -> None:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout
    files = [f for f in out.split("\0") if f]
    bad = [
        f
        for f in files
        if f in FORBIDDEN_EXACT
        or f.startswith(FORBIDDEN_PREFIXES)
        or f.endswith(FORBIDDEN_SUFFIXES)
    ]
    if bad:
        raise SystemExit(
            "tracked files violate the distribution policy (runtime/derived data):\n  "
            + "\n  ".join(sorted(bad)[:50])
        )


def check_single_worker_invariant() -> None:
    unit = ROOT / "deploy" / "edu-backend.service"
    if unit.exists() and "--workers 1" not in unit.read_text(encoding="utf-8"):
        raise SystemExit("deploy/edu-backend.service must pin --workers 1")


def main() -> int:
    check_no_runtime_data_tracked()
    check_single_worker_invariant()
    print("repository invariants ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
