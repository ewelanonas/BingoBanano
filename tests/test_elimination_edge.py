"""Edge case: hindi dapat manalo ang isang round bago pa ito malaro."""

from __future__ import annotations

from httpx import AsyncClient

from tests.test_elimination import (
    call_balls,
    join,
    make_elimination,
    numbers_of,
    state,
)


async def test_a_solo_round_is_not_won_on_the_first_ball(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Isang player lang: "huling natira" na siya kaagad, pero hindi pa tapos.

    Kung idineklara agad ang panalo, hindi nalalaro ang round.
    """
    game = await make_elimination(client, operator_headers)
    await join(client, operator_headers, game["id"], "Nag-isa")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    numbers = await numbers_of(game["id"], "Nag-isa")
    await call_balls(client, operator_headers, game["id"], numbers[:1])

    body = await state(client, operator_headers, game["id"])
    assert body["status"] == "drawing", "hindi pa dapat tapos pagkatapos ng isang bola"
    assert body["survivors"] == 1

    # Tapos na kapag nakumpleto ang buong card niya.
    await call_balls(client, operator_headers, game["id"], numbers[1:])
    body = await state(client, operator_headers, game["id"])
    assert body["status"] == "won"
    assert body["survivors"] == 0


async def test_drawing_a_number_nobody_holds_never_ends_the_round(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_elimination(client, operator_headers)
    await join(client, operator_headers, game["id"], "Ana")
    await join(client, operator_headers, game["id"], "Ben")
    await client.post(f"/api/rounds/{game['id']}/start", headers=operator_headers)

    held = set(await numbers_of(game["id"], "Ana")) | set(await numbers_of(game["id"], "Ben"))
    spares = [n for n in range(1, 76) if n not in held][:5]
    if not spares:
        return

    await call_balls(client, operator_headers, game["id"], spares)
    body = await state(client, operator_headers, game["id"])
    assert body["status"] == "drawing"
    assert body["survivors"] == 2
