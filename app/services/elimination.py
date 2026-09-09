"""Elimination round: buong bingo card, at labas ka kapag nalahat ang numero mo.

Parehong 5x5 card gaya ng classic. Ang pinagkaiba ay ang panalo: hindi ito
unahan sa pattern kundi ang huling natirang may numerong hindi pa natawag.

Bakit hindi puwede ang "labas sa unang tama": 24 na numero sa 75 na bola, kaya
32% ng bisita ay labas na pagkatapos ng ISANG bola at 86% pagkatapos ng lima.
Sa ganitong rule, halos buong pool ang nagagamit at kasali ang lahat hanggang
malapit sa dulo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ROUND_WON, BingoCard, GameRound, Player
from app.services.identity import utcnow


@dataclass(frozen=True, slots=True)
class Knockout:
    player_name: str
    card_id: str
    card_serial: str


@dataclass(frozen=True, slots=True)
class DrawEffect:
    """Ang naging bunga ng isang bola sa elimination round."""

    knocked_out: list[Knockout]
    survivors: int
    winners: list[str]


def card_numbers(card: BingoCard) -> set[int]:
    """Ang 24 na numero ng card. Hindi kasama ang FREE center."""
    return {value for value in card.numbers if value}


def numbers_left(card: BingoCard, drawn: set[int]) -> int:
    return len(card_numbers(card) - drawn)


async def live_cards(db: AsyncSession, round_id: str) -> list[BingoCard]:
    result = await db.scalars(
        select(BingoCard).where(BingoCard.round_id == round_id, BingoCard.eliminated_at.is_(None))
    )
    return list(result)


async def apply_draw(
    db: AsyncSession,
    *,
    game: GameRound,
    drawn: set[int],
    draw_count: int,
) -> DrawEffect:
    """Tanggalin ang mga card na nakumpleto na, tapos tingnan kung may panalo.

    Tinatawag pagkatapos maitala ang bola, kaya pareho ang gawi kung ang app ang
    bumunot o kung ang host ang nagpasok ng numero mula sa tambiolo niya.
    """
    now = utcnow()
    knocked_out: list[Knockout] = []

    for card in await live_cards(db, game.id):
        if card_numbers(card) - drawn:
            continue

        # Ang `eliminated_at IS NULL` na guard ay nasa WHERE, kaya hindi
        # nababago ang naunang pagtanggal kung sakaling maulit ang pagproseso.
        result = await db.execute(
            update(BingoCard)
            .where(BingoCard.id == card.id, BingoCard.eliminated_at.is_(None))
            .values(eliminated_at=now, eliminated_at_draw=draw_count)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            continue

        player = await db.scalar(select(Player).where(Player.id == card.player_id))
        knocked_out.append(
            Knockout(
                player_name=player.given_name if player else "?",
                card_id=card.id,
                card_serial=card.serial,
            )
        )

    survivors = await count_survivors(db, game.id)
    winners = await _settle(db, game=game, survivors=survivors, knocked_out=knocked_out, at=now)
    await db.commit()

    return DrawEffect(knocked_out=knocked_out, survivors=survivors, winners=winners)


async def _settle(
    db: AsyncSession,
    *,
    game: GameRound,
    survivors: int,
    knocked_out: list[Knockout],
    at: datetime,
) -> list[str]:
    """Ideklara ang panalo kung tapos na. Ibinabalik ang mga pangalan."""
    # Walang natanggal, walang nagbago, kaya walang idedeklara. Kung wala ang
    # guard na ito, ang round na isang player lang ay agad na mananalo sa unang
    # bola — "huling natira" na kaagad siya kahit hindi pa nalalaro.
    if not knocked_out:
        return []
    if await count_cards(db, game.id) == 0:  # pragma: no cover - FK ang pumipigil
        return []

    if survivors == 1:
        card = await db.scalar(
            select(BingoCard).where(
                BingoCard.round_id == game.id, BingoCard.eliminated_at.is_(None)
            )
        )
        if card is None:  # pragma: no cover - nabago sa pagitan ng dalawang query
            return []
        winning_ids = [card.id]
    elif survivors == 0 and knocked_out:
        # Sabay na nakumpleto ang huling mga card. Tabla sila. Hindi ito bihira
        # dito: sa dulo ng laro ay isa o dalawang numero na lang ang natitira sa
        # lahat, kaya kayang tapusin ng isang bola ang maraming card nang sabay.
        winning_ids = [knock.card_id for knock in knocked_out]
    else:
        return []

    await db.execute(
        update(BingoCard)
        .where(BingoCard.id.in_(winning_ids))
        .values(is_winner=True)
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        update(GameRound)
        .where(GameRound.id == game.id, GameRound.status != ROUND_WON)
        .values(status=ROUND_WON, ended_at=at, winning_draw_count=None)
        .execution_options(synchronize_session=False)
    )

    names: list[str] = []
    for card_id in winning_ids:
        card = await db.scalar(select(BingoCard).where(BingoCard.id == card_id))
        if card is None:  # pragma: no cover
            continue
        player = await db.scalar(select(Player).where(Player.id == card.player_id))
        names.append(player.given_name if player else "?")
    return names


async def count_cards(db: AsyncSession, round_id: str) -> int:
    total = await db.scalar(select(func.count(BingoCard.id)).where(BingoCard.round_id == round_id))
    return total or 0


async def count_survivors(db: AsyncSession, round_id: str) -> int:
    total = await db.scalar(
        select(func.count(BingoCard.id)).where(
            BingoCard.round_id == round_id, BingoCard.eliminated_at.is_(None)
        )
    )
    return total or 0


async def roster(db: AsyncSession, round_id: str, drawn: set[int]) -> list[dict[str, object]]:
    """Sino ang natira, ilan pa ang kulang, at sino ang labas na."""
    rows = await db.execute(
        select(BingoCard, Player.given_name)
        .join(Player, Player.id == BingoCard.player_id)
        .where(BingoCard.round_id == round_id)
        .order_by(BingoCard.created_at)
    )

    entries: list[dict[str, object]] = []
    for card, name in rows.all():
        entries.append(
            {
                "player_name": name,
                "serial": card.serial,
                "left": numbers_left(card, drawn),
                "out": card.eliminated_at is not None,
                "out_at_draw": card.eliminated_at_draw,
                "is_winner": card.is_winner,
            }
        )

    # Ang malapit nang matapos ay nasa taas, tapos ang mga labas na.
    entries.sort(key=lambda entry: (bool(entry["out"]), entry["left"]))
    return entries
