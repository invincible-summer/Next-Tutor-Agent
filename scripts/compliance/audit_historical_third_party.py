#!/usr/bin/env python3
"""One-shot historical third-party artifact audit (plan.md phase 0).

Scans every object reachable from any ref (``git rev-list --objects --all``)
for third-party binary / vendored-dependency artifacts that would turn a
source-only release into a conveyed binary distribution (PyMuPDF/MuPDF
binaries, Tesseract executables/language packs, wheels, shared objects,
virtualenv trees, node_modules, dependency archives). Text declarations such
as requirements pins or ``import fitz`` source lines are explicitly out of
scope: only artifacts are blocking.

Also records GitHub release assets (via ``gh`` when available) so the report
covers tag-reachable objects plus published artifacts in one place.

Writes ``licenses/history-audit-<date>.json``. Exits non-zero when any
``block``-classified object is found unless ``--allow-blocked`` records a
manual disposition; CI uses the default strict mode.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# (classification, path regex). Order matters: first match wins.
BLOCKING_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("wheel", re.compile(r"\.whl$", re.I)),
    ("shared-object", re.compile(r"\.(so|so\.[0-9.]+|dll|dylib)$", re.I)),
    ("static-library", re.compile(r"\.(a|lib)$", re.I)),
    ("executable-binary", re.compile(r"(^|/)(tesseract|tesseract\.exe)$", re.I)),
    ("tessdata", re.compile(r"\.traineddata$", re.I)),
    ("site-packages", re.compile(r"(^|/)site-packages/")),
    ("virtualenv", re.compile(r"(^|/)(\.?venv|virtualenv)/")),
    ("node_modules", re.compile(r"(^|/)node_modules/")),
    ("pymupdf-vendor", re.compile(r"(^|/)(vendor/)?(pymupdf|mupdf|fitz)/", re.I)),
    ("mobile-binary", re.compile(r"\.(apk|aab|ipa)$", re.I)),
    ("container-image", re.compile(r"\.(img|docker|oci)$", re.I)),
    ("archive-review", re.compile(r"\.(zip|tar|tar\.gz|tgz|tar\.xz|7z)$", re.I)),
]
BLOCK_CLASSES = {name for name, _ in BLOCKING_PATTERNS if name != "archive-review"}

# Text/artifact families whose *names* collide with blocking patterns but are
# known source-side declarations, not binaries. Audited by eye in the report.
REVIEW_NOTE_KEYS = ("pymupdf", "mupdf", "fitz", "tesseract", "redis", "minio")


def run_git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True
    ).stdout


def head_sha() -> str:
    return run_git("rev-parse", "HEAD").strip()


def list_tags() -> list[str]:
    return sorted(line.strip() for line in run_git("tag").splitlines() if line.strip())


def classify(path: str) -> tuple[str | None, bool]:
    for name, pattern in BLOCKING_PATTERNS:
        if pattern.search(path):
            return name, name in BLOCK_CLASSES
    return None, False


def iter_objects() -> list[tuple[str, str, str, int]]:
    """Yield (sha, path, type, size) for every object reachable from all refs."""
    listing = run_git("rev-list", "--objects", "--all", "--no-object-names")
    shas = [line.strip() for line in listing.splitlines() if line.strip()]
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        input="\n".join(shas) + "\n", capture_output=True, text=True, check=True,
    )
    objects: list[tuple[str, str, str, int]] = []
    names = {}
    raw = run_git("rev-list", "--objects", "--all")
    for line in raw.splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[1]:
            names[parts[0]] = parts[1]
    for line in proc.stdout.splitlines():
        sha, otype, size = line.split(" ")
        if otype == "blob":
            objects.append((sha, names.get(sha, ""), otype, int(size)))
    return objects


def tag_reachability(shas: list[str]) -> dict[str, list[str]]:
    """Map blob sha -> tags whose tree contains it (only computed for hits)."""
    result: dict[str, list[str]] = {sha: [] for sha in shas}
    wanted = set(shas)
    for tag in list_tags():
        out = run_git("rev-list", "--objects", tag, "--no-object-names")
        for sha in out.split():
            if sha in wanted:
                result[sha].append(tag)
    return result


def collect_release_assets() -> dict:
    try:
        remote = run_git("remote", "get-url", "origin").strip()
    except subprocess.CalledProcessError:
        return {"source": "unavailable", "releases": [], "note": "no origin remote"}
    match = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", remote)
    if not match:
        return {"source": "unavailable", "releases": [], "note": f"unparsed remote {remote}"}
    owner, repo = match.groups()
    try:
        out = subprocess.run(
            ["gh", "api", "--paginate", f"repos/{owner}/{repo}/releases"],
            capture_output=True, text=True, check=True, timeout=60,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"source": "unavailable", "releases": [], "note": f"gh api failed: {exc}"}
    releases = []
    for rel in json.loads(out or "[]"):
        releases.append({
            "tag": rel.get("tag_name"),
            "id": rel.get("id"),
            "assets": [
                {"name": a.get("name"), "size": a.get("size"), "downloads": a.get("download_count")}
                for a in rel.get("assets", [])
            ],
        })
    return {"source": "github-api", "releases": releases}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=dt.date.today().isoformat(), help="report date stamp")
    parser.add_argument("--allow-blocked", action="store_true",
                        help="record manual-disposition=required instead of failing on block hits")
    args = parser.parse_args()

    objects = iter_objects()
    hits: list[dict] = []
    review_notes: list[dict] = []
    for sha, path, otype, size in objects:
        if not path:
            continue
        klass, blocking = classify(path)
        if klass:
            hits.append({"sha": sha, "path": path, "size": size, "class": klass,
                         "blocking": blocking, "tags": [], "disposition": "manual-review-required"})
        elif any(key in path.lower() for key in REVIEW_NOTE_KEYS) and path.endswith((".py", ".txt", ".md", ".json", ".toml", ".yml", ".yaml", ".ts", ".tsx")):
            review_notes.append({"sha": sha, "path": path,
                                 "note": "text declaration only; allowed by policy"})

    blocked = [h for h in hits if h["blocking"]]
    reach = tag_reachability([h["sha"] for h in blocked]) if blocked else {}
    for hit in blocked:
        hit["tags"] = reach.get(hit["sha"], [])
        if args.allow_blocked:
            hit["disposition"] = "manual-disposition-recorded"

    assets = collect_release_assets()
    binary_asset_classes = ("mobile-binary", "container-image", "wheel", "shared-object")
    asset_hits = []
    for rel in assets.get("releases", []):
        for asset in rel.get("assets", []):
            klass, _ = classify(asset["name"])
            if klass in binary_asset_classes:
                asset_hits.append({"tag": rel["tag"], **asset, "class": klass})

    report = {
        "audit_date": args.date,
        "head_sha": head_sha(),
        "tags": list_tags(),
        "scanned_blobs": len(objects),
        "object_hits": hits,
        "blocked_object_count": len(blocked),
        "text_declaration_note_count": len(review_notes),
        "text_declaration_notes_sample": review_notes[:40],
        "release_assets": assets,
        "release_binary_asset_hits": asset_hits,
        "verdict": "clean" if not blocked and not asset_hits else "blocked-artifacts-found",
        "policy": {
            "blocking": sorted(BLOCK_CLASSES),
            "review_only": ["archive-review"],
            "allowed": "text declarations (requirements/source imports) only",
            "history_rewrite": "not required when verdict=clean (plan.md section 2)",
        },
    }
    out_path = ROOT / "licenses" / f"history-audit-{args.date}.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {out_path}")
    print(f"scanned {len(objects)} blobs; blocked={len(blocked)}; "
          f"release binary assets={len(asset_hits)}; verdict={report['verdict']}")
    for hit in blocked:
        print(f"  BLOCK {hit['class']}: {hit['path']} ({hit['size']} bytes) tags={hit['tags']}")
    for hit in asset_hits:
        print(f"  RELEASE-ASSET {hit['class']}: {hit['tag']}:{hit['name']}")
    if blocked and not args.allow_blocked:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
