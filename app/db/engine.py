"""Lazy async engine creation for the pooled application database URL."""

import asyncio
import sys
from functools import lru_cache

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import get_settings

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class DatabaseUnavailableError(RuntimeError):
    """Raised when the application has no configured database connection."""


def _async_database_url(raw_url: str) -> str:
    """Normalize Postgres URLs for psycopg async, preserving libpq TLS options."""
    url = make_url(raw_url)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    if url.drivername != "postgresql+psycopg":
        raise ValueError("DATABASE_URL harus menggunakan PostgreSQL psycopg.")
    return url.render_as_string(hide_password=False)


@lru_cache
def get_engine() -> AsyncEngine:
    """Create a small, pre-ping pool only after a database URL is configured."""
    settings = get_settings()
    if settings.database_url is None:
        raise DatabaseUnavailableError("DATABASE_URL belum dikonfigurasi.")

    return create_async_engine(
        _async_database_url(settings.database_url.get_secret_value()),
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
        pool_timeout=10,
        connect_args={
            "connect_timeout": 10,
            "prepare_threshold": None,
        },
    )
