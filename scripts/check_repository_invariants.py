#!/usr/bin/env python3
"""Repository invariants for the versioned public-asset design (plan.md §22.4, §37).

This is NOT a repo-size gate: public textbooks / public graph / public vector
artifacts / the demo showcase are product content and stay versioned as plain
Git objects (no LFS, no Releases, no object storage, no history rewrite).
The checks below protect that design against accidental regressions:

  1. .gitignore keeps whitelisting the public/demo paths (exact lines);
  2. no .gitattributes rule routes those paths through filter=lfs;
  3. git ls-files carries no real-user runtime data outside the whitelist
     (students/, notes/, users/, per-account library data, non-exact
     chat/workspace files);
  4. the public asset namespaces are actually tracked;
  5. deploy/edu-backend.service pins --workers 1 (single-worker invariant,
     plan.md §36: file-backed state + process-local locks must not scale to
     multiple uvicorn workers);
  6. G6 migration end-state (plan §16.5/§16.6/§19.1): retired legacy
     student-evaluation input files are untracked; the demo account's
     learning-evidence journal IS tracked; tracked demo files and active
     source carry no old numeric-ability identifiers.

Checks 6 reads file CONTENT only for the deliberately-versioned demo
showcase and for active source trees — never for private runtime data.

Exits 0 when all invariants hold; prints each failure and exits 1 otherwise.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# .gitignore must keep these exact negation lines (public + demo namespaces).
REQUIRED_GITIGNORE_LINES = [
    "!chat_history/library/public.json",
    "!chat_history/library/public.textbooks.json",
    "!chat_history/library/data/public/",
    "!chat_history/library/data/public/**",
    "!/knowledge/custom/public/",
    "!/knowledge/custom/public/**",
    "!/knowledge/public_vector_artifacts/",
    "!/knowledge/public_vector_artifacts/**",
]

DEMO_ACCOUNT = "usr_12e410b4e2"

# LFS must never capture these path prefixes (plain Git objects only).
PLAIN_GIT_PREFIXES = (
    "chat_history/library/public",
    "chat_history/library/data/public/",
    "knowledge/custom/public/",
    "knowledge/public_vector_artifacts/",
    f"notes/{DEMO_ACCOUNT}/",
    f"students/{DEMO_ACCOUNT}",
    f"chat_history/library/{DEMO_ACCOUNT}",
    f"chat_history/library/data/{DEMO_ACCOUNT}/",
)

# Public asset namespaces that must actually be tracked (product content,
# not optional extras). Each pattern must match at least one tracked file.
TRACKED_PUBLIC_PATTERNS = [
    "chat_history/library/public.json",
    "chat_history/library/public.textbooks.json",
    "chat_history/library/data/public/*",
    "knowledge/custom/public/*",
]

# Namespaces that are *allowed and required to stay plain-Git when present*
# (optional derived artifacts — empty today is legitimate, whitelist only).
TRACKED_IF_PRESENT_PATTERNS = [
    "knowledge/public_vector_artifacts/*",
]

# 旧学生评价输入文件（plan §16.3/§16.6）：迁移后不允许再被跟踪。
RETIRED_STUDENT_SUFFIXES = (
    ".learning_records.json", ".quiz_recent.json", ".events.jsonl",
    ".assessment.json",
)

# G6 迁移后 demo 账号必须跟踪的新事实源（§16.5 新文件白名单）。
REQUIRED_DEMO_FILES = [
    f"students/{DEMO_ACCOUNT}.learning_evidence.jsonl",
]

# 受控 demo 内容里不允许再出现的旧数值能力标识（§19.1 受控 demo 扫描）。
DEMO_BANNED_SUBSTRINGS = (
    "p_known", "mastered_ratio", "before_mastery", "after_mastery",
    "learning_gain", "avg_gain", '"weak_points"', '"strong_points"',
    '"mastery"',
)
# prompt_memory 的旧水平字段名（文件级：goal_states 的 current_level 是
# 新 GoalAnalysisLevel 枚举，语义不同，不在禁列）。
PROMPT_MEMORY_BANNED_SUBSTRINGS = ('"current_level"',)

# active source 里的旧算法标识（§19.1；大小写不敏感，含 LearningGain 变体）。
ACTIVE_SOURCE_BANNED = (
    "p_known", "masterytracker", "conceptstate", "record_quiz_result",
    "rebuild_mastery", "derive_concept_status", "mastery_map",
    "current_mastery", "target_mastery", "planned_mastery", "mastered_ratio",
    "before_mastery", "after_mastery", "avg_learning_gain", "learning_gain",
    "learninggain", "seed_from_mastery", "knowledgenodemastery",
    "masteryresp", "masterycolor", "allow_mastery_update",
)
ACTIVE_SOURCE_DIRS = ("backend/app", "frontend/src")
ACTIVE_SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx")

FAILURES: list[str] = []


def fail(message: str) -> None:
    FAILURES.append(message)
    print(f"FAIL: {message}")


def ok(message: str) -> None:
    print(f"ok: {message}")


def git_ls_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                         text=True, check=True)
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def fnmatch_any(pattern: str, paths: list[str]) -> bool:
    import fnmatch
    return any(fnmatch.fnmatch(p, pattern) for p in paths)


def check_gitignore_whitelist() -> None:
    text = (REPO / ".gitignore").read_text(encoding="utf-8")
    for line in REQUIRED_GITIGNORE_LINES:
        if line not in text:
            fail(f".gitignore lost public/demo whitelist line: {line}")
    # demo showcase whitelist must stay explicit per-account, never a
    # blanket notes/students allowance.
    for blanket in ("!notes/", "!students/", "!users/", "!chat_history/"):
        if re.search(rf"^{re.escape(blanket)}\s*$", text, re.M):
            fail(f".gitignore must not blanket-whitelist {blanket}")
    if f"!/{DEMO_ACCOUNT}" in text or f"!notes/*" in text.replace("!notes/", "!notes/"):
        pass  # informational only; blanket checks above are the real gate
    ok(".gitignore keeps public/demo whitelists explicit")


def check_no_lfs_capture() -> None:
    attrs = REPO / ".gitattributes"
    if not attrs.exists():
        ok("no .gitattributes present (nothing can be LFS-captured)")
        return
    for raw in attrs.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "filter=lfs" not in line:
            continue
        pattern = line.split()[0].lstrip("/")
        for prefix in PLAIN_GIT_PREFIXES:
            if pattern in prefix or prefix.startswith(pattern) or (
                    pattern.endswith("*") and prefix.startswith(pattern[:-1])):
                fail(f".gitattributes routes plain-Git path under LFS: {line}")
    ok(".gitattributes does not LFS-capture public/demo paths")


def check_no_real_user_data(files: list[str]) -> None:
    for path in files:
        if path.startswith("users/"):
            fail(f"tracked real-user identity data: {path}")
        if path.startswith("students/") and not path.startswith(
                f"students/{DEMO_ACCOUNT}"):
            fail(f"tracked non-demo student state: {path}")
        if path.startswith("notes/") and not path.startswith(
                f"notes/{DEMO_ACCOUNT}/"):
            fail(f"tracked non-demo notes vault data: {path}")
        if path.startswith("chat_history/library/data/"):
            rest = path[len("chat_history/library/data/"):]
            if not (rest.startswith("public/") or rest.startswith(DEMO_ACCOUNT)):
                fail(f"tracked non-public library data: {path}")
        if path.startswith("chat_history/library/") and path.endswith(".json") \
                and not path.startswith("chat_history/library/data/"):
            name = path.rsplit("/", 1)[-1]
            if not (name.startswith("public") or name.startswith(DEMO_ACCOUNT)):
                fail(f"tracked non-public/non-demo library index: {path}")
        # chat transcripts / workspaces: filenames carry no account id, so
        # only the exact .gitignore negations are allowed.
        if re.match(r"^chat_history/(chat_|workspaces/ws_)", path):
            if path not in _exact_chat_whitelist():
                fail(f"tracked chat/workspace outside exact demo whitelist: {path}")
    ok("no real-user runtime data tracked outside the whitelist")


def _exact_chat_whitelist() -> set[str]:
    text = (REPO / ".gitignore").read_text(encoding="utf-8")
    return {line[1:].strip() for line in text.splitlines()
            if line.startswith("!chat_history/")}


def check_public_assets_tracked(files: list[str]) -> None:
    for pattern in TRACKED_PUBLIC_PATTERNS:
        if not fnmatch_any(pattern, files):
            fail(f"public asset namespace not tracked anymore: {pattern}")
    import fnmatch as _fm
    for pattern in TRACKED_IF_PRESENT_PATTERNS:
        prefix = pattern.split("*")[0]
        present = any(p.startswith(prefix) for p in files) or (REPO / prefix).exists()
        if present and not any(_fm.fnmatch(p, pattern) or p.startswith(prefix)
                               for p in files):
            fail(f"namespace exists on disk but nothing tracked: {pattern}")
    ok("public textbook/graph/vector namespaces remain tracked")


def check_single_worker_invariant() -> None:
    service = REPO / "deploy" / "edu-backend.service"
    if not service.exists():
        fail("deploy/edu-backend.service missing")
        return
    text = service.read_text(encoding="utf-8")
    if "--workers 1" not in text:
        fail("deploy/edu-backend.service must keep --workers 1 "
             "(file-backed persistence + process-local locks)")
    else:
        ok("deploy/edu-backend.service pins --workers 1")


def check_migration_end_state(files: list[str]) -> None:
    """G6 迁移终态（plan §16.5/§16.6）：旧评价输入文件不再被跟踪，
    demo 账号的新 journal 必须被跟踪。"""
    for path in files:
        if path.startswith("students/"):
            for suffix in RETIRED_STUDENT_SUFFIXES:
                if path.endswith(suffix):
                    fail(f"retired legacy evaluation file still tracked: {path}")
    for required in REQUIRED_DEMO_FILES:
        if required not in files:
            fail(f"demo migration output not tracked anymore: {required}")
    ok("no retired legacy evaluation files tracked; demo journal tracked")


def _demo_content_files(files: list[str]) -> list[str]:
    return [p for p in files
            if p.startswith(f"students/{DEMO_ACCOUNT}")
            or p.startswith(f"notes/{DEMO_ACCOUNT}/")
            or re.match(r"^chat_history/(chat_|workspaces/ws_)", p)]


def check_demo_content_clean(files: list[str]) -> None:
    """§19.1 受控 demo 扫描：版本化 demo 文件里不得残留旧数值能力字段。
    只读刻意版本化的 demo 展示文件，绝不读私有运行时数据。"""
    checked = 0
    for rel in _demo_content_files(files):
        path = REPO / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        checked += 1
        for needle in DEMO_BANNED_SUBSTRINGS:
            if needle in text:
                fail(f"demo file carries retired ability field "
                     f"{needle!r}: {rel}")
        if rel.endswith(".prompt_memory.json"):
            for needle in PROMPT_MEMORY_BANNED_SUBSTRINGS:
                if needle in text:
                    fail(f"prompt_memory keeps retired level field "
                         f"{needle!r}: {rel}")
    ok(f"controlled demo content clean over {checked} tracked file(s)")


def check_active_source_identifiers() -> None:
    """§19.1 静态扫描常驻化：active source 中旧算法标识必须归零
    （大小写不敏感，覆盖 LearningGain 一类驼峰变体）。"""
    hits = 0
    for root in ACTIVE_SOURCE_DIRS:
        base = REPO / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if (not path.is_file()
                    or path.suffix not in ACTIVE_SOURCE_SUFFIXES
                    or "__pycache__" in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            for needle in ACTIVE_SOURCE_BANNED:
                if needle in text:
                    hits += 1
                    fail(f"active source carries retired identifier "
                         f"{needle!r}: {path.relative_to(REPO)}")
                    break
    if not hits:
        ok("no retired ability identifiers in active source")


def main() -> int:
    files = git_ls_files()
    check_gitignore_whitelist()
    check_no_lfs_capture()
    check_no_real_user_data(files)
    check_public_assets_tracked(files)
    check_single_worker_invariant()
    check_migration_end_state(files)
    check_demo_content_clean(files)
    check_active_source_identifiers()
    if FAILURES:
        print(f"\n{len(FAILURES)} repository invariant failure(s)")
        return 1
    print("\nall repository invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
