"""Game history: ano ang nangyari sa mga nakaraang round.

Walang bagong table na kailangan dito. Ang lahat ay nasa `GameRound`, `Draw`,
`BingoCard`, at `Claim` na — ito lang ang nagbubuo ng mababasang salaysay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CLAIM_ANNOUNCED,
    GAME_ELIMINATION,
    BingoCard,
    Claim,
    Draw,
    GameRound,
    Player,
)
from app.domain.draws import format_call


@dataclass(frozen=True, slots=True)
class RoundSummary:
    id: str
    join_code: str
    label: str
    game_type: str
    pattern: str
    caller_mode: str
    status: str
    created_at: datetime
    ended_at: datetime | None
    draw_count: int
    player_count: int
    winners: list[str]
    bingo_calls: int
    duration_seconds: int | None


@dataclass(frozen=True, slots=True)
class PlayerLine:
    name: str
    serial: str
    is_winner: bool
    out_at_draw: int | None
    numbers: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ClaimLine:
    player_name: str
    serial: str
    at_draw: int
    verified: bool
    reason: str
    missing: int
    is_first_winner: bool
    reported_marks: list[int]


@dataclass(frozen=True, slots=True)
class RoundDetail:
    summary: RoundSummary
    calls: list[str]
    players: list[PlayerLine]
    claims: list[ClaimLine]


async def _winner_names(db: AsyncSession, game: GameRound) -> list[str]:
    """Sino ang nanalo. Iba ang pinagmumulan depende sa laro."""
    if game.game_type == GAME_ELIMINATION:
        rows = await db.execute(
            select(Player.given_name)
            .join(BingoCard, BingoCard.player_id == Player.id)
            .where(BingoCard.round_id == game.id, BingoCard.is_winner.is_(True))
        )
        return sorted({name for (name,) in rows.all()})

    rows = await db.execute(
        select(Player.given_name)
        .join(Claim, Claim.player_id == Player.id)
        .where(Claim.round_id == game.id, Claim.verified.is_(True))
    )
    return sorted({name for (name,) in rows.all()})


async def _count(db: AsyncSession, statement: object) -> int:
    total = await db.scalar(statement)  # type: ignore[arg-type]
    return total or 0


async def summarise(db: AsyncSession, game: GameRound) -> RoundSummary:
    draw_count = await _count(db, select(func.count(Draw.id)).where(Draw.round_id == game.id))
    player_count = await _count(
        db,
        select(func.count(func.distinct(BingoCard.player_id))).where(BingoCard.round_id == game.id),
    )
    bingo_calls = await _count(db, select(func.count(Claim.id)).where(Claim.round_id == game.id))

    duration: int | None = None
    if game.started_at and game.ended_at:
        duration = int((game.ended_at - game.started_at).total_seconds())

    return RoundSummary(
        id=game.id,
        join_code=game.join_code,
        label=game.label,
        game_type=game.game_type,
        pattern=game.pattern,
        caller_mode=game.caller_mode,
        status=game.status,
        created_at=game.created_at,
        ended_at=game.ended_at,
        draw_count=draw_count,
        player_count=player_count,
        winners=await _winner_names(db, game),
        bingo_calls=bingo_calls,
        duration_seconds=duration,
    )


async def recent_rounds(db: AsyncSession, limit: int = 50) -> list[RoundSummary]:
    """Ang mga round, pinakabago sa taas."""
    rounds = await db.scalars(select(GameRound).order_by(GameRound.created_at.desc()).limit(limit))
    return [await summarise(db, game) for game in rounds]


async def round_detail(db: AsyncSession, round_id: str) -> RoundDetail | None:
    game = await db.scalar(select(GameRound).where(GameRound.id == round_id))
    if game is None:
        return None

    balls = list(
        await db.scalars(
            select(Draw.ball).where(Draw.round_id == round_id).order_by(Draw.sequence_no)
        )
    )

    card_rows = await db.execute(
        select(BingoCard, Player.given_name)
        .join(Player, Player.id == BingoCard.player_id)
        .where(BingoCard.round_id == round_id)
        .order_by(BingoCard.created_at)
    )
    players = [
        PlayerLine(
            name=name,
            serial=card.serial,
            is_winner=card.is_winner,
            out_at_draw=card.eliminated_at_draw,
            numbers=sorted(value for value in card.numbers if value),
        )
        for card, name in card_rows.all()
    ]

    claim_rows = await db.execute(
        select(Claim, Player.given_name, BingoCard.serial)
        .join(Player, Player.id == Claim.player_id)
        .join(BingoCard, BingoCard.id == Claim.card_id)
        .where(Claim.round_id == round_id)
        .order_by(Claim.claimed_at)
    )
    claims = [
        ClaimLine(
            player_name=name,
            serial=serial,
            at_draw=claim.draw_count,
            verified=claim.verified,
            reason=claim.reason,
            missing=claim.missing_cells,
            is_first_winner=claim.is_first_winner,
            reported_marks=list(claim.reported_marks or []),
        )
        for claim, name, serial in claim_rows.all()
    ]

    return RoundDetail(
        summary=await summarise(db, game),
        calls=[format_call(ball) for ball in balls],
        players=players,
        claims=claims,
    )


def outcome_line(summary: RoundSummary) -> str:
    """Isang pangungusap na nagsasabi kung anong nangyari."""
    if summary.winners:
        names = " and ".join(summary.winners)
        if summary.game_type == GAME_ELIMINATION:
            return f"{names} survived to ball {summary.draw_count}"
        return f"{names} won on ball {summary.draw_count}"

    if summary.status == "won":  # pragma: no cover - panalo pero walang pangalan
        return "Won"
    if summary.caller_mode == "offline" and summary.bingo_calls:
        return f"{summary.bingo_calls} BINGO called, decided by the host"
    if summary.status == "drawing":
        return "In progress"
    if summary.status == "open":
        return "Never started"
    return "No winner"


def reason_label(reason: str) -> str:
    return {
        CLAIM_ANNOUNCED: "announced for the host to check",
        "valid": "verified win",
        "pattern_incomplete": "pattern not complete",
        "round_not_drawing": "round was not running",
        "late": "someone already won",
    }.get(reason, reason)
