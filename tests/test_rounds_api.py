"""Draw engine end-to-end: round lifecycle, draws, at BINGO verification."""

from __future__ import annotations

import asyncio
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.db.models import BingoCard, Claim, Draw, GameRound, Player
from app.db.session import get_session_factory
from app.domain.draws import TOTAL_BALLS
from app.domain.patterns import FREE_INDEX


async def make_round(
    client: AsyncClient, headers: dict[str, str], pattern: str = "any_line"
) -> dict[str, Any]:
    response = await client.post(
        "/api/rounds", json={"pattern": pattern, "label": "test"}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


async def join(
    client: AsyncClient,
    headers: dict[str, str],
    round_id: str,
    *,
    name: str = "Ana",
    card_count: int = 1,
) -> str:
    """Sumali sa round via QR pairing. Ibinabalik ang play token."""
    created = await client.post(
        "/api/pairing",
        json={"round_id": round_id, "card_count": card_count},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    nonce = created.json()["pair_url"].rsplit("/", 1)[-1]

    claimed = await client.post(f"/pair/{nonce}/claim", data={"given_name": name})
    assert claimed.status_code == 200, claimed.status_code

    async with get_session_factory()() as db:
        player = await db.scalar(select(Player).where(Player.given_name == name))
    assert player is not None
    return player.play_token


async def cards_of(token: str) -> list[BingoCard]:
    async with get_session_factory()() as db:
        player = await db.scalar(select(Player).where(Player.play_token == token))
        assert player is not None
        result = await db.scalars(select(BingoCard).where(BingoCard.player_id == player.id))
        return list(result)


async def force_draws(round_id: str, balls: list[int]) -> None:
    """Ilagay ang eksaktong bola sa draw table para deterministic ang test."""
    from app.services.identity import utcnow

    async with get_session_factory()() as db:
        existing = await db.scalar(select(Draw).where(Draw.round_id == round_id))
        offset = 0 if existing is None else 1000
        for index, ball in enumerate(balls, start=1):
            db.add(
                Draw(
                    round_id=round_id,
                    sequence_no=offset + index,
                    ball=ball,
                    drawn_at=utcnow(),
                )
            )
        await db.commit()


# --- Round lifecycle -------------------------------------------------------


async def test_creating_a_round_gives_a_join_code(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_round(client, operator_headers, "four_corners")
    assert len(game["join_code"]) == 6
    assert game["status"] == "open"
    assert game["pattern"] == "four_corners"
    assert game["draw_count"] == 0
    assert game["remaining"] == TOTAL_BALLS
    assert game["last_call"] is None


async def test_round_endpoints_require_operator_auth(client: AsyncClient) -> None:
    assert (await client.post("/api/rounds", json={})).status_code == 401
    assert (await client.post("/api/rounds/x/start")).status_code == 401
    assert (await client.post("/api/rounds/x/draw")).status_code == 401


async def test_unknown_pattern_is_rejected(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/rounds", json={"pattern": "letter_z"}, headers=operator_headers
    )
    assert response.status_code == 422


async def test_cannot_draw_before_the_round_starts(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_round(client, operator_headers)
    response = await client.post(f"/api/rounds/{game['id']}/draw", headers=operator_headers)
    assert response.status_code == 409

    async with get_session_factory()() as db:
        assert (await db.scalars(select(Draw))).all() == []


async def test_starting_twice_is_refused(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_round(client, operator_headers)
    first = await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)
    assert first.status_code == 200
    assert first.json()["status"] == "drawing"

    second = await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)
    assert second.status_code == 409


async def test_joining_is_closed_once_drawing_starts(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_round(client, operator_headers)
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    response = await client.post(
        "/api/pairing", json={"round_id": game["id"]}, headers=operator_headers
    )
    assert response.status_code == 409


async def test_pairing_for_unknown_round_is_404(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/pairing", json={"round_id": "wala"}, headers=operator_headers
    )
    assert response.status_code == 404


# --- Draws -----------------------------------------------------------------


async def test_draws_never_repeat_a_ball(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_round(client, operator_headers)
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    balls = []
    for expected_sequence in range(1, 41):
        response = await client.post(f"/api/rounds/{game['id']}/draw", headers=operator_headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["sequence_no"] == expected_sequence
        assert body["remaining"] == TOTAL_BALLS - expected_sequence
        assert body["call"].split("-")[1] == str(body["ball"])
        balls.append(body["ball"])

    assert len(set(balls)) == 40

    async with get_session_factory()() as db:
        rows = (await db.scalars(select(Draw).where(Draw.round_id == game["id"]))).all()
    assert len(rows) == 40
    assert sorted(row.sequence_no for row in rows) == list(range(1, 41))


async def test_draining_the_pool_then_drawing_again_is_refused(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_round(client, operator_headers)
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)
    await force_draws(game["id"], list(range(1, TOTAL_BALLS + 1)))

    response = await client.post(f"/api/rounds/{game['id']}/draw", headers=operator_headers)
    assert response.status_code == 409


async def test_concurrent_draws_do_not_collide(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_round(client, operator_headers)
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    results = await asyncio.gather(
        *(
            client.post(f"/api/rounds/{game['id']}/draw", headers=operator_headers)
            for _ in range(5)
        ),
        return_exceptions=True,
    )
    ok = [r for r in results if not isinstance(r, BaseException) and r.status_code == 200]

    async with get_session_factory()() as db:
        rows = (await db.scalars(select(Draw).where(Draw.round_id == game["id"]))).all()

    balls = [row.ball for row in rows]
    sequences = [row.sequence_no for row in rows]
    assert len(balls) == len(set(balls)), "walang bola na dapat maulit"
    assert len(sequences) == len(set(sequences)), "walang posisyon na dapat maulit"
    assert len(rows) == len(ok)


async def test_round_snapshot_tracks_players_and_cards(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_round(client, operator_headers)
    await join(client, operator_headers, game["id"], name="Ana", card_count=2)
    await join(client, operator_headers, game["id"], name="Ben", card_count=1)

    response = await client.get(f"/api/rounds/{game['id']}", headers=operator_headers)
    body = response.json()
    assert body["player_count"] == 2
    assert body["card_count"] == 3


# --- BINGO verification ----------------------------------------------------


async def test_valid_bingo_is_accepted_and_closes_the_round(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_round(client, operator_headers, "row_3")
    token = await join(client, operator_headers, game["id"])
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    cards = await cards_of(token)
    middle_row = [
        value
        for index, value in enumerate(cards[0].numbers)
        if 10 <= index <= 14 and index != FREE_INDEX
    ]
    await force_draws(game["id"], middle_row)

    response = await client.post(f"/api/play/{token}/bingo")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verified"] is True
    assert body["is_first_winner"] is True
    assert body["card_serial"] == cards[0].serial
    assert "BINGO" in body["message"]

    async with get_session_factory()() as db:
        refreshed = await db.scalar(select(GameRound).where(GameRound.id == game["id"]))
    assert refreshed is not None
    assert refreshed.status == "won"
    assert refreshed.first_winner_card_id == cards[0].id
    assert refreshed.winning_draw_count == len(middle_row)


async def test_false_bingo_is_rejected_and_recorded(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_round(client, operator_headers, "blackout")
    token = await join(client, operator_headers, game["id"])
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    cards = await cards_of(token)
    await force_draws(game["id"], [v for v in cards[0].numbers if v][:5])

    response = await client.post(f"/api/play/{token}/bingo")
    body = response.json()
    assert body["verified"] is False
    assert body["reason"] == "pattern_incomplete"
    assert body["missing"] > 0

    async with get_session_factory()() as db:
        claims = (await db.scalars(select(Claim))).all()
        refreshed = await db.scalar(select(GameRound).where(GameRound.id == game["id"]))
    # Ang maling claim ay naka-record pero hindi nagsasara ng round.
    assert len(claims) == 1
    assert claims[0].verified is False
    assert refreshed is not None
    assert refreshed.status == "drawing"


async def test_one_away_message_when_a_single_number_is_short(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_round(client, operator_headers, "row_3")
    token = await join(client, operator_headers, game["id"])
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    cards = await cards_of(token)
    middle_row = [
        value
        for index, value in enumerate(cards[0].numbers)
        if 10 <= index <= 14 and index != FREE_INDEX
    ]
    await force_draws(game["id"], middle_row[:-1])

    body = (await client.post(f"/api/play/{token}/bingo")).json()
    assert body["verified"] is False
    assert body["missing"] == 1
    assert "One away" in body["message"]


async def test_claim_before_start_is_refused(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_round(client, operator_headers, "row_3")
    token = await join(client, operator_headers, game["id"])

    body = (await client.post(f"/api/play/{token}/bingo")).json()
    assert body["verified"] is False
    assert body["reason"] == "round_not_drawing"


async def test_co_winner_on_the_same_ball_is_still_a_winner(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_round(client, operator_headers, "four_corners")
    ana = await join(client, operator_headers, game["id"], name="Ana")
    ben = await join(client, operator_headers, game["id"], name="Ben")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    ana_cards = await cards_of(ana)
    ben_cards = await cards_of(ben)
    corners = {ana_cards[0].numbers[i] for i in (0, 4, 20, 24)}
    corners |= {ben_cards[0].numbers[i] for i in (0, 4, 20, 24)}
    await force_draws(game["id"], sorted(corners))

    first = (await client.post(f"/api/play/{ana}/bingo")).json()
    second = (await client.post(f"/api/play/{ben}/bingo")).json()

    assert first["verified"] is True
    assert first["is_first_winner"] is True
    # Parehong bilang ng bola, kaya co-winner pa rin — hindi "late".
    assert second["verified"] is True
    assert second["is_first_winner"] is False
    assert second["reason"] == "valid"


async def test_unknown_play_token_is_404(client: AsyncClient) -> None:
    assert (await client.get("/play/wala")).status_code == 404
    assert (await client.post("/api/play/wala/bingo")).status_code == 404


async def test_play_board_renders_the_stored_cards(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_round(client, operator_headers, "letter_x")
    token = await join(client, operator_headers, game["id"], card_count=2)

    page = await client.get(f"/play/{token}")
    assert page.status_code == 200
    cards = await cards_of(token)
    for card in cards:
        assert card.serial in page.text
    assert "BINGO!" in page.text
    assert "letter x" in page.text


async def test_play_board_cells_are_tappable_buttons(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Bawat numero ay button para puwedeng i-dab nang manual at keyboard-accessible."""
    game = await make_round(client, operator_headers, "any_line")
    token = await join(client, operator_headers, game["id"], card_count=1)

    page = await client.get(f"/play/{token}")
    cards = await cards_of(token)
    numbers = [value for value in cards[0].numbers if value]

    # 24 na numero kada card, ang FREE center ay hindi pinipindot.
    assert len(numbers) == 24
    assert page.text.count('class="dab"') == 24
    assert page.text.count('aria-pressed="false"') == 24
    for value in numbers:
        assert f'data-ball="{value}"' in page.text

    # Ang auto-daub toggle ay naka-on sa simula.
    assert 'id="auto-daub"' in page.text
    assert "Turn this off to tap them yourself" in page.text
