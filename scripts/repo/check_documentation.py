#!/usr/bin/env python3
"""Documentation and layout guard.

Complements ``check_repository_hygiene.py`` (copyright / runtime data / history)
with documentation-structure checks:

1. every relative Markdown link in tracked ``*.md`` resolves to an existing file;
2. required module READMEs exist;
3. tracked source / test / config contains no stale ``plan.md §`` references;
4. no tracked ``*_PLAN.md`` remains;
5. banned legacy paths (``backend/``, ``frontend/``, ``The_Website_deployment_plan``,
   ``public/example``, ``pages-demo-extra``) only appear where intentionally
   allowed — a ratchet list keeps the known legacy references visible until the
   migration that removes them also drops the entry.

Usage::

    python scripts/repo/check_documentation.py
    python scripts/repo/check_documentation.py --check-generated   # + catalog --check

Exit code 0 means all checks passed.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------

TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".json", ".yml", ".yaml",
    ".toml", ".sh", ".bash", ".md", ".css", ".html", ".txt", ".example",
    ".service", ".conf", ".svg", ".gitignore", ".cfg", ".ini", ".lock",
}

# Source / test / config extensions for the plan-section reference check.
CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".json",
                 ".yml", ".yaml", ".toml", ".sh", ".bash", ".css", ".service"}

# ---------------------------------------------------------------------------
# Required module READMEs (docs/README.md navigation contract)
# ---------------------------------------------------------------------------

REQUIRED_READMES = [
    "README.md",
    "AGENTS.md",
    "THIRD-PARTY-NOTICES.md",
    "apps/web/README.md",
    "fixtures/demo/README.md",
    "services/api/README.md",
    "services/api/app/README.md",
    "services/api/app/api/README.md",
    "services/api/app/agents/README.md",
    "services/api/app/agents/assessment/README.md",
    "services/api/app/agents/evaluation/README.md",
    "services/api/app/agents/knowledge/README.md",
    "services/api/app/agents/learning_orchestration/README.md",
    "services/api/app/agents/memory/README.md",
    "services/api/app/agents/site_assistant/README.md",
    "services/api/app/agents/skill_runtime/README.md",
    "services/api/app/agents/student_model/README.md",
    "services/api/app/agents/teaching_engine/README.md",
    "services/api/app/agents/ux_intelligence/README.md",
    "services/api/app/classroom/README.md",
    "services/api/app/core/README.md",
    "services/api/app/diagrams/README.md",
    "services/api/app/identity/README.md",
    "services/api/app/illustration/README.md",
    "services/api/app/notes/README.md",
    "services/api/app/prompts/README.md",
    "services/api/app/schemas/README.md",
    "services/api/app/tools/README.md",
    "services/api/app/voice/README.md",
    "services/api/tests/README.md",
    "services/voice/README.md",
    "scripts/README.md",
    "scripts/acceptance/README.md",
    "scripts/diagrams/README.md",
    "scripts/evaluation/README.md",
    "scripts/retrieval/README.md",
    "deploy/README.md",
]

# ---------------------------------------------------------------------------
# plan.md section references
# ---------------------------------------------------------------------------

PLAN_REF_RE = re.compile(r"plan(?:\.md)?\s*§")

# Intentional survivors of the plan-section ban.
PLAN_REF_ALLOWLIST = {
    # Prompt text inside the versioned registry; changing the string requires a
    # prompt version bump, so the historical mention stays until the next
    # deliberate text change.
    "services/api/app/prompts/registry.py",
    # This guard's own docstring names the banned pattern.
    "scripts/repo/check_documentation.py",
}

# ---------------------------------------------------------------------------
# Legacy paths
# ---------------------------------------------------------------------------

LEGACY_PATTERNS = {
    r"(?<![\w.\-])backend/": "pre-services layout path 'backend/'",
    r"(?<![\w.\-])frontend/": "pre-services layout path 'frontend/'",
    r"The_Website_deployment_plan": "private deployment plan reference",
    r"public/example": "legacy demo data path 'public/example'",
    r"pages-demo-extra": "out-of-tree demo payload reference",
}

# Files where a legacy pattern may legitimately appear (rule lists, ignore
# rules, historical ADR context). Never edited by the ratchet below.
LEGACY_PATH_EXEMPT_FILES = {
    ".gitignore",
    "scripts/repo/check_repository_hygiene.py",
    "scripts/repo/check_documentation.py",
}
LEGACY_PATH_EXEMPT_DIRS = {
    "docs/adr",  # immutable decision records describe historical layouts
}

# Ratchet: known legacy references awaiting their migration. An entry only
# forgives the *named pattern* in the *named file*. Remove the entry in the
# same change that removes the reference; a stale entry (file no longer
# matches) is itself an error so the list cannot rot.
# Legacy layout paths were fully cleaned up in phase D (clean-plan §8.3);
# the ratchet is kept empty so any reintroduced reference fails immediately.
LEGACY_PATH_RATCHET: dict[str, list[str]] = {}

# ---------------------------------------------------------------------------
# Markdown link checking
# ---------------------------------------------------------------------------

MD_LINK_RE = re.compile(r"(?<!\!)\[[^\]\n]*\]\(([^)\n]+)\)")
MD_IMAGE_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']")
SKIP_TARGET_PREFIXES = ("http://", "https://", "mailto:", "//", "#", "data:")


def repo_files() -> list[str]:
    """Tracked files plus untracked-but-not-ignored files (pre-commit state)."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
        capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def check_markdown_links(files: list[str]) -> list[str]:
    """Verify relative Markdown links against the working tree.

    A target is valid when it exists on disk and is not gitignored — the
    filesystem state a clean clone would reach after committing pending work.
    """
    problems = []
    links: list[tuple[str, Path]] = []
    for rel in files:
        if not rel.endswith(".md"):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for target in MD_LINK_RE.findall(text) + MD_IMAGE_RE.findall(text):
            target = target.strip()
            if not target or target.startswith(SKIP_TARGET_PREFIXES):
                continue
            path_part = target.split("#", 1)[0]
            if not path_part:
                continue
            links.append((rel, (ROOT / rel).parent.joinpath(path_part).resolve()))
    ignored = _ignored_paths({p for _, p in links})
    for rel, path in links:
        if not path.exists():
            problems.append(f"{rel}: broken relative link -> {path.relative_to(ROOT)}")
        elif path in ignored:
            problems.append(
                f"{rel}: link target is gitignored, breaks on clean clone -> {path.relative_to(ROOT)}"
            )
    return problems


