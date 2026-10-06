"""Per-domain storage routing: SQL documents vs the legacy file stores.

Each domain cutover (ADR-0017) keeps both implementations alive; which one
serves traffic is configuration, so an importer can run, shadow reads can
be compared and a bad rollout can fall back per domain.

``DOMAIN_DOCUMENT_BACKENDS`` (empty by default):

- ``""``            — every not-yet-cut-over domain stays on file storage
- ``all``           — every domain serves from SQL (needs DATABASE_URL)
- ``none``          — every domain on file storage, even in enterprise mode
- ``chat=file,notes=sql`` — per-domain overrides; ``default=sql`` sets the
  fallback for domains not listed

``sql_enabled(domain)`` additionally requires ``DATABASE_URL``: without the
enterprise database nothing routes to SQL, whatever the flag says.
"""
from __future__ import annotations

import os

from ..db import enterprise_mode

#: Default since the 9-domain cutover completed (ADR-0017): enterprise
#: deployments (DATABASE_URL set) serve facts from SQL unless explicitly
#: overridden per domain. File-only deployments never see SQL —
#: ``sql_enabled`` still requires the enterprise database.
_DEFAULT_BACKEND = "sql"


def _parse(raw: str) -> tuple[dict[str, str], str | None]:
    overrides: dict[str, str] = {}
    default: str | None = None
    for chunk in raw.split(","):
        chunk = chunk.strip().lower()
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        key = key.strip()
        value = (value.strip() or "sql") if value else "sql"
        if value not in ("sql", "file"):
            continue  # unknown backend names never widen storage routing
        if key == "default":
            default = value
        else:
            overrides[key] = value
    return overrides, default


def backend_for(domain: str) -> str:
    """Configured backend for one domain ("sql" or "file")."""
    from ..models.documents import DOCUMENT_DOMAINS

    if domain not in DOCUMENT_DOMAINS:
        raise ValueError(f"unknown document domain {domain!r}")
    raw = (os.getenv("DOMAIN_DOCUMENT_BACKENDS") or "").strip().lower()
    if raw in ("", "default"):
        return _DEFAULT_BACKEND
    if raw == "all":
        return "sql"
    if raw == "none":
        return "file"
    overrides, default = _parse(raw)
    if domain in overrides:
        return overrides[domain]
    return default or _DEFAULT_BACKEND


def sql_enabled(domain: str) -> bool:
    """True when the domain must serve facts from the document tables."""
    return enterprise_mode() and backend_for(domain) == "sql"
