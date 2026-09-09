"""Tatlong caller mode: app ang bumubunot, host ang nagtatala, o cards lang.

Ang `manual` mode ay dumadaan sa parehong Draw table gaya ng `auto`, kaya ang
marking, verification, at co-winner na lohika ay hindi na kailangang doblehin —
ang mga test na ito ang nagpapatunay niyon.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.db.models import Claim, Draw, GameRound
from app.db.session import get_session_factory
from app.domain.patterns import FREE_INDEX
from tests.test_rounds_api import cards_of, join, make_round


async def make_mode_round(
    client: AsyncClient,
    headers: dict[str, str],
    mode: str,
    pattern: str = "any_line",
) -> dict[str, Any]:
    response = await client.post(
        "/api/rounds",
        json={"pattern": pattern, "caller_mode": mode},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["caller_mode"] == mode
    return body


async def start(client: AsyncClient, headers: dict[str, str], round_id: str) -> None:
    response = await client.post(f"/api/rounds/{round_id}/start", headers=headers)
    assert response.status_code == 200, response.text


# --- Mode validation ------------------------------------------------------


async def test_unknown_caller_mode_is_rejected(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/rounds", json={"caller_mode": "telepathy"}, headers=operator_headers
    )
    assert response.status_code == 422


async def test_auto_is_the_default(client: AsyncClient, operator_headers: dict[str, str]) -> None:
    game = await make_round(client, operator_headers)
    assert game["caller_mode"] == "auto"


# --- Manual mode ----------------------------------------------------------


async def test_manual_records_the_ball_the_host_supplies(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_mode_round(client, operator_headers, "manual")
    await start(client, operator_headers, game["id"])

    for expected_sequence, ball in enumerate([7, 42, 61], start=1):
        response = await client.post(
            f"/api/rounds/{game['id']}/draw", json={"ball": ball}, headers=operator_headers
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ball"] == ball
        assert body["sequence_no"] == expected_sequence

    assert (
        await client.post(
            f"/api/rounds/{game['id']}/draw", json={"ball": 7}, headers=operator_headers
        )
    ).status_code == 409, "hindi dapat puwedeng maulit ang bola"

    async with get_session_factory()() as db:
        balls = list(await db.scalars(select(Draw.ball).where(Draw.round_id == game["id"])))
    assert sorted(balls) == [7, 42, 61]


async def test_manual_calls_use_the_right_letter(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_mode_round(client, operator_headers, "manual")
    await start(client, operator_headers, game["id"])

    for ball, call in ((1, "B-1"), (16, "I-16"), (31, "N-31"), (46, "G-46"), (75, "O-75")):
        response = await client.post(
            f"/api/rounds/{game['id']}/draw", json={"ball": ball}, headers=operator_headers
        )
        assert response.json()["call"] == call


async def test_manual_requires_a_ball(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_mode_round(client, operator_headers, "manual")
    await start(client, operator_headers, game["id"])

    response = await client.post(f"/api/rounds/{game['id']}/draw", headers=operator_headers)
    assert response.status_code == 409
    assert "machine" in response.json()["detail"]


async def test_out_of_range_ball_is_rejected(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_mode_round(client, operator_headers, "manual")
    await start(client, operator_headers, game["id"])

    for ball in (0, 76, -3):
        response = await client.post(
            f"/api/rounds/{game['id']}/draw", json={"ball": ball}, headers=operator_headers
        )
        assert response.status_code == 422, ball


async def test_auto_refuses_a_supplied_ball(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Kung tinanggap ito, mapipili ng host kung sino ang mananalo."""
    game = await make_mode_round(client, operator_headers, "auto")
    await start(client, operator_headers, game["id"])

    response = await client.post(
        f"/api/rounds/{game['id']}/draw", json={"ball": 7}, headers=operator_headers
    )
    assert response.status_code == 409
    assert "draws its own numbers" in response.json()["detail"]


