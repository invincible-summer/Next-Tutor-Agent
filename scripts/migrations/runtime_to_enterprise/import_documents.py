#!/usr/bin/env python3
"""Idempotent domain-document import: file stores → <domain>_documents.

Per-domain walkers enumerate file-mode facts (never mutating them) and
insert missing SQL rows through the same SqlDocumentRepository the API
uses. Existing rows are never overwritten: re-runs converge, and SQL-side
writes made after the import win (that is the cutover contract — the file
side freezes at cutover).

Domains land here one by one (chat first); ``--domain`` selects which
walker runs. ``--verify`` then re-reads both sides and compares counts +
payload digests, exiting 1 on any drift.

State (counts, source digest, timestamp) is recorded in the shared
migration state file; the file runtime stays read-only.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common  # noqa: E402


def _digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False,
                                     sort_keys=True).encode()).hexdigest()


# -- walkers ----------------------------------------------------------------


def _iter_chat_documents() -> list[tuple[str, str, str, dict[str, Any]]]:
    """(owner, kind, doc_id, payload) for every file-mode chat session and
    transcript. Ownership: the payload's student_id (legacy fallback
    "student_default" mirrors the API's visibility rule)."""
    from app.agents.student_model.store import DEFAULT_STUDENT_ID
    from app.core import paths

    sessions_dir = paths.runtime_paths().sessions
    out: list[tuple[str, str, str, dict[str, Any]]] = []
    if not sessions_dir.is_dir():
        return out
    transcripts: dict[str, list[dict[str, Any]]] = {}
    for p in sorted(sessions_dir.glob("*.json")):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        sid = str(payload.get("session_id") or p.stem)
        owner = str(payload.get("student_id") or DEFAULT_STUDENT_ID)
        out.append((owner, "session", sid, payload))
        for tid in payload.get("trace_ids") or []:
            out.append((owner, "trace_ref", str(tid),
                        {"session_id": sid}))
        tpath = p.with_name(f"{p.stem}.transcript.jsonl")
        if tpath.is_file():
            lines = []
            for line in tpath.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        lines.append(json.loads(line))
                    except Exception:
                        continue
            if lines:
                transcripts[sid] = lines
    for sid, lines in transcripts.items():
        owner = next((o for o, k, d, _ in out
                      if k == "session" and d == sid), DEFAULT_STUDENT_ID)
        out.append((owner, "transcript", sid, {"lines": lines}))
    return out


WALKERS = {
    "chat": _iter_chat_documents,
}


