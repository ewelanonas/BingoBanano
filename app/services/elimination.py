"""Elimination round: may hawak na numero ang bisita, at labas siya kapag natawag.

Ang huling natira ang panalo. Kapag sabay na natanggal ang huling mga natira sa
isang bola, magkatabla sila — ganito rin ang gawi sa totoong party.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ROUND_WON,
    EliminationTicket,
    GameRound,
    Player,
    TicketNumber,
)
from app.domain.draws import ALL_BALLS, format_call
from app.domain.rng import Randomizer
from app.services.identity import utcnow

_MAX_NUMBER_ATTEMPTS = 8


class EliminationError(Exception):
    """Hindi kayang gawin ang hinihiling sa elimination round."""


@dataclass(frozen=True, slots=True)
class Knockout:
    player_name: str
    ticket_id: str
    numbers: list[int]


@dataclass(frozen=True, slots=True)
class DrawEffect:
    """Ang naging bunga ng isang bola sa elimination round."""

    knocked_out: list[Knockout]
    survivors: int
    winners: list[str]


async def taken_numbers(db: AsyncSession, round_id: str) -> set[int]:
    result = await db.scalars(select(TicketNumber.number).where(TicketNumber.round_id == round_id))
    return set(result)


async def issue_ticket(
    db: AsyncSession,
    *,
    game: GameRound,
    player: Player,
    rng: Randomizer,
) -> list[int]:
    """Bigyan ng numero ang bisita. Ibinabalik ang mga numerong nakuha niya.

    Ang unique constraint sa `(round_id, number)` ang nagsasalba dito: kapag may
    sabay na sumali at nagkasabay ng numero, babagsak ang pangalawa at pipili ng
    iba kaysa magbigay ng dobleng numero.
    """
    wanted = game.numbers_per_ticket
    now = utcnow()

    ticket = EliminationTicket(round_id=game.id, player_id=player.id, created_at=now)
    db.add(ticket)
    await db.flush()

    assigned: list[int] = []
    for _ in range(wanted):
        number = await _claim_one_number(db, game=game, ticket_id=ticket.id, rng=rng)
        assigned.append(number)

    assigned.sort()
    return assigned


async def _claim_one_number(
    db: AsyncSession,
    *,
    game: GameRound,
    ticket_id: str,
    rng: Randomizer,
) -> int:
    for _ in range(_MAX_NUMBER_ATTEMPTS):
        pool = sorted(ALL_BALLS - await taken_numbers(db, game.id))
        if not pool:
            raise EliminationError(
                "Every number from 1 to 75 is already taken, so nobody else can join. "
                "Start the round, or use fewer numbers per guest."
            )

        candidate = rng.sample(pool, 1)[0]
        savepoint = await db.begin_nested()
        db.add(TicketNumber(round_id=game.id, ticket_id=ticket_id, number=candidate))
        try:
            await savepoint.commit()
        except IntegrityError:
            # Nakuha na ng iba habang tayo ay pumipili. Pumili ng iba.
            await savepoint.rollback()
            continue
        return candidate

    raise EliminationError("Could not reserve a free number after several attempts.")


async def numbers_for_ticket(db: AsyncSession, ticket_id: str) -> list[int]:
    result = await db.scalars(
        select(TicketNumber.number)
        .where(TicketNumber.ticket_id == ticket_id)
        .order_by(TicketNumber.number)
    )
    return list(result)


async def ticket_for_player(
    db: AsyncSession, *, round_id: str, player_id: str
) -> EliminationTicket | None:
    return await db.scalar(
        select(EliminationTicket).where(
            EliminationTicket.round_id == round_id,
            EliminationTicket.player_id == player_id,
        )
    )


async def apply_draw(
    db: AsyncSession,
    *,
    game: GameRound,
    ball: int,
    draw_count: int,
) -> DrawEffect:
    """Tanggalin ang may hawak ng bolang lumabas, tapos tingnan kung may panalo na.

    Tinatawag ito pagkatapos maitala ang bola, kaya pareho ang gawi sa auto at sa
    manual na mode.
    """
    now = utcnow()

    ticket_ids = list(
        await db.scalars(
            select(TicketNumber.ticket_id).where(
                TicketNumber.round_id == game.id, TicketNumber.number == ball
            )
        )
    )

    # Dahil unique ang `(round_id, number)`, isa lang talaga ang laman nito.
    # List pa rin ang hawak para hindi masira kung sakaling payagan ang
    # pagkakahati ng numero sa hinaharap.
    knocked_out: list[Knockout] = []
    for ticket_id in ticket_ids:
        # Ang `eliminated_at IS NULL` na guard ay nasa WHERE, kaya hindi
        # mababago ang naunang eliminasyon kung sakaling maulit ang draw.
        result = await db.execute(
            update(EliminationTicket)
            .where(
                EliminationTicket.id == ticket_id,
                EliminationTicket.eliminated_at.is_(None),
            )
            .values(eliminated_at=now, eliminated_by_ball=ball, eliminated_at_draw=draw_count)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            continue

        ticket = await db.scalar(select(EliminationTicket).where(EliminationTicket.id == ticket_id))
        player = await db.scalar(select(Player).where(Player.id == ticket.player_id))  # type: ignore[union-attr]
        knocked_out.append(
            Knockout(
                player_name=player.given_name if player else "?",
                ticket_id=ticket_id,
                numbers=await numbers_for_ticket(db, ticket_id),
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
    """Ideklara ang panalo kung tapos na ang laro. Ibinabalik ang mga pangalan."""
    total = await count_tickets(db, game.id)
    if total == 0:
        return []

    if survivors == 1:
        ticket = await db.scalar(
            select(EliminationTicket).where(
                EliminationTicket.round_id == game.id,
                EliminationTicket.eliminated_at.is_(None),
            )
        )
        if ticket is None:  # pragma: no cover - nabago sa pagitan ng dalawang query
            return []
        winners = [ticket.id]
    elif survivors == 0 and knocked_out:
        # Ang huling natira ang natawag. Siya ang pinakamatagal na tumagal, kaya
        # panalo pa rin siya — wala nang ibang makakapanalo pagkatapos niya.
        winners = [knock.ticket_id for knock in knocked_out]
    else:
        return []

    await db.execute(
        update(EliminationTicket)
        .where(EliminationTicket.id.in_(winners))
        .values(is_winner=True)
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        update(GameRound)
        .where(GameRound.id == game.id, GameRound.status != ROUND_WON)
        .values(status=ROUND_WON, ended_at=at)
        .execution_options(synchronize_session=False)
    )

    names: list[str] = []
    for ticket_id in winners:
        ticket = await db.scalar(select(EliminationTicket).where(EliminationTicket.id == ticket_id))
        if ticket is None:  # pragma: no cover
            continue
        player = await db.scalar(select(Player).where(Player.id == ticket.player_id))
        names.append(player.given_name if player else "?")
    return names


async def count_tickets(db: AsyncSession, round_id: str) -> int:
    total = await db.scalar(
        select(func.count(EliminationTicket.id)).where(EliminationTicket.round_id == round_id)
    )
    return total or 0


async def count_survivors(db: AsyncSession, round_id: str) -> int:
    total = await db.scalar(
        select(func.count(EliminationTicket.id)).where(
            EliminationTicket.round_id == round_id,
            EliminationTicket.eliminated_at.is_(None),
        )
    )
    return total or 0


async def roster(db: AsyncSession, round_id: str) -> list[dict[str, object]]:
    """Sino ang natira at sino ang labas na, para sa caller screen."""
    rows = await db.execute(
        select(EliminationTicket, Player.given_name)
        .join(Player, Player.id == EliminationTicket.player_id)
        .where(EliminationTicket.round_id == round_id)
        .order_by(EliminationTicket.created_at)
    )

    entries: list[dict[str, object]] = []
    for ticket, name in rows.all():
        entries.append(
            {
                "player_name": name,
                "numbers": await numbers_for_ticket(db, ticket.id),
                "out": ticket.eliminated_at is not None,
                "out_on": format_call(ticket.eliminated_by_ball)
                if ticket.eliminated_by_ball
                else None,
                "out_at_draw": ticket.eliminated_at_draw,
                "is_winner": ticket.is_winner,
            }
        )

    # Ang natira sa taas, tapos ang huling natanggal.
    entries.sort(key=lambda entry: (bool(entry["out"]), -(entry["out_at_draw"] or 0)))
    return entries
