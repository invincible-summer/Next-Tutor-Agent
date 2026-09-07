"""Persistence for LEARNED knowledge edges (M5.5).

The curated seed lives in code (seed.py) and is the source of truth for known
facts. Only edges DERIVED by the DependencyReasoner (provenance="reasoner") or
material-derived edges are persisted here, at project-root `knowledge/graph.json`.
The seed is never written to disk -- it regenerates from code on every start,
exactly like how student_model keeps the working JSON small and the events log
append-only.

Path-traversal guarded the same way as student_model.store / core/session
(.name strip). Every function is defensive: a corrupt/missing file is treated
as "no learned edges yet", so a bad file can never break a chat turn.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from ...core.atomic import atomic_write_text, file_lock

# knowledge/ lives at the project root (parent of backend/), sibling of
# chat_history/ and students/ -- same resolution policy.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_KG_DIR = _PROJECT_ROOT / "knowledge"
_KG_FILE = _KG_DIR / "graph.json"

# cap learned edges so an over-eager reasoner can never grow the file unbounded;
# older ones are recoverable from the reasoner's append-only log (future).
_MAX_LEARNED_EDGES = 500


def _ensure_dir() -> None:
    try:
        _KG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _path() -> Path:
    return _KG_FILE


def load_learned_edges() -> list[dict[str, Any]]:
    """Return the learned-edge list from disk ([] if missing/corrupt)."""
    try:
        if not _KG_FILE.exists():
            return []
        data = json.loads(_KG_FILE.read_text(encoding="utf-8"))
        edges = data.get("edges", []) if isinstance(data, dict) else []
        return [e for e in edges if isinstance(e, dict)]
    except Exception:
        return []


def save_learned_edges(edges: list[dict[str, Any]]) -> bool:
    """Persist the learned-edge list (overwrites). Returns False on failure.

    Trims to the most recent _MAX_LEARNED_EDGES so the file stays bounded. The
    caller (KnowledgeService) passes the FULL learned set; this is the single
    write point, so a crash mid-write only risks losing the latest addition.
    """
    try:
        _ensure_dir()
        trimmed = list(edges)[-_MAX_LEARNED_EDGES:]
        payload = {"edges": trimmed, "version": 1}
        with file_lock(_KG_FILE):
            atomic_write_text(_KG_FILE, json.dumps(payload, ensure_ascii=False, indent=2))
        return True
    except Exception:
        return False


def append_learned_edge(edge: dict[str, Any]) -> bool:
    """Append one learned edge and persist. Idempotent on (source,target,type).

    Returns True if the edge was added (or already present), False on failure.
    This is the write primitive the Reasoner calls after a validated candidate
    passes the threshold gate + DAG check.
    """
    try:
        with file_lock(_KG_FILE):
            edges = load_learned_edges()
            key = (edge.get("source"), edge.get("target"), edge.get("type"))
            for e in edges:
                if (e.get("source"), e.get("target"), e.get("type")) == key:
                    return True  # already learned
            edges.append(edge)
            return save_learned_edges(edges)
    except Exception:
        return False


def clear_learned_edges() -> bool:
    """Remove the learned-edge file (admin/debug reset). Returns True on success."""
    try:
        if _KG_FILE.exists():
            _KG_FILE.unlink()
        return True
    except Exception:
        return False


# --- M5.7 custom graphs -----------------------------------------------------
#
# Layout (all under knowledge/custom/):
#   <student>/<topic_key>.json            active graph (nodes+edges+contents)
#   <student>/archive/<topic_key>.vN.json archived versions (rollback source)
#
# Writes are ATOMIC (tmp file + os.replace) so a crash mid-write can never
# leave a half-written active graph. Reads are defensive: corrupt/missing
# files are treated as "no custom graph". Both student ids and topic keys are
# reduced to a safe basename before touching the filesystem (same path-
# traversal policy as the learned-edge store above).

_CUSTOM_DIR = _KG_DIR / "custom"
_KEY_RE = re.compile(r"^[\w.\-\u4e00-\u9fff]{1,80}$", re.UNICODE)


def _safe_name(s: str) -> str:
    """Reduce an untrusted path segment to a safe basename ("" if unsafe)."""
    name = Path(str(s or "")).name.strip()
    # reject dot-paths ("." / ".." / dot-prefixed) and anything outside the
    # conservative key alphabet (topic_key() output always passes)
    if not name or name.startswith(".") or ".." in name:
        return ""
    return name if _KEY_RE.match(name) else ""


def _student_dir(student_id: str) -> Path | None:
    name = _safe_name(student_id)
    return (_CUSTOM_DIR / name) if name else None


def _custom_path(student_id: str, topic_key: str) -> Path | None:
    sdir = _student_dir(student_id)
    key = _safe_name(topic_key)
    return (sdir / f"{key}.json") if sdir and key else None


def _archive_dir(student_id: str) -> Path | None:
    sdir = _student_dir(student_id)
    return (sdir / "archive") if sdir else None


def _write_atomic(path: Path, payload: dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def load_custom_graph(student_id: str, topic_key: str) -> dict[str, Any] | None:
    """The active graph payload for (student, topic), or None."""
    try:
        p = _custom_path(student_id, topic_key)
        if p is None or not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("nodes") else None
    except Exception:
        return None


def save_custom_graph(student_id: str, topic_key: str,
                      payload: dict[str, Any]) -> bool:
    """Atomically write the active graph. This is the single write point."""
    p = _custom_path(student_id, topic_key)
    return _write_atomic(p, payload) if p else False


# --- concept → chunks 预索引（P6-C2：按知识点划分检索域） -------------------

def _chunks_path(student_id: str, topic_key: str) -> Path | None:
    sdir = _student_dir(student_id)
    key = _safe_name(topic_key)
    return (sdir / f"{key}.chunks.json") if sdir and key else None


def save_concept_chunks(student_id: str, topic_key: str,
                        index: dict[str, Any]) -> bool:
    """Persist the concept→chunk_ids index next to the graph (atomic)."""
    p = _chunks_path(student_id, topic_key)
    return _write_atomic(p, index) if p else False


def load_concept_chunks(student_id: str, topic_key: str) -> dict[str, Any] | None:
    """Load the concept→chunk_ids index, or None."""
    try:
        p = _chunks_path(student_id, topic_key)
        if p is None or not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("concepts") else None
    except Exception:
        return None


# --- textbook per-volume normalized spec cache -----------------------------

def volume_specs_dir(student_id: str, topic_key: str) -> Path | None:
    """Directory containing complete, policy-independent specs for one textbook."""
    sdir = _student_dir(student_id)
    key = _safe_name(topic_key)
    return (sdir / f"{key}.volume_specs") if sdir and key else None


def _volume_spec_path(student_id: str, topic_key: str, file_id: str) -> Path | None:
    directory = volume_specs_dir(student_id, topic_key)
    fid = _safe_name(file_id)
    return (directory / f"{fid}.json") if directory and fid else None


def save_volume_spec(student_id: str, topic_key: str, file_id: str,
                     payload: dict[str, Any]) -> bool:
    path = _volume_spec_path(student_id, topic_key, file_id)
    return _write_atomic(path, payload) if path else False


def load_volume_spec(student_id: str, topic_key: str, file_id: str) -> dict[str, Any] | None:
    try:
        path = _volume_spec_path(student_id, topic_key, file_id)
        if path is None or not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def delete_volume_spec(student_id: str, topic_key: str, file_id: str) -> bool:
    try:
        path = _volume_spec_path(student_id, topic_key, file_id)
        if path and path.exists():
            path.unlink()
            if path.parent.exists() and not any(path.parent.iterdir()):
                path.parent.rmdir()
        return True
    except Exception:
        return False


def list_custom_graphs(student_id: str) -> list[dict[str, Any]]:
    """Active graph payloads for a student (full dicts; [] on any problem).

    Skips the `*.chunks.json` concept→chunk indexes that live in the same
    directory: they are retrieval domain data, not graph payloads, and the
    public namespace ships several MB of them (parsed then discarded here)
    on every cold merge.
    """
    out: list[dict[str, Any]] = []
    try:
        sdir = _student_dir(student_id)
        if sdir is None or not sdir.is_dir():
            return []
        for p in sorted(sdir.glob("*.json")):
            if p.stem.endswith(".chunks"):
                continue
            data = load_custom_graph(student_id, p.stem)
            if data:
                out.append(data)
    except Exception:
        pass
    return out


def list_custom_stamp(student_id: str) -> tuple[tuple[str, int], ...]:
    """((topic_key, mtime_ns), ...) — cheap cache-invalidation stamp for the
    merged per-student graph; changes on any custom write, no file parsing.

    Chunks indexes are excluded: they are rewritten by graph builds but carry
    no graph data, and counting their mtimes made every textbook rebuild
    invalidate the merged-graph cache for every user (a minutes-long rebuild
    before the index fix).
    """
    try:
        sdir = _student_dir(student_id)
        if sdir is None or not sdir.is_dir():
            return ()
        return tuple(sorted(
            (p.stem, p.stat().st_mtime_ns) for p in sdir.glob("*.json")
            if not p.stem.endswith(".chunks")))
    except Exception:
        return ()


def _archive_versions(student_id: str, topic_key: str) -> list[int]:
    adir = _archive_dir(student_id)
    key = _safe_name(topic_key)
    if adir is None or not key or not adir.is_dir():
        return []
    out: list[int] = []
    for p in adir.glob(f"{key}.v*.json"):
        try:
            out.append(int(p.name[len(key) + 2:-len(".json")]))
        except ValueError:
            continue
    return sorted(out)


def archive_custom_graph(student_id: str, topic_key: str) -> bool:
    """Copy the ACTIVE graph to archive/<key>.vN.json (N = next free)."""
    try:
        payload = load_custom_graph(student_id, topic_key)
        adir = _archive_dir(student_id)
        if payload is None or adir is None:
            return False
        versions = _archive_versions(student_id, topic_key)
        n = (versions[-1] + 1) if versions else 1
        return _write_atomic(adir / f"{_safe_name(topic_key)}.v{n}.json", payload)
    except Exception:
        return False


def archive_count(student_id: str, topic_key: str) -> int:
    return len(_archive_versions(student_id, topic_key))


def rollback_custom_graph(student_id: str, topic_key: str) -> dict[str, Any] | None:
    """Restore the newest archive as active.

    The current active is archived first (copy), then the newest archive file
    is MOVED into place (atomic rename), so rolling back twice in a row
    toggles between the two most recent versions. Returns the restored
    payload, or None when there is no archive (or no active graph).
    """
    try:
        current = load_custom_graph(student_id, topic_key)
        if current is None:
            return None
        versions = _archive_versions(student_id, topic_key)
        adir = _archive_dir(student_id)
        if not versions or adir is None:
            return None
        if not archive_custom_graph(student_id, topic_key):
            return None
        src = adir / f"{_safe_name(topic_key)}.v{versions[-1]}.json"
        dst = _custom_path(student_id, topic_key)
        if dst is None or not src.exists():
            return None
        os.replace(src, dst)
        return load_custom_graph(student_id, topic_key)
    except Exception:
        return None


def delete_custom_graph(student_id: str, topic_key: str) -> bool:
    """Archive the active graph, then remove it (and its concept-chunks index).
    Archives are kept."""
    try:
        p = _custom_path(student_id, topic_key)
        if p is None or not p.exists():
            return False
        archive_custom_graph(student_id, topic_key)
        p.unlink()
        cp = _chunks_path(student_id, topic_key)  # P6-C2 索引随图谱删除
        if cp is not None and cp.exists():
            cp.unlink()
        return True
    except Exception:
        return False


def purge_custom_graph(student_id: str, topic_key: str, *,
                       include_legacy_archives: bool = True,
                       include_volume_specs: bool = True) -> bool:
    """Remove active graph, concept index and optionally every legacy snapshot.

    Unlike :func:`delete_custom_graph`, this is the permanent-deletion primitive
    and therefore never creates another archive while deleting.
    """
    removed = False
    try:
        for path in (_custom_path(student_id, topic_key), _chunks_path(student_id, topic_key)):
            if path is not None and path.exists():
                path.unlink()
                removed = True
        specs = volume_specs_dir(student_id, topic_key)
        if include_volume_specs and specs is not None and specs.exists():
            import shutil
            shutil.rmtree(specs)
            removed = True
        if include_legacy_archives:
            adir = _archive_dir(student_id)
            key = _safe_name(topic_key)
            if adir is not None and key and adir.is_dir():
                for path in adir.glob(f"{key}.v*.json"):
                    path.unlink()
                    removed = True
                try:
                    adir.rmdir()
                except OSError:
                    pass
        return removed
    except Exception:
        return False


def cleanup_legacy_graph_archives() -> dict[str, Any]:
    """One-time removal of the pre-trash ``custom/*/archive/*.json`` layout.

    Those files only contain graph snapshots and cannot restore the textbook,
    source files, chunks, or workspace references. Keeping them would present
    a false recovery promise after the unified trash lifecycle is introduced.
    The marker makes the startup migration idempotent.
    """
    marker = _CUSTOM_DIR / ".legacy-graph-archive-cleanup-v1.json"
    removed = 0
    failed: list[str] = []
    try:
        _CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
        with file_lock(marker):
            if marker.exists():
                try:
                    existing = json.loads(marker.read_text(encoding="utf-8"))
                    if isinstance(existing, dict):
                        return existing
                except Exception:
                    pass
            for archive_dir in sorted(_CUSTOM_DIR.glob("*/archive")):
                if not archive_dir.is_dir():
                    continue
                for path in sorted(archive_dir.glob("*.json")):
                    try:
                        path.unlink()
                        removed += 1
                    except Exception:
                        failed.append(str(path.relative_to(_CUSTOM_DIR)))
                try:
                    archive_dir.rmdir()
                except OSError:
                    pass
            result = {"version": 1, "removed": removed, "failed": failed}
            atomic_write_text(marker, json.dumps(result, ensure_ascii=False, indent=2))
            return result
    except Exception as exc:
        return {"version": 1, "removed": removed,
                "failed": failed + [f"migration:{type(exc).__name__}"]}
