"""Schema drift: lumang database file kasama ang bagong app.

Ang `create_all()` ay gumagawa ng kulang na table pero hindi nagdadagdag ng
column sa table na existing na. Dati, tahimik itong dumadaan at bumabagsak lang
sa unang INSERT — malayo na sa tunay na dahilan.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models import Base


async def _build_current(path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def test_missing_columns_are_reported_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "stale.db"
    await _build_current(db_path)

    # Gayahin ang database na gawa bago pa idagdag ang caller modes at elimination.
    connection = sqlite3.connect(db_path)
    for column in ("game_type", "caller_mode"):
        connection.execute(f"ALTER TABLE game_round DROP COLUMN {column}")
    # Hindi puwedeng i-drop ng SQLite ang column na naka-index, kaya ang
    # `eliminated_at` ay iniiwan at ang mga hindi naka-index ang tinatanggal.
    for column in ("is_winner", "eliminated_at_draw"):
        connection.execute(f"ALTER TABLE bingo_card DROP COLUMN {column}")
    connection.commit()
    connection.close()

    monkeypatch.setenv("BINGO_OPERATOR_API_KEY", "x" * 40)
    monkeypatch.setenv("BINGO_DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")

    from app.config import get_settings
    from app.db import session as db_session

    get_settings.cache_clear()
    await db_session.dispose_engine()
    try:
        with pytest.raises(db_session.SchemaOutdatedError) as caught:
            await db_session.create_schema()
    finally:
        await db_session.dispose_engine()
        get_settings.cache_clear()

    message = str(caught.value)
    # Sinasabi ang mga kulang na column at ang eksaktong solusyon.
    assert "game_round" in message
    assert "game_type" in message
    assert "caller_mode" in message
    # Sinasabi rin ang ibang table na may kulang, hindi lang ang unang nakita.
    assert "bingo_card" in message
    assert "is_winner" in message
    assert "eliminated_at_draw" in message
    assert "Remove-Item" in message
    assert "-Reset" in message


async def test_a_current_database_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "fresh.db"
    monkeypatch.setenv("BINGO_OPERATOR_API_KEY", "x" * 40)
    monkeypatch.setenv("BINGO_DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")

    from app.config import get_settings
    from app.db import session as db_session

    get_settings.cache_clear()
    await db_session.dispose_engine()
    try:
        await db_session.create_schema()
        # Dapat idempotent: paulit-ulit na pagtakbo ay hindi nagre-reklamo.
        await db_session.create_schema()
    finally:
        await db_session.dispose_engine()
        get_settings.cache_clear()


async def test_a_missing_table_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ang bagong table naman ay ginagawa ni create_all, hindi ito error."""
    db_path = tmp_path / "partial.db"
    await _build_current(db_path)

    connection = sqlite3.connect(db_path)
    connection.execute("DROP TABLE audit_event")
    connection.commit()
    connection.close()

    monkeypatch.setenv("BINGO_OPERATOR_API_KEY", "x" * 40)
    monkeypatch.setenv("BINGO_DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")

    from app.config import get_settings
    from app.db import session as db_session

    get_settings.cache_clear()
    await db_session.dispose_engine()
    try:
        await db_session.create_schema()
    finally:
        await db_session.dispose_engine()
        get_settings.cache_clear()
