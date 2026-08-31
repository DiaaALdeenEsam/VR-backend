"""Alembic environment.

Supports two modes:
  1. Normal CLI use (`alembic upgrade head`) -- builds its own async engine from
     app.config.get_settings().database_url and runs migrations against it.
  2. Programmatic use with a pre-existing connection, via
     `config.attributes["connection"] = <sync-style connection>` -- used by
     tests/conftest.py to run migrations against the same in-memory SQLite
     connection the test itself will use (see AsyncConnection.run_sync).
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import models so SQLModel.metadata is populated before we reference it below.
from app.config import get_settings
from app.infrastructure.db.models import SQLModel  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def get_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        {"sqlalchemy.url": get_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    existing_connection = config.attributes.get("connection", None)
    if existing_connection is not None:
        # Already inside AsyncConnection.run_sync (a sync-style connection proxy) --
        # run migrations directly, no nested event loop involved.
        do_run_migrations(existing_connection)
    else:
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