def _ignored_paths(paths: set[Path]) -> set[Path]:
    if not paths:
        return set()
    stdin = "\0".join(str(p) for p in paths)
    # Exit code 0 = at least one path ignored, 1 = none ignored; both are fine.
    result = subprocess.run(
        ["git", "check-ignore", "--stdin", "-z"], cwd=ROOT,
        input=stdin, capture_output=True, text=True,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"git check-ignore failed: {result.stderr.strip()}")
    return {Path(p) for p in result.stdout.split("\0") if p}


def check_required_readmes() -> list[str]:
    return [f"missing required README: {rel}" for rel in REQUIRED_READMES
            if not (ROOT / rel).is_file()]


def check_plan_refs(files: list[str]) -> list[str]:
    problems = []
    for rel in files:
        if Path(rel).suffix not in CODE_SUFFIXES or rel in PLAN_REF_ALLOWLIST:
            continue
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        if PLAN_REF_RE.search(text):
            problems.append(
                f"{rel}: references local plan sections; cite docs/architecture or an ADR instead"
            )
    return problems


def check_no_plan_docs(files: list[str]) -> list[str]:
    return [f"tracked planning doc is not allowed: {rel}" for rel in files
            if rel.endswith("_PLAN.md")]


def check_legacy_paths(files: list[str]) -> list[str]:
    problems = []
    consumed: set[tuple[str, str]] = set()
    for rel in files:
        if rel in LEGACY_PATH_EXEMPT_FILES:
            continue
        if any(rel.startswith(d + "/") for d in LEGACY_PATH_EXEMPT_DIRS):
            continue
        path = ROOT / rel
        if path.suffix not in TEXT_SUFFIXES and path.name != ".gitignore":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern, label in LEGACY_PATTERNS.items():
            if not re.search(pattern, text):
                continue
            allowed = rel in LEGACY_PATH_RATCHET and pattern in LEGACY_PATH_RATCHET[rel]
            if allowed:
                consumed.add((rel, pattern))
            else:
                problems.append(f"{rel}: {label} (see docs/architecture for current layout)")
    stale = [
        f"ratchet entry is stale (reference already gone): {rel} / {pattern}"
        for rel, patterns in LEGACY_PATH_RATCHET.items()
        for pattern in patterns
        if (rel, pattern) not in consumed
    ]
    return problems + stale


def check_generated_reference() -> list[str]:
    result = subprocess.run(
        [sys.executable, "scripts/diagrams/build_catalog.py", "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        return ["generated reference drift: " + (result.stdout + result.stderr).strip()[-500:]]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-generated",
        action="store_true",
        help="additionally verify the generated diagram reference against its generator",
    )
    args = parser.parse_args()

    files = repo_files()
    problems: list[str] = []
    problems += check_markdown_links(files)
    problems += check_required_readmes()
    problems += check_plan_refs(files)
    problems += check_no_plan_docs(files)
    problems += check_legacy_paths(files)
    if args.check_generated:
        problems += check_generated_reference()

    if problems:
        print("documentation check: FAILED")
        for line in problems:
            print(f"  - {line}")
        return 1
    print("documentation check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
