"""Disaster-recovery drill for the enterprise persistence plane (ADR-0017).

CI-runnable evidence for ``docs/validation/disaster-recovery.md``. The three
subcommands compose with the operator shell commands from the runbook; the
release workflow's enterprise lane runs the whole sequence against its real
PostgreSQL + Valkey service containers::

    python scripts/ops/dr_drill.py seed --out /tmp/dr
    docker run --rm --network host postgres:18 pg_dump -h 127.0.0.1 ... > /tmp/dr/db.pgdump
    ... psql DROP DATABASE / CREATE DATABASE (runbook form) ...
    docker run --rm -i --network host postgres:18 pg_restore ... < /tmp/dr/db.pgdump
    python scripts/ops/dr_drill.py flush-cache --snapshot /tmp/dr/snapshot.json
    python scripts/ops/dr_drill.py verify --snapshot /tmp/dr/snapshot.json

``seed`` migrates a scratch database to head and writes one identity bundle
(account + credential + personal tenant + owner membership + active tenant),
two audit events, one chat and one notes domain document (ADR-0017 tables)
and two cache-tier entries, then freezes a content-addressed snapshot:
per-table row counts plus a sha256 over the canonically serialized rows.

``flush-cache`` FLUSHALLs the shared cache tier and immediately re-reads the
seeded facts from PostgreSQL — proof that the cache is disposable state.

``verify`` recomputes the snapshot and compares it byte-for-byte (count,
column values, JSONB payloads). Any drift after a drop/restore cycle exits 1.

Environment: ``TEST_DATABASE_URL`` (fallback ``DATABASE_URL``) is required;
``TEST_CACHE_URL`` (fallback ``CACHE_URL``) enables the cache phase, and the
drill degrades to a PostgreSQL-only run without it.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVICES_API = REPO / "services" / "api"

SNAPSHOT_TABLES = (
    "users", "credentials", "tenants", "memberships", "audit_events",
    "chat_documents", "notes_documents",
)


def _database_url() -> str:
    url = (os.getenv("TEST_DATABASE_URL")
           or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        sys.exit("dr_drill: TEST_DATABASE_URL (or DATABASE_URL) is required")
    return url


def _cache_url() -> str:
    return (os.getenv("TEST_CACHE_URL")
            or os.getenv("CACHE_URL") or "").strip()


def _prepare_environment() -> str:
    """Bind a scratch data root before any ``app.*`` import resolves paths."""
    os.environ.setdefault("NEXT_TUTOR_DATA_DIR",
                          tempfile.mkdtemp(prefix="dr-drill-"))
    url = _database_url()
    # Every subcommand is a fresh process: point the shared lazy engine at
    # the drill database before the persistence layer caches anything.
    os.environ["DATABASE_URL"] = url
    sys.path.insert(0, str(SERVICES_API))
    return url


def _canonical(rows: list[dict]) -> str:
    return json.dumps(sorted(rows, key=lambda r: json.dumps(r, sort_keys=True,
                                                            default=str)),
                      sort_keys=True, default=str, ensure_ascii=False)


async def _table_fingerprints(engine) -> dict:
    from sqlalchemy import text

    fingerprints: dict[str, dict] = {}
    async with engine.connect() as conn:
        for table in SNAPSHOT_TABLES:
            result = await conn.execute(text(f"SELECT * FROM {table}"))
            rows = [dict(row._mapping) for row in result]
            fingerprints[table] = {
                "count": len(rows),
                "sha256": hashlib.sha256(
                    _canonical(rows).encode("utf-8")).hexdigest(),
            }
    return fingerprints


def _migrate_to_head() -> None:
    """Sync on purpose: alembic's env.py drives its own asyncio.run, which
    cannot nest inside this script's event loop (same shape as the CI
    enterprise lane's setUpClass)."""
    from alembic.config import main as alembic_main

    cwd = os.getcwd()
    os.chdir(SERVICES_API)
    try:
        # Deterministic starting point: identical to the CI enterprise lane.
        alembic_main(["downgrade", "base"])
        alembic_main(["upgrade", "head"])
    finally:
        os.chdir(cwd)


