"""Elimination: parehong bingo card, pero labas ka kapag nalahat ang numero mo.

Ang huling natirang may numerong hindi pa natawag ang panalo. Ang alternatibong
rule na "labas sa unang tama" ay hindi mapaglalaruan: 24 na numero sa 75 na bola,
kaya 32% ng bisita ay labas na pagkatapos ng isang bola.
"""

from __future__ import annotations

import re
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.db.models import BingoCard, GameRound
from app.db.session import get_session_factory


async def make_elimination(
    client: AsyncClient,
    headers: dict[str, str],
    caller_mode: str = "manual",
) -> dict[str, Any]:
    response = await client.post(
        "/api/rounds",
        json={"game_type": "elimination", "caller_mode": caller_mode},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["game_type"] == "elimination"
    return body


async def join(
    client: AsyncClient, headers: dict[str, str], round_id: str, name: str
) -> tuple[str, list[int]]:
    """Sumali sa round. Ibinabalik ang play token at ang numero ng card."""
    created = await client.post("/api/pairing", json={"round_id": round_id}, headers=headers)
    assert created.status_code == 200, created.text
    nonce = created.json()["pair_url"].rsplit("/", 1)[-1]

    claimed = await client.post(f"/pair/{nonce}/claim", data={"given_name": name})
    # Diretso na sa live board, walang dead cards na dumadaan.
    assert claimed.status_code == 303, claimed.status_code
    token = claimed.headers["location"].split("/play/")[1].split("?")[0]

    async with get_session_factory()() as db:
        card = await db.scalar(
            select(BingoCard).join(BingoCard.session).where(BingoCard.round_id == round_id)
        )
    assert card is not None
    return token, sorted(value for value in card.numbers if value)


async def card_of(round_id: str, name: str) -> BingoCard:
    from app.db.models import Player

    async with get_session_factory()() as db:
        player = await db.scalar(select(Player).where(Player.given_name == name))
        assert player is not None
        card = await db.scalar(
            select(BingoCard).where(
                BingoCard.round_id == round_id, BingoCard.player_id == player.id
            )
        )
    assert card is not None
    return card


async def numbers_of(round_id: str, name: str) -> list[int]:
    card = await card_of(round_id, name)
    return sorted(value for value in card.numbers if value)


async def call_balls(
    client: AsyncClient, headers: dict[str, str], round_id: str, balls: list[int]
) -> None:
    for ball in balls:
        response = await client.post(
            f"/api/rounds/{round_id}/draw", json={"ball": ball}, headers=headers
        )
        assert response.status_code == 200, response.text


async def state(client: AsyncClient, headers: dict[str, str], round_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/rounds/{round_id}", headers=headers)
    assert response.status_code == 200
    return response.json()


# --- Setup ----------------------------------------------------------------


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


async def test_unknown_game_type_is_rejected(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/rounds", json={"game_type": "roulette"}, headers=operator_headers
    )
    assert response.status_code == 422


async def test_guests_get_a_normal_bingo_card(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Parehong card sa dalawang laro. Ang panalo lang ang iba."""
    game = await make_elimination(client, operator_headers)
    _, numbers = await join(client, operator_headers, game["id"], "Ana")

    assert len(numbers) == 24
    assert numbers == sorted(numbers)
    assert len(set(numbers)) == 24

    card = await card_of(game["id"], "Ana")
    assert len(card.numbers) == 25
    assert card.numbers[12] == 0  # FREE center
    assert card.eliminated_at is None
    assert card.is_winner is False


# --- Knockouts ------------------------------------------------------------


async def test_you_stay_in_until_your_whole_card_is_called(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_elimination(client, operator_headers)
    await join(client, operator_headers, game["id"], "Ana")
    await join(client, operator_headers, game["id"], "Ben")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    ana = await numbers_of(game["id"], "Ana")

    # Lahat maliban sa isa: nasa laro pa rin siya.
    await call_balls(client, operator_headers, game["id"], ana[:-1])
    body = await state(client, operator_headers, game["id"])
    assert body["survivors"] == 2, "hindi pa dapat labas: may isa pang numero"

    card = await card_of(game["id"], "Ana")
    assert card.eliminated_at is None

    # Ang huling numero niya.
    await call_balls(client, operator_headers, game["id"], [ana[-1]])
    card = await card_of(game["id"], "Ana")
    assert card.eliminated_at is not None
    assert card.eliminated_at_draw == len(ana)


async def test_a_number_nobody_holds_changes_nothing(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_elimination(client, operator_headers)
    await join(client, operator_headers, game["id"], "Ana")
    await join(client, operator_headers, game["id"], "Ben")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    held = set(await numbers_of(game["id"], "Ana")) | set(await numbers_of(game["id"], "Ben"))
    spare = next((n for n in range(1, 76) if n not in held), None)
    if spare is None:
        return  # bihira: sakop ng dalawang card ang lahat ng 75

    await call_balls(client, operator_headers, game["id"], [spare])
    body = await state(client, operator_headers, game["id"])
    assert body["survivors"] == 2


async def test_the_last_card_with_a_number_left_wins(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_elimination(client, operator_headers)
    await join(client, operator_headers, game["id"], "Ana")
    await join(client, operator_headers, game["id"], "Ben")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    ana = set(await numbers_of(game["id"], "Ana"))
    ben = set(await numbers_of(game["id"], "Ben"))
    # Ang hawak ni Ben na hindi hawak ni Ana ay iniiwan, para siya ang matira.
    ben_only = ben - ana
    if not ben_only:
        return  # bihira: sakop ni Ana ang buong card ni Ben

    await call_balls(client, operator_headers, game["id"], sorted(ana - ben_only))

    body = await state(client, operator_headers, game["id"])
    assert body["status"] == "won"
    assert body["survivors"] == 1

    winner = await card_of(game["id"], "Ben")
    loser = await card_of(game["id"], "Ana")
    assert winner.is_winner is True
    assert winner.eliminated_at is None
    assert loser.eliminated_at is not None


async def test_cards_finished_by_the_same_ball_tie(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Sa dulo ay isa o dalawang numero na lang ang kulang sa lahat, kaya kayang
    tapusin ng isang bola ang maraming card nang sabay."""
    game = await make_elimination(client, operator_headers)
    await join(client, operator_headers, game["id"], "Ana")
    await join(client, operator_headers, game["id"], "Ben")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    ana = set(await numbers_of(game["id"], "Ana"))
    ben = set(await numbers_of(game["id"], "Ben"))
    shared = ana & ben
    if not shared:
        return  # walang pinagsasaluhan, hindi puwedeng sabay matapos

    last = sorted(shared)[-1]
    await call_balls(client, operator_headers, game["id"], sorted((ana | ben) - {last}))
    body = await state(client, operator_headers, game["id"])
    assert body["survivors"] == 2, "dalawa pa dapat, isang numero na lang ang kulang"

    await call_balls(client, operator_headers, game["id"], [last])

    body = await state(client, operator_headers, game["id"])
    assert body["survivors"] == 0
    assert body["status"] == "won"

    async with get_session_factory()() as db:
        winners = (
            await db.scalars(
                select(BingoCard).where(
                    BingoCard.round_id == game["id"], BingoCard.is_winner.is_(True)
                )
            )
        ).all()
    assert len(winners) == 2, "sabay natapos, kaya tabla"


async def test_the_round_stops_as_soon_as_it_is_decided(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Hindi hinihintay ang lahat ng 75: pagkapanalo ay sarado na ang draws."""
    game = await make_elimination(client, operator_headers)
    await join(client, operator_headers, game["id"], "Ana")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    called = 0
    for ball in range(1, 76):
        response = await client.post(
            f"/api/rounds/{game['id']}/draw", json={"ball": ball}, headers=operator_headers
        )
        if response.status_code == 409:
            assert "won" in response.json()["detail"]
            break
        assert response.status_code == 200, response.text
        called += 1

    body = await state(client, operator_headers, game["id"])
    assert body["status"] == "won"
    assert body["survivors"] == 0
    # Isang card lang, kaya tapos na pagkatawag sa pinakamataas na numero niya.
    ana = await numbers_of(game["id"], "Ana")
    assert called == max(ana)


# --- Views ----------------------------------------------------------------


async def test_player_sees_a_sorted_list_not_a_grid(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_elimination(client, operator_headers)
    token, numbers = await join(client, operator_headers, game["id"], "Ana")

    page = await client.get(f"/play/{token}")
    assert page.status_code == 200
    assert "You're still in" in page.text
    assert "lowest to highest" in page.text

    # Ang 24 na numero ay nakalista sa pataas na order.
    listed = [
        int(n) for n in re.findall(r'class="survival-number[^"]*"\s+id="number-(\d+)"', page.text)
    ]
    assert listed == numbers
    assert listed == sorted(listed)

    # Walang 5x5 grid, walang FREE, at walang BINGO button.
    assert "FREE" not in page.text
    assert "BINGO!" not in page.text
    assert 'class="dab"' not in page.text


async def test_called_numbers_are_struck_through(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_elimination(client, operator_headers)
    token, numbers = await join(client, operator_headers, game["id"], "Ana")
    await join(client, operator_headers, game["id"], "Ben")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    await call_balls(client, operator_headers, game["id"], numbers[:3])

    page = await client.get(f"/play/{token}")
    for number in numbers[:3]:
        assert f'class="survival-number called"\n        id="number-{number}"' in page.text or (
            f'id="number-{number}"' in page.text and "called" in page.text
        )
    # Ang counter ay nagbabawas.
    assert f">{len(numbers) - 3}</div>" in page.text


async def test_player_page_shows_knocked_out_state(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_elimination(client, operator_headers)
    token, numbers = await join(client, operator_headers, game["id"], "Ana")
    await join(client, operator_headers, game["id"], "Ben")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    await call_balls(client, operator_headers, game["id"], numbers)

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


async def test_caller_roster_shows_numbers_left(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    from tests.conftest import TEST_API_KEY

    await client.post("/operator/session", data={"api_key": TEST_API_KEY})
    game = await make_elimination(client, operator_headers)
    await join(client, operator_headers, game["id"], "Ana")
    await join(client, operator_headers, game["id"], "Ben")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    ana = await numbers_of(game["id"], "Ana")
    await call_balls(client, operator_headers, game["id"], ana[:5])

    page = await client.get(f"/caller/{game['id']}")
    assert page.status_code == 200
    assert "Who's left" in page.text
    assert 'data-name="Ana"' in page.text
    assert 'data-name="Ben"' in page.text
    assert "left" in page.text
    # Walang pattern na binabanggit: hindi bagay sa elimination.
    assert "Pattern:" not in page.text


async def test_classic_rounds_are_untouched(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    from tests.test_rounds_api import cards_of, make_round
    from tests.test_rounds_api import join as classic_join

    game = await make_round(client, operator_headers, "four_corners")
    assert game["game_type"] == "classic"
    token = await classic_join(client, operator_headers, game["id"], card_count=2)

    cards = await cards_of(token)
    assert len(cards) == 2
    page = await client.get(f"/play/{token}")
    assert "BINGO!" in page.text
    assert 'class="dab"' in page.text

    async with get_session_factory()() as db:
        refreshed = await db.scalar(select(GameRound).where(GameRound.id == game["id"]))
    assert refreshed is not None
    assert refreshed.game_type == "classic"
    for card in cards:
        assert card.eliminated_at is None
        assert card.is_winner is False
