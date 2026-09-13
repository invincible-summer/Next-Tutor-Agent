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
     multiple uvicorn workers).

Exits 0 when all invariants hold; prints each failure and exits 1 otherwise.
Never inspects file contents — paths and git metadata only.
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


def main() -> int:
    files = git_ls_files()
    check_gitignore_whitelist()
    check_no_lfs_capture()
    check_no_real_user_data(files)
    check_public_assets_tracked(files)
    check_single_worker_invariant()
    if FAILURES:
        print(f"\n{len(FAILURES)} repository invariant failure(s)")
        return 1
    print("\nall repository invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
