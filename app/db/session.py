"""Async engine at session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import Connection, inspect
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


class SchemaOutdatedError(RuntimeError):
    """Ang database file ay galing sa lumang bersyon ng app."""


def _database_file(url: str) -> str | None:
    """Ang path ng SQLite file, kung SQLite nga ang gamit."""
    if not url.startswith("sqlite"):
        return None
    _, _, tail = url.partition("///")
    return tail or None


def _assert_schema_current(connection: Connection) -> None:
    """Ihambing ang aktuwal na schema sa mga model.

    Ang `create_all()` ay gumagawa ng kulang na table pero HINDI nagdadagdag ng
    column sa table na existing na. Kaya kapag may bagong field na naidagdag at
    luma pa ang database file, tahimik itong dumadaan at bumabagsak lang sa
    unang INSERT — malayo na sa tunay na dahilan. Dito natin hinuhuli iyon.
    """
    inspector = inspect(connection)
    problems: list[str] = []

    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):  # pragma: no cover - ginawa na ni create_all
            problems.append(f"{table.name}: buong table ay wala")
            continue
        actual = {column["name"] for column in inspector.get_columns(table.name)}
        missing = sorted({column.name for column in table.columns} - actual)
        if missing:
            problems.append(f"{table.name}: {', '.join(missing)}")

    if not problems:
        return

    path = _database_file(get_settings().database_url) or "your database"
    raise SchemaOutdatedError(
        "The database was created by an older version of BingoBanano and is "
        "missing columns:\n  "
        + "\n  ".join(problems)
        + "\n\nRounds are disposable, so the fix is to delete the file and let it "
        f"be recreated:\n  Remove-Item -Force {path}\n"
        "Or run: .\\scripts\\setup-dev.ps1 -Reset"
    )


async def create_schema() -> None:
    """Scaffold-level schema creation, tapos schema drift check.

    Palitan ito ng Alembic migration kapag kailangan nang panatilihin ang data
    sa pagitan ng mga bersyon. Sa ngayon ay disposable ang bawat round, kaya
    sapat na ang malinaw na error at pag-delete ng file.
    """
    async with get_engine().begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(_assert_schema_current)


async def dispose_engine() -> None:
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _factory = None


async def get_db() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session
