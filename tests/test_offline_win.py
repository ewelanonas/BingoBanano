"""Ang host ang nagpapasya sa cards-only mode, at may dapat itala kapag ganoon.

Bago ito, ang cards-only round ay walang katapusan: naaanunsyo ang BINGO pero
walang paraan para sabihin ng host na tama nga, kaya walang nakikitang anunsyo
ang mga bisita at "decided by the host" lang ang naitatala sa history.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.db.models import BingoCard, Claim, GameRound
from app.db.session import get_session_factory
from tests.test_rounds_api import join


async def make_offline_round(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.post(
        "/api/rounds",
        json={"pattern": "any_line", "caller_mode": "offline", "label": "party"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def announce(client: AsyncClient, token: str) -> dict[str, Any]:
    result = await client.post(f"/api/play/{token}/bingo", json={"marked": [3, 17, 42]})
    assert result.status_code == 200, result.text
    body: dict[str, Any] = result.json()
    # Hindi nagdedeklara ang server dito. Ito ang dahilan kung bakit kailangan
    # ng pasya ng host.
    assert body["verified"] is False
    assert body["reason"] == "announced"
    return body


async def test_host_can_confirm_a_cards_only_win(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_offline_round(client, operator_headers)
    token = await join(client, operator_headers, game["id"], name="Eli")
    await client.post(f"/api/rounds/{game['id']}/start", json={}, headers=operator_headers)
    await announce(client, token)

    async with get_session_factory()() as db:
        card = await db.scalar(select(BingoCard).where(BingoCard.round_id == game["id"]))
    assert card is not None

    confirmed = await client.post(
        f"/api/rounds/{game['id']}/confirm-win",
        json={"card_serial": card.serial},
        headers=operator_headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "won"

    async with get_session_factory()() as db:
        stored = await db.scalar(select(BingoCard).where(BingoCard.id == card.id))
        game_row = await db.scalar(select(GameRound).where(GameRound.id == game["id"]))
        # Ang claim ay append-only, kaya hindi ito ginagalaw ng confirmation.
        claim = await db.scalar(select(Claim).where(Claim.round_id == game["id"]))
    assert stored is not None and stored.is_winner is True
    assert game_row is not None and game_row.status == "won"
    assert claim is not None and claim.verified is False


async def test_confirmed_win_reaches_the_history(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    await client.post("/operator/session", data={"api_key": _key()})
    game = await make_offline_round(client, operator_headers)
    token = await join(client, operator_headers, game["id"], name="Eli")
    await client.post(f"/api/rounds/{game['id']}/start", json={}, headers=operator_headers)
    await announce(client, token)

    async with get_session_factory()() as db:
        card = await db.scalar(select(BingoCard).where(BingoCard.round_id == game["id"]))
    assert card is not None
    await client.post(
        f"/api/rounds/{game['id']}/confirm-win",
        json={"card_serial": card.serial},
        headers=operator_headers,
    )

    page = await client.get("/history")
    assert page.status_code == 200
    # Walang naitalang bola sa mode na ito, kaya walang bilang na sinasabi.
    assert "Eli won, confirmed by the host" in page.text
    assert "on ball" not in page.text


async def test_tracked_rounds_refuse_host_confirmation(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Sa auto at manual ay may draw table, kaya ang server ang nagpapasya."""
    made = await client.post(
        "/api/rounds",
        json={"pattern": "any_line", "caller_mode": "auto"},
        headers=operator_headers,
    )
    game = made.json()
    token = await join(client, operator_headers, game["id"], name="Ana")
    await client.post(f"/api/rounds/{game['id']}/start", json={}, headers=operator_headers)

    async with get_session_factory()() as db:
        card = await db.scalar(select(BingoCard).where(BingoCard.round_id == game["id"]))
    assert card is not None
    assert token

    refused = await client.post(
        f"/api/rounds/{game['id']}/confirm-win",
        json={"card_serial": card.serial},
        headers=operator_headers,
    )
    assert refused.status_code == 409
    assert "decides the winner itself" in refused.json()["detail"]


async def test_confirming_an_unknown_card_is_refused(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_offline_round(client, operator_headers)
    await join(client, operator_headers, game["id"], name="Eli")
    await client.post(f"/api/rounds/{game['id']}/start", json={}, headers=operator_headers)

    refused = await client.post(
        f"/api/rounds/{game['id']}/confirm-win",
        json={"card_serial": "BB-NOPE-NOPE"},
        headers=operator_headers,
    )
    assert refused.status_code == 409
    assert "not in this round" in refused.json()["detail"]


async def test_confirm_requires_the_operator(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_offline_round(client, operator_headers)
    response = await client.post(
        f"/api/rounds/{game['id']}/confirm-win", json={"card_serial": "BB-X"}
    )
    assert response.status_code in (401, 403)


async def test_caller_screen_offers_the_popup_preview(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Kailangan ng host ng paraan para masilip ang popup bago ang party."""
    await client.post("/operator/session", data={"api_key": _key()})
    game = await make_offline_round(client, operator_headers)

    page = await client.get(f"/caller/{game['id']}")
    assert page.status_code == 200
    assert 'id="preview-button"' in page.text
    assert "showCelebration(" in page.text


def _key() -> str:
    from tests.conftest import TEST_API_KEY

    return TEST_API_KEY
