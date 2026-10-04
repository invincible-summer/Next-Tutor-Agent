"""Alembic environment for the enterprise persistence schema.

Async-first: the engine is created from DATABASE_URL (or
EDU_MIGRATION_DATABASE_URL) through the same normalization as
app.persistence.db, so ``postgresql://`` is upgraded to asyncpg transparently.
Autogenerate diffs against app.persistence.models.Base.metadata — models stay
the single schema source; migrations are the deployable history.

Application startup never runs migrations (`alembic upgrade` is an explicit
operator action; the API process only assumes the schema is current).
"""
from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Make services/api importable when alembic runs from other cwd.
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from app.persistence.models import Base  # noqa: E402

target_metadata = Base.metadata


def _database_url() -> str:
    url = (os.getenv("EDU_MIGRATION_DATABASE_URL")
           or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError(
            "alembic needs DATABASE_URL (or EDU_MIGRATION_DATABASE_URL) — "
            "refusing to guess a target database.")
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


def run_migrations_offline() -> None:
    """Emit SQL to stdout without touching a database."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=url_is_sqlite(),
    )
    with context.begin_transaction():
        context.run_migrations()


def url_is_sqlite() -> bool:
    return _database_url().startswith("sqlite")


def run_migrations_online() -> None:
    """Run migrations against a live database over the async engine."""
    config.set_main_option("sqlalchemy.url", _database_url())

    def do_run(connection: Connection) -> None:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # sqlite batch mode keeps ALTERs working in the smoke lane.
            render_as_batch=_database_url().startswith("sqlite"),
        )
        with context.begin_transaction():
            context.run_migrations()

    async def run_async() -> None:
        connectable = async_engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        try:
            async with connectable.connect() as connection:
                await connection.run_sync(do_run)
        finally:
            await connectable.dispose()

    asyncio.run(run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
