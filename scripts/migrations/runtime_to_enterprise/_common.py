"""Shared helpers for the runtime → enterprise migration toolkit.

Rules that hold across every subcommand:

- The file runtime is read-only for this toolkit: importing never mutates or
  deletes file data (cutover keeps a rollback window instead).
- Imports are idempotent and deterministic: derived ids (tenant/membership/
  credential) hash from the user_id, so re-running after a partial failure
  converges instead of duplicating rows.
- State (source hash, counts, phase timestamps) lives in
  ``<data root>/migrations/runtime_to_enterprise/state.json`` — runtime data,
  never committed, resolved through core.paths like every other runtime path.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "services" / "api"))

STATE_RELATIVE = Path("migrations") / "runtime_to_enterprise" / "state.json"


def data_root() -> Path:
    from app.core import paths

    return paths.runtime_paths().root


def state_path() -> Path:
    return data_root() / STATE_RELATIVE


def accounts_file() -> Path:
    from app.identity import store

    return Path(store._ACCOUNTS_FILE)


def load_state() -> dict[str, Any]:
    path = state_path()
    if not path.is_file():
        return {"imports": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"imports": []}


def save_state(state: dict[str, Any]) -> None:
    import os

    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".state.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_file_accounts() -> dict[str, Any]:
    """Read-only snapshot of users/accounts.json (raw dict, never written)."""
    path = accounts_file()
    if not path.is_file():
        return {"users": {}, "by_id": {}, "_missing": True}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {"users": {}, "by_id": {}}


def stable_id(prefix: str, seed: str) -> str:
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def require_enterprise_database() -> None:
    from app.persistence import db

    if not db.enterprise_mode():
        raise SystemExit(
            "DATABASE_URL is not configured: the enterprise target is "
            "required for this subcommand (scan/report work file-only).")


def repository():
    from app.persistence.repositories.identity import SqlAlchemyIdentityRepository

    return SqlAlchemyIdentityRepository()


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()