async def _seed(out_dir: Path) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.persistence import db
    from app.persistence.documents.records import DocumentRecord
    from app.persistence.documents.repository import SqlDocumentRepository
    from app.persistence.repositories.identity import (
        SqlAlchemyIdentityRepository)
    from app.persistence.repositories.records import (AccountRecord,
                                                      AuditEventRecord,
                                                      CredentialRecord,
                                                      MembershipRecord,
                                                      TenantRecord)

    url = _database_url()
    engine = db.create_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = SqlAlchemyIdentityRepository(factory)

    stamp = time.time()
    seed = uuid.uuid4().hex[:8]
    user_id = f"usr_drill_{seed}"
    tenant_id = f"tnt_drill_{seed}"
    now = time.time()
    await repo.create_account(
        AccountRecord(user_id=user_id, email=f"{user_id}@drill.test",
                      username=user_id, role="student",
                      created_at=now, last_login_at=now),
        credential=CredentialRecord(
            id=f"crd_{uuid.uuid4().hex[:12]}", user_id=user_id,
            secret_hash="x" * 60, created_at=now, updated_at=now))
    await repo.create_tenant(TenantRecord(
        tenant_id=tenant_id, kind="personal", name=tenant_id,
        display_name="DR Drill Tenant", owner_user_id=user_id, created_at=now))
    await repo.create_membership(MembershipRecord(
        membership_id=f"mem_{uuid.uuid4().hex[:12]}",
        tenant_id=tenant_id, user_id=user_id, tenant_role="owner",
        created_at=now))
    await repo.set_active_tenant(user_id, tenant_id)
    for slot in ("seed", "post_seed"):
        await repo.record_event(AuditEventRecord(
            event_type=f"dr_drill.{slot}", actor_user_id=user_id,
            tenant_id=tenant_id, request_id=f"req_drill_{slot}",
            occurred_at=stamp))

    for domain, payload in (
            ("chat", {"title": "DR drill session",
                      "messages": [{"role": "user", "text": "drill"}]}),
            ("notes", {"name": "DR drill note", "body": "drill"})):
        documents = SqlDocumentRepository(domain, session_factory=factory)
        await documents.put(DocumentRecord(
            doc_id=f"doc_drill_{seed}", owner_id=user_id, kind="drill",
            tenant_id=tenant_id, payload=payload))

    cache_keys: list[str] = []
    cache_url = _cache_url()
    if cache_url:
        from app.persistence.cache.resp import RespCachePrimitives

        cache = RespCachePrimitives(cache_url)
        rate_key = f"dr_drill:rl:{seed}"
        kv_key = f"dr_drill:kv:{seed}"
        await cache.rate_limit(rate_key, limit=5, window_seconds=60)
        await cache.set(kv_key, b"drill", ttl_seconds=300)
        await cache.aclose()
        cache_keys = [rate_key, kv_key]

    snapshot = {
        "generated_at": stamp,
        "identity": {"user_id": user_id, "tenant_id": tenant_id},
        "tables": await _table_fingerprints(engine),
        "cache": {"keys": cache_keys},
    }
    await engine.dispose()

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "snapshot.json").write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(f"dr_drill seed: {user_id} / {tenant_id}")
    for table, fp in snapshot["tables"].items():
        print(f"  {table}: {fp['count']} rows sha256={fp['sha256'][:12]}…")
    print(f"dr_drill seed: snapshot written to {out_dir / 'snapshot.json'}")


async def _flush_cache(snapshot_path: Path) -> None:
    import redis.asyncio as aioredis

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.persistence import db
    from app.persistence.repositories.identity import (
        SqlAlchemyIdentityRepository)

    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    cache_url = _cache_url()
    if not cache_url:
        print("dr_drill flush-cache: no cache URL configured — skipped")
        return

    client = aioredis.from_url(cache_url)
    await client.flushall()
    for key in snapshot["cache"]["keys"]:
        remaining = await client.get(key)
        if remaining is not None:
            sys.exit(f"dr_drill flush-cache: key {key} survived FLUSHALL")
    await client.aclose()

    # The disposable tier is gone; the authoritative facts must not care.
    engine = db.create_engine(_database_url())
    repo = SqlAlchemyIdentityRepository(
        async_sessionmaker(engine, expire_on_commit=False))
    identity = snapshot["identity"]
    account = await repo.get_account_by_id(identity["user_id"])
    await engine.dispose()
    if account is None or account.active_tenant_id != identity["tenant_id"]:
        sys.exit("dr_drill flush-cache: facts unreadable from PostgreSQL "
                 "after FLUSHALL — cache is NOT disposable in this topology")
    print("dr_drill flush-cache: cache wiped, PostgreSQL facts intact")


async def _verify(snapshot_path: Path) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.persistence import db
    from app.persistence.repositories.identity import (
        SqlAlchemyIdentityRepository)

    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    engine = db.create_engine(_database_url())
    current = await _table_fingerprints(engine)

    repo = SqlAlchemyIdentityRepository(
        async_sessionmaker(engine, expire_on_commit=False))
    identity = snapshot["identity"]
    account = await repo.get_account_by_id(identity["user_id"])
    await engine.dispose()

    problems: list[str] = []
    if account is None:
        problems.append(f"account {identity['user_id']} missing")
    elif account.active_tenant_id != identity["tenant_id"]:
        problems.append(
            f"active_tenant_id={account.active_tenant_id!r} != "
            f"{identity['tenant_id']!r}")
    for table, expected in snapshot["tables"].items():
        actual = current.get(table)
        if actual is None:
            problems.append(f"table {table} unreadable")
        elif actual != expected:
            problems.append(
                f"table {table} drifted: {expected['count']} rows "
                f"(sha256 {expected['sha256'][:12]}…) -> "
                f"{actual['count']} rows (sha256 {actual['sha256'][:12]}…)")
    if problems:
        for problem in problems:
            print(f"dr_drill verify: {problem}", file=sys.stderr)
        sys.exit(1)
    print(f"dr_drill verify: {len(snapshot['tables'])} tables byte-identical, "
          f"identity bundle readable — no drift")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="migrate, write facts, freeze snapshot")
    seed.add_argument("--out", required=True, type=Path,
                      help="directory for snapshot.json")

    flush = sub.add_parser("flush-cache",
                           help="FLUSHALL the cache tier, prove facts survive")
    flush.add_argument("--snapshot", required=True, type=Path)

    verify = sub.add_parser("verify",
                            help="recompute and compare against the snapshot")
    verify.add_argument("--snapshot", required=True, type=Path)

    args = parser.parse_args()
    _prepare_environment()
    if args.command == "seed":
        _migrate_to_head()
        asyncio.run(_seed(args.out))
    elif args.command == "flush-cache":
        asyncio.run(_flush_cache(args.snapshot))
    else:
        asyncio.run(_verify(args.snapshot))


if __name__ == "__main__":
    main()
