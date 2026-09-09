"""Async engine at session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.db.models import Base

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        connect_args: dict[str, object] = {}
        if settings.database_url.startswith("sqlite"):
            # Mas mahabang lock wait para hindi agad mag-"database is locked"
            # kapag sabay-sabay ang claim attempts.
            connect_args["timeout"] = 30
        _engine = create_async_engine(
            settings.database_url,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _factory
    if _factory is None:
        _factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _factory


async def create_schema() -> None:
    """Scaffold-level schema creation.

    Palitan ito ng Alembic migration bago ang unang totoong schema change —
    kailangan ng schema history para sa audit.
    """
    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _factory = None


async def get_db() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session
