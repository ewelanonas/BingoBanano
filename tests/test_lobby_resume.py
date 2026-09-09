"""Ang round ay nasa DB, kaya hindi dapat mawala kapag umalis sa page.

Ang bug na tinatakpan nito: pagkabukas ng caller screen at pagbalik sa lobby, ang
round ay mukhang nabura dahil sa memory lang ng page ito nakatago.
"""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import TEST_API_KEY
from tests.test_rounds_api import join, make_round


async def sign_in(client: AsyncClient) -> None:
    await client.post("/operator/session", data={"api_key": TEST_API_KEY})


async def test_lobby_shows_nothing_when_no_round_is_running(
    client: AsyncClient,
) -> None:
    await sign_in(client)
    page = await client.get("/kiosk")
    assert page.status_code == 200
    assert "Rounds still going" not in page.text


async def test_open_round_is_listed_in_the_lobby(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    await sign_in(client)
    game = await make_round(client, operator_headers)

    page = await client.get("/kiosk")
    assert page.status_code == 200
    assert "Rounds still going" in page.text
    assert game["join_code"] in page.text
    # Dalawang paraan pabalik: magdagdag ng bisita, o sa caller screen.
    assert f'data-round="{game["id"]}"' in page.text
    assert f'href="/caller/{game["id"]}"' in page.text


async def test_drawing_round_is_listed_without_the_add_guests_button(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    await sign_in(client)
    game = await make_round(client, operator_headers)
    await join(client, operator_headers, game["id"])
    started = await client.post(
        f"/api/rounds/{game['id']}/start", json={}, headers=operator_headers
    )
    assert started.status_code == 200, started.text

    page = await client.get("/kiosk")
    assert "Rounds still going" in page.text
    assert game["join_code"] in page.text
    assert "Drawing" in page.text
    # Sarado na ang pagsali, kaya hindi dapat may button na nangangako nito.
    assert f'data-round="{game["id"]}"' not in page.text


async def test_closed_round_leaves_the_lobby(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    await sign_in(client)
    game = await make_round(client, operator_headers)
    closed = await client.post(f"/api/rounds/{game['id']}/close", json={}, headers=operator_headers)
    assert closed.status_code == 200, closed.text

    page = await client.get("/kiosk")
    assert game["join_code"] not in page.text