def _iter_notes_documents() -> list[tuple[str, str, str, dict[str, Any]]]:
    """(owner, kind, doc_id, payload) for every file-mode notes vault.

    Owner = vault directory name (the student key). Note contents, per-note
    revision lists and agent states aggregate into their own documents,
    mirroring the runtime layout.
    """
    import re as _re

    from app.core import paths

    notes_root = paths.runtime_paths().notes
    out: list[tuple[str, str, str, dict[str, Any]]] = []
    if not notes_root.is_dir():
        return out
    for vault_dir in sorted(p for p in notes_root.iterdir() if p.is_dir()):
        owner = vault_dir.name
        index = vault_dir / "vault.json"
        if index.is_file():
            try:
                payload = json.loads(index.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    out.append((owner, "vault", "index", payload))
            except Exception:
                pass
        notes_dir = vault_dir / "notes"
        if notes_dir.is_dir():
            for p in sorted(notes_dir.glob("*.md")):
                out.append((owner, "note", p.stem,
                            {"content": p.read_text(encoding="utf-8",
                                                    errors="ignore")}))
        revisions_root = vault_dir / "revisions"
        if revisions_root.is_dir():
            for note_dir in sorted(p for p in revisions_root.iterdir()
                                   if p.is_dir()):
                items: list[dict[str, Any]] = []
                for p in sorted(note_dir.glob("*.md")):
                    m = _re.match(r"^(\d+)_(\d+)_([^\.]+)\.md$", p.name)
                    if not m:
                        continue
                    items.append({
                        "revision": int(m.group(1)),
                        "ts": float(m.group(2)),
                        "author": m.group(3),
                        "content": p.read_text(encoding="utf-8",
                                               errors="ignore"),
                    })
                if items:
                    items.sort(key=lambda r: r["revision"])
                    out.append((owner, "revisions", note_dir.name,
                                {"items": items}))
        agent_dir = vault_dir / "agent"
        if agent_dir.is_dir():
            for p in sorted(agent_dir.glob("*.json")):
                try:
                    payload = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if isinstance(payload, dict):
                    out.append((owner, "agent", p.stem, payload))
    return out


WALKERS["notes"] = _iter_notes_documents


def _iter_evidence_documents() -> list[tuple[str, str, str, dict[str, Any]]]:
    """(owner, kind, doc_id, payload) for learner journals + profiles.

    Journal lines import verbatim (checksums/generations survive); the
    profile is the students/<key>.json blob.
    """
    from app.agents.student_model.evaluation.store import JOURNAL_SUFFIX
    from app.core import paths

    students = paths.runtime_paths().students
    out: list[tuple[str, str, str, dict[str, Any]]] = []
    if not students.is_dir():
        return out
    for p in sorted(students.iterdir()):
        if not p.is_file():
            continue
        owner = p.name.split(".")[0]
        if p.name.endswith(JOURNAL_SUFFIX):
            lines = [line for line in
                     p.read_text(encoding="utf-8").splitlines() if line.strip()]
            if lines:
                out.append((owner, "journal", owner, {"lines": lines}))
        elif p.name == f"{owner}.json":
            try:
                payload = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                out.append((owner, "profile", owner, payload))
    return out


WALKERS["evidence"] = _iter_evidence_documents


def _iter_orchestration_documents() -> list[tuple[str, str, str, dict[str, Any]]]:
    """(owner, kind, doc_id, payload) for orchestration state + event logs.

    State imports verbatim; event JSONL imports as the whole line list
    (parsed to dicts, order preserved oldest-first like the file; a line
    that fails to parse stays a raw string so invalid-count semantics
    survive the cutover).
    """
    from app.core import paths

    students = paths.runtime_paths().students
    out: list[tuple[str, str, str, dict[str, Any]]] = []
    if not students.is_dir():
        return out
    for p in sorted(students.iterdir()):
        if not p.is_file():
            continue
        if p.name.endswith(".orchestration.json"):
            try:
                payload = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                out.append((p.name[: -len(".orchestration.json")],
                            "state", "state", payload))
        elif p.name.endswith(".orchestration_events.jsonl"):
            lines: list[Any] = []
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    lines.append(json.loads(line))
                except ValueError:
                    lines.append(line)
            if lines:
                out.append((p.name[: -len(".orchestration_events.jsonl")],
                            "events", "events", {"lines": lines}))
    return out


WALKERS["orchestration"] = _iter_orchestration_documents


async def _import_domain(domain: str, dry_run: bool) -> dict[str, Any]:
    from app.persistence.documents import DocumentRecord
    from app.persistence.documents.repository import SqlDocumentRepository

    _common.require_enterprise_database()
    walker = WALKERS[domain]
    items = walker()
    repo = SqlDocumentRepository(domain)
    created = skipped = 0
    conflicts: list[dict[str, Any]] = []
    for owner, kind, doc_id, payload in items:
        if not doc_id:
            conflicts.append({"kind": kind, "reason": "no_doc_id"})
            continue
        existing = await repo.get(owner, doc_id, kind=kind)
        if existing is not None:
            skipped += 1
            continue
        if not dry_run:
            await repo.put(DocumentRecord(doc_id=doc_id, owner_id=owner,
                                          kind=kind, payload=payload))
        created += 1
    return {
        "domain": domain,
        "created": created,
        "skipped_existing": skipped,
        "conflicts": conflicts,
        "source_digest": hashlib.sha256(
            "\n".join(f"{o}|{k}|{d}|{_digest(p)}"
                      for o, k, d, p in items).encode()).hexdigest(),
        "dry_run": dry_run,
    }


async def _verify_domain(domain: str) -> dict[str, Any]:
    from app.persistence.documents.repository import SqlDocumentRepository

    _common.require_enterprise_database()
    items = WALKERS[domain]()
    repo = SqlDocumentRepository(domain)
    missing: list[dict[str, Any]] = []
    mismatched: list[dict[str, Any]] = []
    checked = 0
    for owner, kind, doc_id, payload in items:
        row = await repo.get(owner, doc_id, kind=kind)
        if row is None:
            missing.append({"owner": owner, "kind": kind, "doc_id": doc_id})
            continue
        checked += 1
        if _digest(row.payload or {}) != _digest(payload):
            mismatched.append({"owner": owner, "kind": kind,
                               "doc_id": doc_id})
    return {"domain": domain, "checked": checked, "missing": missing,
            "mismatched": mismatched,
            "ok": not missing and not mismatched}


async def _main_async(args: argparse.Namespace) -> int:
    results: dict[str, Any] = {}
    exit_code = 0
    for domain in args.domains:
        if args.verify:
            result = await _verify_domain(domain)
            results[f"verify:{domain}"] = result
            if not result["ok"]:
                exit_code = 1
        else:
            results[f"import:{domain}"] = await _import_domain(
                domain, args.dry_run)
    if not args.dry_run:
        state = _common.load_state()
        state.setdefault("imports", []).append({
            "ts": time.time(),
            "kind": "domain_documents",
            **results,
        })
        _common.save_state(state)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", dest="domains", action="append",
                        required=True, choices=sorted(WALKERS),
                        help="domain walker(s) to run (repeatable)")
    parser.add_argument("--dry-run", action="store_true",
                        help="count what would be imported, write nothing")
    parser.add_argument("--verify", action="store_true",
                        help="compare file side against SQL rows, exit 1 on drift")
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
