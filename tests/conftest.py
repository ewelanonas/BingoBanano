"""Test fixtures. Isolated na SQLite file kada test, walang totoong network."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

TEST_API_KEY = "test-operator-key-0123456789abcdefghij"


@pytest.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "test.db"
    env = {
        "BINGO_OPERATOR_API_KEY": TEST_API_KEY,
        "BINGO_DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
        "BINGO_PUBLIC_BASE_URL": "http://192.168.1.10:8000",
        "BINGO_PAIRING_TTL_SECONDS": "120",
        "BINGO_PAIRING_RATE_LIMIT_PER_MINUTE": "200",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    # Iwasan ang .env ng dev machine sa loob ng tests.
    monkeypatch.chdir(tmp_path)

    from app.config import get_settings
    from app.db import session as db_session

    get_settings.cache_clear()
    await db_session.dispose_engine()

    from app.api.security import get_limiter
    from app.main import create_app

    get_limiter().reset()

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as http_client:
        async with app.router.lifespan_context(app):
            yield http_client

    await db_session.dispose_engine()
    get_settings.cache_clear()
    for key in env:
        os.environ.pop(key, None)


@pytest.fixture
def operator_headers() -> dict[str, str]:
    return {"X-API-Key": TEST_API_KEY}
