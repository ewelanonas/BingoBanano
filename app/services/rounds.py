"""Round lifecycle, ball draws, at claim verification.

Lahat ng draw at lahat ng win check ay nangyayari dito, sa server. Ang client ay
nagre-render lang. Ang pinindot na BINGO ay isang request lang — ang server ang
nagpapasya kung panalo.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CALLER_AUTO,
    CALLER_MANUAL,
    CALLER_MODES,
    CALLER_OFFLINE,
    CLAIM_ANNOUNCED,
    CLAIM_LATE,
    CLAIM_PATTERN_INCOMPLETE,
    CLAIM_ROUND_NOT_DRAWING,
    CLAIM_VALID,
    GAME_CLASSIC,
    GAME_ELIMINATION,
    GAME_TYPES,
    ROUND_CLOSED,
    ROUND_DRAWING,
    ROUND_OPEN,
    ROUND_WON,
    BingoCard,
    Claim,
    Draw,
    GameRound,
    Player,
)
from app.domain.draws import (
    TOTAL_BALLS,
    PoolExhaustedError,
    format_call,
    pick_next,
    validate_ball,
)
from app.domain.patterns import PATTERNS, is_win, marked_mask, missing_count
from app.domain.rng import Randomizer, new_join_code
from app.services import elimination
from app.services.identity import utcnow

_MAX_JOIN_CODE_ATTEMPTS = 6
_MAX_DRAW_ATTEMPTS = 6


class RoundError(Exception):
    """Hindi puwede ang operasyon sa kasalukuyang state ng round."""


@dataclass(frozen=True, slots=True)
class DrawResult:
    ball: int
    call: str
    sequence_no: int
    remaining: int


@dataclass(frozen=True, slots=True)
class ClaimOutcome:
    verified: bool
    reason: str
    missing: int
    draw_count: int
    is_first_winner: bool
    card_serial: str


async def create_round(
    db: AsyncSession,
    *,
    pattern: str,
    label: str = "",
    caller_mode: str = CALLER_AUTO,
    game_type: str = GAME_CLASSIC,
) -> GameRound:
    if game_type not in GAME_TYPES:
        raise RoundError(f"Unknown game type: {game_type}")
    if pattern not in PATTERNS:
        raise RoundError(f"Unknown pattern: {pattern}")
    if caller_mode not in CALLER_MODES:
        raise RoundError(f"Unknown caller mode: {caller_mode}")

    # Kailangang alam ng app ang bawat bola para may matanggal. Sa cards-only
    # mode ay wala itong nalalaman.
    if game_type == GAME_ELIMINATION and caller_mode == CALLER_OFFLINE:
        raise RoundError(
            "Elimination needs the app to know each number, so it cannot be used "
            "with the cards-only caller mode."
        )

    for _ in range(_MAX_JOIN_CODE_ATTEMPTS):
        game = GameRound(
            join_code=new_join_code(),
            game_type=game_type,
            pattern=pattern,
            caller_mode=caller_mode,
            status=ROUND_OPEN,
            label=label,
            created_at=utcnow(),
        )
        savepoint = await db.begin_nested()
        db.add(game)
        try:
            await savepoint.commit()
        except IntegrityError:
            await savepoint.rollback()
            continue
        return game

    raise RoundError("Could not allocate a free join code.")


async def get_round(db: AsyncSession, round_id: str) -> GameRound | None:
    return await db.scalar(select(GameRound).where(GameRound.id == round_id))


async def get_round_by_code(db: AsyncSession, join_code: str) -> GameRound | None:
    return await db.scalar(select(GameRound).where(GameRound.join_code == join_code.upper()))


async def drawn_balls(db: AsyncSession, round_id: str) -> list[int]:
    """Ang sequence ng bola sa pagkakasunod ng paglabas."""
    result = await db.scalars(
        select(Draw.ball).where(Draw.round_id == round_id).order_by(Draw.sequence_no)
    )
    return list(result)


async def start_round(db: AsyncSession, game: GameRound) -> GameRound:
    """Isara ang pagpasok ng bagong card at simulan ang draws.

    Hindi na puwedeng sumali pagkatapos nito — pareho ang bilang ng bola na
    nakikita ng lahat ng card sa round.
    """
    if game.status != ROUND_OPEN:
        raise RoundError(f"The round is already {game.status} and cannot be started.")

    result = await db.execute(
        update(GameRound)
        .where(GameRound.id == game.id, GameRound.status == ROUND_OPEN)
        .values(status=ROUND_DRAWING, started_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise RoundError("Someone else already started this round.")

    await db.commit()
    await db.refresh(game)
    return game


async def close_round(db: AsyncSession, game: GameRound) -> GameRound:
    result = await db.execute(
        update(GameRound)
        .where(GameRound.id == game.id, GameRound.status.in_([ROUND_OPEN, ROUND_DRAWING]))
        .values(status=ROUND_CLOSED, ended_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise RoundError("The round is already closed.")
    await db.commit()
    await db.refresh(game)
    return game


async def draw_next(
    db: AsyncSession,
    game: GameRound,
    rng: Randomizer,
    *,
    ball: int | None = None,
) -> DrawResult:
    """Itala ang susunod na bola.

    Kapag `ball` ay `None`, ang RNG ang pumipili — iyon ang `auto` mode. Kapag
    may `ball`, iyon ang ipinasok ng host mula sa pisikal na tambiolo, at
    `manual` mode iyon. Pareho ang lahat ng iba: parehong table, parehong
    constraint, parehong verification.

    Ang unique constraint sa `(round_id, sequence_no)` ang gumagawa nitong safe
    kahit dalawang pindot ang dumating nang sabay: ang pangalawa ay babagsak sa
    IntegrityError at magbabasa ng bagong state bago mag-retry, kaya walang
    dalawang bola na nakukuha ang parehong posisyon.
    """
    if game.caller_mode == CALLER_OFFLINE:
        raise RoundError("This round is not tracking numbers, so nothing can be drawn.")
    if game.status != ROUND_DRAWING:
        raise RoundError(f"The round is {game.status}, so drawing is not allowed.")
    if game.caller_mode == CALLER_AUTO and ball is not None:
        raise RoundError("This round draws its own numbers, so no ball may be supplied.")
    if game.caller_mode == CALLER_MANUAL and ball is None:
        raise RoundError("This round needs the ball that came out of your machine.")

    if ball is not None:
        validate_ball(ball)

    for _ in range(_MAX_DRAW_ATTEMPTS):
        drawn = await drawn_balls(db, game.id)

        if ball is None:
            try:
                chosen = pick_next(drawn, rng)
            except PoolExhaustedError as exc:
                raise RoundError("All 75 balls have already been drawn.") from exc
        else:
            if ball in drawn:
                raise RoundError(f"{format_call(ball)} has already come out.")
            chosen = ball

        sequence_no = len(drawn) + 1
        savepoint = await db.begin_nested()
        db.add(
            Draw(
                round_id=game.id,
                sequence_no=sequence_no,
                ball=chosen,
                drawn_at=utcnow(),
            )
        )
        try:
            await savepoint.commit()
        except IntegrityError:
            await savepoint.rollback()
            continue

        await db.commit()
        return DrawResult(
            ball=chosen,
            call=format_call(chosen),
            sequence_no=sequence_no,
            remaining=TOTAL_BALLS - sequence_no,
        )

    raise RoundError("The draw did not go through because of simultaneous presses.")


async def cards_for_player(db: AsyncSession, player_id: str) -> list[BingoCard]:
    result = await db.scalars(
        select(BingoCard).where(BingoCard.player_id == player_id).order_by(BingoCard.serial)
    )
    return list(result)


async def verify_claim(
    db: AsyncSession,
    *,
    game: GameRound,
    player: Player,
    cards: list[BingoCard],
    reported_marks: list[int] | None = None,
) -> ClaimOutcome:
    """I-verify ang BINGO gamit ang naka-store na card at ang naka-store na draws.

    Walang input mula sa client tungkol sa marking. Ang stored layout at ang
    draw table lang ang basehan.
    """
    if not cards:
        raise RoundError("This player has no cards in this round.")

    drawn = await drawn_balls(db, game.id)
    draw_count = len(drawn)
    now = utcnow()

    if game.caller_mode == CALLER_OFFLINE:
        # Walang draw table na maihahambing, kaya walang maidedeklara ang server.
        # Inaanunsyo lang ito at ang host ang titingin sa card gamit ang mga
        # numerong lumabas sa pisikal na tambiolo.
        return await _record_claim(
            db,
            game=game,
            player=player,
            card=cards[0],
            verified=False,
            reason=CLAIM_ANNOUNCED,
            missing=0,
            draw_count=draw_count,
            at=now,
            reported_marks=reported_marks,
        )

    if game.status not in (ROUND_DRAWING, ROUND_WON):
        return await _record_claim(
            db,
            game=game,
            player=player,
            card=cards[0],
            verified=False,
            reason=CLAIM_ROUND_NOT_DRAWING,
            missing=0,
            draw_count=draw_count,
            at=now,
        )

    # Ang pinakamalapit na card ang ipinapanalo. Kung marami ang card ng player,
    # sapat na ang isang tumama.
    best_card = cards[0]
    best_missing = missing_count(marked_mask(tuple(cards[0].numbers), drawn), game.pattern)
    winner: BingoCard | None = None

    for card in cards:
        marked = marked_mask(tuple(card.numbers), drawn)
        if is_win(marked, game.pattern):
            winner = card
            best_missing = 0
            break
        gap = missing_count(marked, game.pattern)
        if gap < best_missing:
            best_card, best_missing = card, gap

    if winner is None:
        return await _record_claim(
            db,
            game=game,
            player=player,
            card=best_card,
            verified=False,
            reason=CLAIM_PATTERN_INCOMPLETE,
            missing=best_missing,
            draw_count=draw_count,
            at=now,
        )

    # Ang unang valid na claim ang nagsasara ng round. Ang mga valid na claim sa
    # parehong `draw_count` ay co-winner — ganito rin sa totoong bingo hall
    # kapag sabay tumama sa parehong bola.
    first = await db.execute(
        update(GameRound)
        .where(GameRound.id == game.id, GameRound.first_winner_card_id.is_(None))
        .values(
            status=ROUND_WON,
            first_winner_card_id=winner.id,
            winning_draw_count=draw_count,
            ended_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    is_first = first.rowcount == 1
    if is_first:
        late = False
    else:
        # Basahin muli mula sa DB: ang in-memory na `game` ay puwedeng luma na
        # kung ibang request ang nakauna sa pagsara ng round.
        winning_at = await db.scalar(
            select(GameRound.winning_draw_count).where(GameRound.id == game.id)
        )
        late = winning_at is not None and draw_count > winning_at

    return await _record_claim(
        db,
        game=game,
        player=player,
        card=winner,
        verified=not late,
        reason=CLAIM_LATE if late else CLAIM_VALID,
        missing=0,
        draw_count=draw_count,
        at=now,
        is_first_winner=is_first,
    )


async def _record_claim(
    db: AsyncSession,
    *,
    game: GameRound,
    player: Player,
    card: BingoCard,
    verified: bool,
    reason: str,
    missing: int,
    draw_count: int,
    at: object,
    is_first_winner: bool = False,
    reported_marks: list[int] | None = None,
) -> ClaimOutcome:
    savepoint = await db.begin_nested()
    db.add(
        Claim(
            round_id=game.id,
            card_id=card.id,
            player_id=player.id,
            pattern=game.pattern,
            verified=verified,
            reason=reason,
            draw_count=draw_count,
            missing_cells=missing,
            is_first_winner=is_first_winner,
            claimed_at=at,
            reported_marks=reported_marks or [],
        )
    )
    try:
        await savepoint.commit()
    except IntegrityError:
        # Dobleng pindot sa parehong bola. Hindi na kailangang i-record muli.
        await savepoint.rollback()

    await db.commit()
    return ClaimOutcome(
        verified=verified,
        reason=reason,
        missing=missing,
        draw_count=draw_count,
        is_first_winner=is_first_winner,
        card_serial=card.serial,
    )


async def round_summary(db: AsyncSession, game: GameRound) -> dict[str, object]:
    """State ng round para sa caller screen at para sa reconnect ng player."""
    drawn = await drawn_balls(db, game.id)
    card_total = await db.scalar(
        select(func.count(BingoCard.id)).where(BingoCard.round_id == game.id)
    )
    player_total = await db.scalar(
        select(func.count(func.distinct(BingoCard.player_id))).where(BingoCard.round_id == game.id)
    )
    extra: dict[str, object] = {}
    if game.game_type == GAME_ELIMINATION:
        extra = {"survivors": await elimination.count_survivors(db, game.id)}

    return {
        "id": game.id,
        "join_code": game.join_code,
        "game_type": game.game_type,
        "pattern": game.pattern,
        "caller_mode": game.caller_mode,
        "status": game.status,
        **extra,
        "label": game.label,
        "drawn": drawn,
        "draw_count": len(drawn),
        "remaining": TOTAL_BALLS - len(drawn),
        "last_call": format_call(drawn[-1]) if drawn else None,
        "card_count": card_total or 0,
        "player_count": player_total or 0,
        "winning_draw_count": game.winning_draw_count,
    }
