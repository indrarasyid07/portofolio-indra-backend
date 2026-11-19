"""Request-scoped async SQLAlchemy sessions."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.engine import get_engine


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped session and roll back an unfinished transaction."""
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
