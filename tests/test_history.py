"""Game history at ang inaangkin na marka sa cards-only mode."""

from __future__ import annotations

import re
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.db.models import Claim
from app.db.session import get_session_factory
from tests.conftest import TEST_API_KEY


async def sign_in(client: AsyncClient) -> None:
    await client.post("/operator/session", data={"api_key": TEST_API_KEY})


async def make_round(
    client: AsyncClient,
    headers: dict[str, str],
    **payload: Any,
) -> dict[str, Any]:
    response = await client.post("/api/rounds", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def join(client: AsyncClient, headers: dict[str, str], round_id: str, name: str) -> str:
    created = await client.post(
        "/api/pairing", json={"round_id": round_id, "card_count": 1}, headers=headers
    )
    assert created.status_code == 200, created.text
    nonce = created.json()["pair_url"].rsplit("/", 1)[-1]
    claimed = await client.post(f"/pair/{nonce}/claim", data={"given_name": name})
    assert claimed.status_code == 303
    return claimed.headers["location"].split("/play/")[1].split("?")[0]


# --- Access ---------------------------------------------------------------


async def test_history_needs_a_host_session(client: AsyncClient) -> None:
    for path in ("/history", "/history/anything"):
        response = await client.get(path)
        assert response.status_code == 303
        assert response.headers["location"] == "/operator"


async def test_empty_history_says_so(client: AsyncClient) -> None:
    await sign_in(client)
    page = await client.get("/history")
    assert page.status_code == 200
    assert "No rounds yet" in page.text


async def test_unknown_round_is_404(client: AsyncClient) -> None:
    await sign_in(client)
    assert (await client.get("/history/wala")).status_code == 404


# --- Listing --------------------------------------------------------------


async def test_rounds_are_listed_newest_first(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    await sign_in(client)
    first = await make_round(client, operator_headers, pattern="any_line")
    second = await make_round(client, operator_headers, pattern="blackout")

    page = await client.get("/history")
    assert page.status_code == 200
    positions = [
        page.text.index(first["join_code"]),
        page.text.index(second["join_code"]),
    ]
    assert positions[1] < positions[0], "ang pinakabago ay dapat nasa taas"


async def test_a_never_started_round_says_so(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    await sign_in(client)
    await make_round(client, operator_headers)
    page = await client.get("/history")
    assert "Never started" in page.text


# --- Classic round --------------------------------------------------------


async def test_classic_history_records_the_win(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    from tests.test_rounds_api import cards_of, force_draws

    await sign_in(client)
    game = await make_round(client, operator_headers, pattern="row_3", caller_mode="manual")
    token = await join(client, operator_headers, game["id"], "Ana")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    cards = await cards_of(token)
    middle = [v for i, v in enumerate(cards[0].numbers) if 10 <= i <= 14 and v]
    await force_draws(game["id"], middle)
    won = await client.post(f"/api/play/{token}/bingo")
    assert won.json()["verified"] is True

    listing = await client.get("/history")
    assert "Ana won on ball" in listing.text

    detail = await client.get(f"/history/{game['id']}")
    assert detail.status_code == 200
    assert "Ana" in detail.text
    assert "verified win" in detail.text
    assert "row 3" in detail.text
    assert "the host&#39;s own machine, entered in" in detail.text or "entered in" in detail.text


async def test_detail_lists_the_calls_in_order(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    await sign_in(client)
    game = await make_round(client, operator_headers, caller_mode="manual")
    await join(client, operator_headers, game["id"], "Ana")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    for ball in (7, 30, 61):
        await client.post(
            f"/api/rounds/{game['id']}/draw", json={"ball": ball}, headers=operator_headers
        )

    detail = await client.get(f"/history/{game['id']}")
    calls = re.findall(r"<li>([BINGO]-\d+)</li>", detail.text)
    assert calls == ["B-7", "I-30", "O-61"], calls


async def test_a_rejected_claim_is_recorded(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Ang maling claim ay nasa history rin, para may makitang pattern."""
    await sign_in(client)
    game = await make_round(client, operator_headers, pattern="blackout", caller_mode="manual")
    token = await join(client, operator_headers, game["id"], "Ana")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    rejected = await client.post(f"/api/play/{token}/bingo")
    assert rejected.json()["verified"] is False

    detail = await client.get(f"/history/{game['id']}")
    assert "pattern not complete" in detail.text
    assert "short" in detail.text


# --- Cards-only mode ------------------------------------------------------


async def test_cards_only_bingo_is_recorded_with_the_claimed_marks(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Walang binibilang ang server, kaya ang inaangkin ang naitatala."""
    await sign_in(client)
    game = await make_round(client, operator_headers, caller_mode="offline")
    token = await join(client, operator_headers, game["id"], "Tita Baby")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    response = await client.post(
        f"/api/play/{token}/bingo", json={"marked": [5, 5, 12, 40, 999, 0, 71]}
    )
    body = response.json()
    assert body["reason"] == "announced"

    async with get_session_factory()() as db:
        claim = await db.scalar(select(Claim).where(Claim.round_id == game["id"]))
    assert claim is not None
    # Naka-sort, walang duplicate, at tinanggal ang wala sa 1-75.
    assert claim.reported_marks == [5, 12, 40, 71]

    detail = await client.get(f"/history/{game['id']}")
    assert "announced for the host to check" in detail.text
    assert "5, 12, 40, 71" in detail.text

    listing = await client.get("/history")
    assert "decided by the host" in listing.text


async def test_tracked_modes_ignore_reported_marks(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """May draw table na basehan doon, kaya walang pakialam sa sabi ng phone."""
    await sign_in(client)
    game = await make_round(client, operator_headers, pattern="blackout", caller_mode="manual")
    token = await join(client, operator_headers, game["id"], "Ana")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    response = await client.post(f"/api/play/{token}/bingo", json={"marked": list(range(1, 76))})
    # Sinabi niyang lahat ay marked, pero walang nailabas na bola.
    assert response.json()["verified"] is False

    async with get_session_factory()() as db:
        claim = await db.scalar(select(Claim).where(Claim.round_id == game["id"]))
    assert claim is not None
    assert claim.reported_marks == []


async def test_cards_only_detail_notes_that_numbers_were_not_tracked(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    await sign_in(client)
    game = await make_round(client, operator_headers, caller_mode="offline")
    await join(client, operator_headers, game["id"], "Ana")

    detail = await client.get(f"/history/{game['id']}")
    assert "did not track numbers" in detail.text


# --- Elimination ----------------------------------------------------------


async def test_elimination_history_records_the_survivor(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    from tests.test_elimination import numbers_of

    await sign_in(client)
    game = await make_round(client, operator_headers, game_type="elimination", caller_mode="manual")
    await join(client, operator_headers, game["id"], "Ana")
    await join(client, operator_headers, game["id"], "Ben")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    ana = set(await numbers_of(game["id"], "Ana"))
    ben = set(await numbers_of(game["id"], "Ben"))
    ben_only = ben - ana
    if not ben_only:
        return

    for ball in sorted(ana - ben_only):
        await client.post(
            f"/api/rounds/{game['id']}/draw", json={"ball": ball}, headers=operator_headers
        )

    listing = await client.get("/history")
    assert "Ben survived to ball" in listing.text

    detail = await client.get(f"/history/{game['id']}")
    assert "out at ball" in detail.text
    assert "no BINGO button" in detail.text


async def test_history_is_linked_from_the_lobby_and_caller(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    await sign_in(client)
    game = await make_round(client, operator_headers)

    lobby = await client.get("/kiosk")
    assert 'href="/history"' in lobby.text

    caller = await client.get(f"/caller/{game['id']}")
    assert f'href="/history/{game["id"]}"' in caller.text
