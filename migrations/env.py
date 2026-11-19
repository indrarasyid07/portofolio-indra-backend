"""Alembic runner guarded against accidental production migration."""

import asyncio
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.db.base import Base
from app.models import *  # noqa: F403

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def migration_url() -> str:
    """Use the direct migration URL and refuse production execution."""
    settings = get_settings()
    if settings.app_env.lower() == "production":
        raise RuntimeError("Alembic CLI ini dilarang di production; gunakan release workflow.")
    if settings.database_direct_url is None:
        raise RuntimeError("DATABASE_DIRECT_URL harus diatur untuk migrasi.")
    return settings.database_direct_url.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(
        url=migration_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = migration_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    if sys.platform == "win32":
        # Psycopg's async connection needs the selector loop on Windows.
        asyncio.run(run_async_migrations(), loop_factory=asyncio.SelectorEventLoop)
        return
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
