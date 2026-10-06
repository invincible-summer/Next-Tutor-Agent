"""Enterprise persistence layer (PostgreSQL / object store / RESP cache).

Activated by environment configuration only:

- ``DATABASE_URL`` set  -> enterprise mode: async SQLAlchemy engine, typed
  repositories over the identity schema, rotating auth sessions, audit events.
- ``DATABASE_URL`` unset -> file mode: this package stays inert (no engine is
  created, no connection attempted) and every existing file-backed code path
  behaves exactly as before.

Domain services depend on the repository protocols and the plain-data records
in ``repositories.records`` — never on SQLAlchemy models (models live in
``models/`` and are mapped to records inside ``repositories/`` only).
"""
from __future__ import annotations
