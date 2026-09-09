"""Elimination round: may hawak na numero, at labas kapag natawag.

Ang unique constraint sa `(round_id, number)` ang bumabantay na walang dalawang
bisitang parehong numero, kaya isahan ang bawat pagtanggal.
"""

from __future__ import annotations

import re
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.db.models import EliminationTicket, GameRound, TicketNumber
from app.db.session import get_session_factory


async def make_elimination(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    numbers_per_ticket: int = 1,
    caller_mode: str = "manual",
) -> dict[str, Any]:
    response = await client.post(
        "/api/rounds",
        json={
            "game_type": "elimination",
            "caller_mode": caller_mode,
            "numbers_per_ticket": numbers_per_ticket,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["game_type"] == "elimination"
    return body


async def join(
    client: AsyncClient, headers: dict[str, str], round_id: str, name: str
) -> tuple[str, list[int]]:
    """Sumali sa elimination round. Ibinabalik ang play token at ang numero."""
    created = await client.post("/api/pairing", json={"round_id": round_id}, headers=headers)
    assert created.status_code == 200, created.text
    nonce = created.json()["pair_url"].rsplit("/", 1)[-1]

    page = await client.post(f"/pair/{nonce}/claim", data={"given_name": name})
    assert page.status_code == 200, page.status_code

    token = re.search(r'href="/play/([^"]+)"', page.text).group(1)
    numbers = [
        int(n) for n in re.findall(r'<div class="ticket-number">\s*(\d+)\s*</div>', page.text)
    ]
    assert numbers, page.text[:400]
    return token, numbers


async def call_ball(client: AsyncClient, headers: dict[str, str], round_id: str, ball: int) -> None:
    response = await client.post(
        f"/api/rounds/{round_id}/draw", json={"ball": ball}, headers=headers
    )
    assert response.status_code == 200, response.text


async def state(client: AsyncClient, headers: dict[str, str], round_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/rounds/{round_id}", headers=headers)
    assert response.status_code == 200
    return response.json()


# --- Setup validation -----------------------------------------------------


async def test_elimination_cannot_use_cards_only_mode(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Walang matatanggal kung hindi alam ng app ang bola."""
    response = await client.post(
        "/api/rounds",
        json={"game_type": "elimination", "caller_mode": "offline"},
        headers=operator_headers,
    )
    assert response.status_code == 422
    assert "cards-only" in response.json()["detail"]


async def test_numbers_per_guest_is_bounded(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    for count in (0, 6, 99):
        response = await client.post(
            "/api/rounds",
            json={"game_type": "elimination", "numbers_per_ticket": count},
            headers=operator_headers,
        )
        assert response.status_code == 422, count


async def test_classic_rejects_ticket_settings(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/rounds",
        json={"game_type": "classic", "numbers_per_ticket": 3},
        headers=operator_headers,
    )
    assert response.status_code == 422
    assert "elimination" in response.json()["detail"]


async def test_unknown_game_type_is_rejected(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/rounds", json={"game_type": "roulette"}, headers=operator_headers
    )
    assert response.status_code == 422


# --- Ticket assignment ----------------------------------------------------


async def test_each_guest_gets_a_different_number(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_elimination(client, operator_headers)
    handed_out: list[int] = []
    for index in range(12):
        _, numbers = await join(client, operator_headers, game["id"], f"Guest{index}")
        assert len(numbers) == 1
        handed_out.extend(numbers)

    assert len(set(handed_out)) == 12, handed_out

    async with get_session_factory()() as db:
        rows = list(
            await db.scalars(select(TicketNumber.number).where(TicketNumber.round_id == game["id"]))
        )
    assert sorted(rows) == sorted(handed_out)


async def test_multiple_numbers_per_guest(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_elimination(client, operator_headers, numbers_per_ticket=3)
    _, numbers = await join(client, operator_headers, game["id"], "Tatlo")
    assert len(numbers) == 3
    assert len(set(numbers)) == 3
    assert numbers == sorted(numbers)


async def test_round_summary_counts_tickets(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_elimination(client, operator_headers)
    for name in ("A", "B", "C"):
        await join(client, operator_headers, game["id"], name)

    body = await state(client, operator_headers, game["id"])
    assert body["ticket_count"] == 3
    assert body["survivors"] == 3
    assert body["player_count"] == 3
    assert body["card_count"] == 0


# --- Knockouts ------------------------------------------------------------


async def test_a_called_number_knocks_that_guest_out(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_elimination(client, operator_headers)
    _, ana_numbers = await join(client, operator_headers, game["id"], "Ana")
    await join(client, operator_headers, game["id"], "Ben")
    await join(client, operator_headers, game["id"], "Caloy")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    await call_ball(client, operator_headers, game["id"], ana_numbers[0])

    body = await state(client, operator_headers, game["id"])
    assert body["survivors"] == 2
    assert body["status"] == "drawing"

    async with get_session_factory()() as db:
        tickets = (
            await db.scalars(
                select(EliminationTicket).where(EliminationTicket.round_id == game["id"])
            )
        ).all()
    out = [t for t in tickets if t.eliminated_at is not None]
    assert len(out) == 1
    assert out[0].eliminated_by_ball == ana_numbers[0]
    assert out[0].eliminated_at_draw == 1


async def test_a_number_nobody_holds_changes_nothing(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_elimination(client, operator_headers)
    _, mine = await join(client, operator_headers, game["id"], "Solo")
    await join(client, operator_headers, game["id"], "Kasama")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    async with get_session_factory()() as db:
        taken = set(
            await db.scalars(select(TicketNumber.number).where(TicketNumber.round_id == game["id"]))
        )
    spare = next(n for n in range(1, 76) if n not in taken)

    await call_ball(client, operator_headers, game["id"], spare)
    body = await state(client, operator_headers, game["id"])
    assert body["survivors"] == 2
    assert mine  # walang naapektuhan


async def test_last_one_standing_wins(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_elimination(client, operator_headers)
    _, ana = await join(client, operator_headers, game["id"], "Ana")
    _, ben = await join(client, operator_headers, game["id"], "Ben")
    _, caloy = await join(client, operator_headers, game["id"], "Caloy")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    await call_ball(client, operator_headers, game["id"], ana[0])
    await call_ball(client, operator_headers, game["id"], ben[0])

    body = await state(client, operator_headers, game["id"])
    assert body["survivors"] == 1
    assert body["status"] == "won"

    async with get_session_factory()() as db:
        winners = (
            await db.scalars(
                select(EliminationTicket).where(
                    EliminationTicket.round_id == game["id"],
                    EliminationTicket.is_winner.is_(True),
                )
            )
        ).all()
    assert len(winners) == 1
    assert winners[0].eliminated_at is None
    assert caloy  # si Caloy ang natira


async def test_one_ball_can_only_remove_one_guest(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Dahil unique ang numero kada round, isa lang ang natatanggal kada bola.

    Ito ang dahilan kung bakit hindi nangyayari ang sabay na pagtanggal ng
    dalawang bisita: walang numerong hawak ng dalawa.
    """
    game = await make_elimination(client, operator_headers, numbers_per_ticket=2)
    _, ana = await join(client, operator_headers, game["id"], "Ana")
    _, ben = await join(client, operator_headers, game["id"], "Ben")
    assert not set(ana) & set(ben), "hindi dapat maghati sa numero"

    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)
    await call_ball(client, operator_headers, game["id"], ana[0])

    body = await state(client, operator_headers, game["id"])
    assert body["survivors"] == 1
    assert body["status"] == "won"


async def test_the_final_survivor_being_called_still_wins(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Kapag ang huling natira ang natawag, panalo pa rin siya.

    Siya ang pinakamatagal na tumagal, kaya kahit natanggal ang numero niya ay
    wala nang ibang makakapanalo.
    """
    game = await make_elimination(client, operator_headers)
    _, solo = await join(client, operator_headers, game["id"], "Nag-isa")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    await call_ball(client, operator_headers, game["id"], solo[0])

    body = await state(client, operator_headers, game["id"])
    assert body["survivors"] == 0
    assert body["status"] == "won"

    async with get_session_factory()() as db:
        winners = (
            await db.scalars(
                select(EliminationTicket).where(
                    EliminationTicket.round_id == game["id"],
                    EliminationTicket.is_winner.is_(True),
                )
            )
        ).all()
    assert len(winners) == 1
    assert winners[0].eliminated_by_ball == solo[0]


async def test_a_guest_with_several_numbers_goes_out_on_the_first_hit(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_elimination(client, operator_headers, numbers_per_ticket=3)
    _, mine = await join(client, operator_headers, game["id"], "Tatlo")
    await join(client, operator_headers, game["id"], "Kasama")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    await call_ball(client, operator_headers, game["id"], mine[1])

    async with get_session_factory()() as db:
        ticket = await db.scalar(
            select(EliminationTicket).where(
                EliminationTicket.round_id == game["id"],
                EliminationTicket.eliminated_at.is_not(None),
            )
        )
    assert ticket is not None
    assert ticket.eliminated_by_ball == mine[1]


# --- Player at caller views ----------------------------------------------


async def test_player_sees_their_numbers_not_a_card(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_elimination(client, operator_headers, numbers_per_ticket=2)
    token, numbers = await join(client, operator_headers, game["id"], "Ana")

    page = await client.get(f"/play/{token}")
    assert page.status_code == 200
    assert "You're still in" in page.text
    for number in numbers:
        assert f'id="number-{number}"' in page.text
    # Walang 5x5 grid at walang BINGO button.
    assert "BINGO!" not in page.text
    assert "FREE" not in page.text


async def test_player_page_shows_knocked_out_state(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_elimination(client, operator_headers)
    token, numbers = await join(client, operator_headers, game["id"], "Ana")
    await join(client, operator_headers, game["id"], "Ben")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)
    await call_ball(client, operator_headers, game["id"], numbers[0])

    page = await client.get(f"/play/{token}")
    assert "You're out" in page.text
    assert 'data-state="out"' in page.text


async def test_pressing_bingo_in_elimination_is_refused(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_elimination(client, operator_headers)
    token, _ = await join(client, operator_headers, game["id"], "Ana")

    response = await client.post(f"/api/play/{token}/bingo")
    assert response.status_code == 409
    assert "nothing to claim" in response.json()["detail"]


async def test_caller_screen_shows_the_roster(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    from tests.conftest import TEST_API_KEY

    await client.post("/operator/session", data={"api_key": TEST_API_KEY})
    game = await make_elimination(client, operator_headers)
    _, ana = await join(client, operator_headers, game["id"], "Ana")
    await join(client, operator_headers, game["id"], "Ben")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)
    await call_ball(client, operator_headers, game["id"], ana[0])

    page = await client.get(f"/caller/{game['id']}")
    assert page.status_code == 200
    assert "Who's left" in page.text
    assert 'data-name="Ana"' in page.text
    assert 'data-name="Ben"' in page.text
    assert "roster-row out" in page.text
    # Walang pattern na binabanggit: hindi bagay sa elimination.
    assert "Pattern:" not in page.text


async def test_classic_rounds_are_untouched(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Ang elimination ay hindi dapat nakagalaw sa dating laro."""
    from tests.test_rounds_api import cards_of, make_round
    from tests.test_rounds_api import join as classic_join

    game = await make_round(client, operator_headers, "four_corners")
    assert game["game_type"] == "classic"
    token = await classic_join(client, operator_headers, game["id"], card_count=2)

    cards = await cards_of(token)
    assert len(cards) == 2
    page = await client.get(f"/play/{token}")
    assert "BINGO!" in page.text

    async with get_session_factory()() as db:
        refreshed = await db.scalar(select(GameRound).where(GameRound.id == game["id"]))
        tickets = (await db.scalars(select(EliminationTicket))).all()
    assert refreshed is not None
    assert refreshed.numbers_per_ticket == 1
    assert tickets == []
