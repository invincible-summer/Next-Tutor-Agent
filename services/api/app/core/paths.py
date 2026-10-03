"""Single ownership of all runtime storage paths.

All persistent user/runtime data lives under one root, default
``<repo>/.runtime/data`` and overridable with ``NEXT_TUTOR_DATA_DIR`` (or
programmatically via ``set_runtime_root``). Storage modules bind their
module-level path constants through ``bind_storage_path`` so tests and
embedders can retarget every root at once; the repository itself never
tracks any of these directories — the public textbook namespace is a
deployment-local runtime feature that starts empty on a fresh clone.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ENV_DATA_DIR = "NEXT_TUTOR_DATA_DIR"
_REPO_ROOT = Path(__file__).resolve().parents[4]


class RuntimePaths:
    """Resolved runtime directory layout (data plane only; never code assets)."""

    __slots__ = (
        "root", "users", "sessions", "transcripts", "library", "library_data",
        "workspaces", "classroom", "assistant", "trash", "policies",
        "knowledge", "knowledge_custom", "vector_db", "notes", "students",
        "traces", "uploads", "artifacts", "public_vectors", "auth_secret", "illustrations", "diagram_assets",
    )

    def __init__(self, root: Path) -> None:
        root = Path(root)
        self.root = root
        self.users = root / "users"
        # Sessions and transcript sidecars share the chat_history root, as in
        # the legacy repo layout the storage modules were written against.
        self.sessions = root / "chat_history"
        self.transcripts = root / "chat_history"
        self.library = root / "chat_history" / "library"
        self.library_data = root / "chat_history" / "library" / "data"
        self.workspaces = root / "chat_history" / "workspaces"
        self.classroom = root / "chat_history" / "classroom"
        self.assistant = root / "chat_history" / "assistant"
        self.illustrations = root / "illustrations"
        self.diagram_assets = root / "diagram_assets"
        self.trash = root / "chat_history" / "trash"
        self.policies = root / "chat_history" / "settings"
        self.knowledge = root / "knowledge"
        self.knowledge_custom = root / "knowledge" / "custom"
        self.vector_db = root / "knowledge" / "vector_db"
        self.notes = root / "notes"
        self.students = root / "students"
        self.traces = root / "traces"
        self.uploads = root / "uploads"
        self.artifacts = root / "artifacts"
        self.public_vectors = root / "artifacts" / "public_vectors"
        # Instance-level generated JWT secret sits beside the data root (the
        # default layout keeps it at <repo>/.runtime/auth_jwt_secret).
        self.auth_secret = root.parent / "auth_jwt_secret"

    def resolve_path(self, kind: str, extra: str | None = None) -> Path:
        base = getattr(self, kind)
        return base / extra if extra else base


_runtime: RuntimePaths | None = None
_runtime_key: str | None = None
_override_root: Path | None = None
# (owner, attr, kind, extra, as_str). ``owner`` is a module name, or
# "module:attr" when the bound attribute lives on a singleton object such as
# config.settings.
_bindings: list[tuple[str, str, str, str | None, bool]] = []


def repo_root() -> Path:
    """Repository root (code/config assets only; never write runtime data here)."""
    return _REPO_ROOT


def default_data_root() -> Path:
    if _override_root is not None:
        return _override_root
    env = os.getenv(_ENV_DATA_DIR, "").strip()
    if env:
        return Path(env).expanduser().absolute()
    return _REPO_ROOT / ".runtime" / "data"


def runtime_paths() -> RuntimePaths:
    global _runtime, _runtime_key
    key = str(default_data_root())
    if _runtime is None or _runtime_key != key:
        _runtime = RuntimePaths(Path(key))
        _runtime_key = key
    return _runtime


def reset_runtime_paths() -> None:
    """Drop the cached layout; next ``runtime_paths()`` re-resolves the root."""
    global _runtime, _runtime_key
    _runtime = None
    _runtime_key = None


def current_override_root() -> Path | None:
    """The programmatic override currently in effect, if any."""
    return _override_root


def set_runtime_root(root: Path | None) -> RuntimePaths:
    """Programmatically override the runtime root (tests, demo exporter).

    Re-applies every registered binding. Passing ``None`` restores
    env/default resolution (call ``reset_runtime_paths`` semantics included).
    """
    global _override_root, _runtime, _runtime_key
    _override_root = Path(root).expanduser().absolute() if root is not None else None
    _runtime = None
    _runtime_key = None
    paths = runtime_paths()
    _rebind_all()
    return paths


def _resolve_owner(owner: str):
    if ":" in owner:
        mod_name, _, obj_attr = owner.partition(":")
        return getattr(sys.modules[mod_name], obj_attr)
    return sys.modules[owner]


def _apply_binding(owner: str, attr: str, kind: str, extra: str | None, as_str: bool) -> Path:
    value = runtime_paths().resolve_path(kind, extra)
    target = _resolve_owner(owner)
    setattr(target, attr, str(value) if as_str else value)
    return value


def bind_storage_path(owner: str, attr: str, kind: str, extra: str | None = None,
                       *, as_str: bool = False) -> Path:
    """Bind ``owner.attr`` to a runtime path and register it for bulk retarget.

    Call at module import time so the constant starts correct::

        _LIBRARY_DIR = bind_storage_path(__name__, "_LIBRARY_DIR", "library")
        POLICY_FILE = bind_storage_path(__name__, "POLICY_FILE", "policies", "x.json")

    ``owner`` may be "module:obj_attr" to bind an attribute on a singleton
    object (e.g. ``"app.core.config:settings"``); ``as_str`` keeps the bound
    value a ``str`` for dataclass fields typed as ``str``.
    """
    _bindings.append((owner, attr, kind, extra, as_str))
    return _apply_binding(owner, attr, kind, extra, as_str)


def _rebind_all() -> None:
    for owner, attr, kind, extra, as_str in _bindings:
        _apply_binding(owner, attr, kind, extra, as_str)
