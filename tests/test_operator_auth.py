"""Operator login, cookie session, at CSRF."""

from __future__ import annotations

from httpx import AsyncClient

from app.api.security import CSRF_HEADER, OPERATOR_COOKIE
from tests.conftest import TEST_API_KEY


async def test_wrong_api_key_does_not_create_a_session(client: AsyncClient) -> None:
    response = await client.post("/operator/session", data={"api_key": "mali"})
    assert response.status_code == 303
    assert response.headers["location"] == "/operator?error=invalid"
    assert OPERATOR_COOKIE not in response.cookies


async def test_login_sets_httponly_cookie(client: AsyncClient) -> None:
    response = await client.post("/operator/session", data={"api_key": TEST_API_KEY})
    assert response.status_code == 303
    assert response.headers["location"] == "/kiosk"

    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "samesite=strict" in set_cookie.lower()


async def test_kiosk_requires_a_session(client: AsyncClient) -> None:
    response = await client.get("/kiosk")
    assert response.status_code == 303
    assert response.headers["location"] == "/operator"


async def test_kiosk_page_carries_a_csrf_token(client: AsyncClient) -> None:
    await client.post("/operator/session", data={"api_key": TEST_API_KEY})
    page = await client.get("/kiosk")
    assert page.status_code == 200
    assert "CSRF_TOKEN" in page.text
    # Ang API key ay hindi kailanman naipapadala sa browser.
    assert TEST_API_KEY not in page.text


async def test_cookie_session_without_csrf_header_is_refused(client: AsyncClient) -> None:
    await client.post("/operator/session", data={"api_key": TEST_API_KEY})
    response = await client.post("/api/pairing", json={})
    assert response.status_code == 403


async def test_cookie_session_with_csrf_header_works(client: AsyncClient) -> None:
    await client.post("/operator/session", data={"api_key": TEST_API_KEY})
    page = await client.get("/kiosk")
    token = page.text.split('const CSRF_TOKEN = "', 1)[1].split('"', 1)[0]
    csrf = {CSRF_HEADER: token}

    game = await client.post("/api/rounds", json={"pattern": "any_line"}, headers=csrf)
    assert game.status_code == 200

    response = await client.post("/api/pairing", json={"round_id": game.json()["id"]}, headers=csrf)
    assert response.status_code == 200


async def test_logout_revokes_the_session(client: AsyncClient) -> None:
    await client.post("/operator/session", data={"api_key": TEST_API_KEY})
    await client.post("/operator/logout")
    response = await client.get("/kiosk")
    assert response.status_code == 303