async def test_manual_still_verifies_bingo(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Ang buong punto ng manual mode: parehong verification gaya ng auto."""
    game = await make_mode_round(client, operator_headers, "manual", "row_3")
    token = await join(client, operator_headers, game["id"])
    await start(client, operator_headers, game["id"])

    cards = await cards_of(token)
    middle_row = [
        value
        for index, value in enumerate(cards[0].numbers)
        if 10 <= index <= 14 and index != FREE_INDEX
    ]

    # Kulang ng isa: hindi pa panalo.
    for ball in middle_row[:-1]:
        await client.post(
            f"/api/rounds/{game['id']}/draw", json={"ball": ball}, headers=operator_headers
        )
    early = (await client.post(f"/api/play/{token}/bingo")).json()
    assert early["verified"] is False
    assert early["missing"] == 1

    # Ang huling numero.
    await client.post(
        f"/api/rounds/{game['id']}/draw",
        json={"ball": middle_row[-1]},
        headers=operator_headers,
    )
    won = (await client.post(f"/api/play/{token}/bingo")).json()
    assert won["verified"] is True
    assert won["is_first_winner"] is True

    async with get_session_factory()() as db:
        refreshed = await db.scalar(select(GameRound).where(GameRound.id == game["id"]))
    assert refreshed is not None
    assert refreshed.status == "won"


# --- Offline mode ---------------------------------------------------------


async def test_offline_refuses_to_draw(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_mode_round(client, operator_headers, "offline")
    await start(client, operator_headers, game["id"])

    for body in (None, {"ball": 7}):
        response = await client.post(
            f"/api/rounds/{game['id']}/draw", json=body, headers=operator_headers
        )
        assert response.status_code == 409
        assert "not tracking numbers" in response.json()["detail"]


async def test_offline_announces_instead_of_declaring_a_winner(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_mode_round(client, operator_headers, "offline")
    token = await join(client, operator_headers, game["id"], card_count=2)
    await start(client, operator_headers, game["id"])

    response = await client.post(f"/api/play/{token}/bingo")
    assert response.status_code == 200
    body = response.json()
    assert body["verified"] is False
    assert body["reason"] == "announced"
    assert "host is checking" in body["message"]

    async with get_session_factory()() as db:
        claims = (await db.scalars(select(Claim))).all()
        refreshed = await db.scalar(select(GameRound).where(GameRound.id == game["id"]))

    assert len(claims) == 1
    assert claims[0].reason == "announced"
    assert claims[0].is_first_winner is False
    # Hindi nagsasara ang round: ang host ang magpapasya.
    assert refreshed is not None
    assert refreshed.status == "drawing"
    assert refreshed.first_winner_card_id is None


async def test_offline_player_board_allows_free_marking(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_mode_round(client, operator_headers, "offline")
    token = await join(client, operator_headers, game["id"], card_count=2)

    page = await client.get(f"/play/{token}")
    assert page.status_code == 200
    assert "const FREE_MARKING = true" in page.text
    assert "Tap your numbers as the host calls them" in page.text
    # Walang auto-daub toggle: walang binibilang na dapat sabayan.
    assert 'id="auto-daub"' not in page.text

    # Bawat numero sa dalawang card ay pinipindot: 24 kada card.
    assert page.text.count('class="dab"') == 48
    assert page.text.count('aria-pressed="false"') == 48


async def test_offline_marks_survive_a_closed_tab(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Walang binibilang ang server dito, kaya sa phone naka-save ang marka.

    Kung hindi, mawawala ang bawat pindot kapag nag-lock ang phone o nag-reload
    ang tab, at wala nang paraan para maibalik.
    """
    game = await make_mode_round(client, operator_headers, "offline")
    token = await join(client, operator_headers, game["id"])

    page = await client.get(f"/play/{token}")
    # Ang key ay nakabase sa play token, kaya hiwalay ang marka kada laro.
    assert "bingobanano.marks." in page.text
    assert "localStorage.setItem" in page.text
    assert "localStorage.getItem" in page.text
    # May paraan ding burahin lahat, dahil normal ang mali-mali sa malayang pagpindot.
    assert 'id="clear-marks"' in page.text
    assert "Clear all marks" in page.text


async def test_tracked_modes_filter_restored_marks(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Ang naka-save na marka ay hindi puwedeng lumampas sa aktuwal na nailabas.

    Kung hindi ito sinala, puwedeng umilaw ang BINGO button sa card na hindi
    talaga panalo.
    """
    game = await make_mode_round(client, operator_headers, "auto")
    token = await join(client, operator_headers, game["id"], name="Tracked")

    page = await client.get(f"/play/{token}")
    assert "if (!FREE_MARKING && !drawn.has(ball)) { continue; }" in page.text
    # Walang clear button sa mode na may binibilang: awtomatiko naman ang marka.
    assert 'id="clear-marks"' not in page.text


async def test_tracked_modes_do_not_allow_free_marking(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    for mode in ("auto", "manual"):
        game = await make_mode_round(client, operator_headers, mode)
        token = await join(client, operator_headers, game["id"], name=f"P{mode}")
        page = await client.get(f"/play/{token}")
        assert "const FREE_MARKING = false" in page.text, mode
        assert 'id="auto-daub"' in page.text, mode


# --- Caller screen --------------------------------------------------------


async def test_caller_screen_is_mode_aware(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    from tests.conftest import TEST_API_KEY

    await client.post("/operator/session", data={"api_key": TEST_API_KEY})

    auto = await make_mode_round(client, operator_headers, "auto")
    manual = await make_mode_round(client, operator_headers, "manual")
    offline = await make_mode_round(client, operator_headers, "offline")

    # Ang markup ng pinipindot na board, hindi ang mention sa JavaScript.
    pick_button = 'class="board-ball board-pick"'

    auto_page = await client.get(f"/caller/{auto['id']}")
    assert "Draw next ball" in auto_page.text
    assert pick_button not in auto_page.text

    manual_page = await client.get(f"/caller/{manual['id']}")
    assert "Click the number that came out" in manual_page.text
    assert pick_button in manual_page.text
    assert manual_page.text.count(pick_button) == 75
    assert "Draw next ball" not in manual_page.text

    offline_page = await client.get(f"/caller/{offline['id']}")
    assert "Called BINGOs" in offline_page.text
    assert "not tracking numbers" in offline_page.text
    assert pick_button not in offline_page.text
    # Walang board sa offline: wala namang binibilang.
    assert 'id="board"' not in offline_page.text
