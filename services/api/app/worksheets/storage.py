"""Owner-isolated worksheet documents and attachments."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from app.core.atomic import atomic_write_bytes, atomic_write_text, file_lock
from app.core.paths import bind_storage_path

_ROOT = bind_storage_path(__name__, "_ROOT", "worksheets")


def safe(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}", value) or ".." in value:
        raise ValueError("invalid_worksheet_key")
    return value


def owner_dir(owner: str) -> Path:
    return _ROOT / safe(owner)


def document_path(owner: str, worksheet_id: str) -> Path:
    return owner_dir(owner) / "documents" / f"{safe(worksheet_id)}.json"


def read(owner: str, worksheet_id: str) -> dict | None:
    try:
        return json.loads(document_path(owner, worksheet_id).read_text("utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return None


def write(owner: str, worksheet_id: str, document: dict) -> None:
    path = document_path(owner, worksheet_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(document, ensure_ascii=False, sort_keys=True, allow_nan=False))


def list_documents(owner: str) -> list[dict]:
    root = owner_dir(owner) / "documents"
    if not root.is_dir():
        return []
    rows: list[dict] = []
    for path in root.glob("ws_*.json"):
        try:
            value = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and not value.get("deleted"):
            rows.append(value)
    rows.sort(key=lambda row: float(row.get("updated_at", 0)), reverse=True)
    return rows


def delete(owner: str, worksheet_id: str) -> None:
    root = owner_dir(owner)
    with file_lock(root):
        document_path(owner, worksheet_id).unlink(missing_ok=True)
        shutil.rmtree(root / "assets" / safe(worksheet_id), ignore_errors=True)


def save_asset(owner: str, worksheet_id: str, asset_id: str, data: bytes, suffix: str = "png") -> Path:
    path = owner_dir(owner) / "assets" / safe(worksheet_id) / f"{safe(asset_id)}.{safe(suffix)}"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, data)
    return path


def asset_path(owner: str, worksheet_id: str, asset_id: str) -> Path:
    base = owner_dir(owner) / "assets" / safe(worksheet_id)
    for suffix in ("png", "jpg", "jpeg", "webp"):
        candidate = base / f"{safe(asset_id)}.{suffix}"
        if candidate.is_file():
            return candidate
    return base / f"{safe(asset_id)}.png"


def remove_asset(owner: str, worksheet_id: str, asset_id: str) -> None:
    """Remove every supported representation of one worksheet attachment."""
    base = owner_dir(owner) / "assets" / safe(worksheet_id)
    key = safe(asset_id)
    for suffix in ("png", "jpg", "jpeg", "webp"):
        (base / f"{key}.{suffix}").unlink(missing_ok=True)
