"""Player-side live board.

Ang `/play/{token}` ay capability URL: ang token lang ang nagbubukas ng cards ng
player. Walang password, walang session — tulad ng pairing nonce, pero hindi
nag-e-expire habang tumatakbo ang round.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import templates
from app.api.rounds import round_topic
from app.api.security import enforce_rate_limit
from app.config import get_settings
from app.db.models import (
    CALLER_OFFLINE,
    CLAIM_ANNOUNCED,
    BingoCard,
    EliminationTicket,
    GameRound,
    Player,
)
from app.db.session import get_db
from app.domain.cards import GRID_SIZE
from app.domain.draws import TOTAL_BALLS
from app.domain.patterns import pattern_cell_groups
from app.services import elimination, rounds
from app.services.events import get_broker

router = APIRouter()

_WS_PING_SECONDS = 20.0


class ClaimResponse(BaseModel):
    verified: bool
    reason: str
    missing: int
    draw_count: int
    is_first_winner: bool
    card_serial: str
    message: str


async def _player_by_token(db: AsyncSession, token: str) -> Player:
    player = await db.scalar(select(Player).where(Player.play_token == token))
    if player is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That board does not exist."
        )
    return player


async def _round_of(db: AsyncSession, cards: list[BingoCard]) -> GameRound:
    game = await db.scalar(select(GameRound).where(GameRound.id == cards[0].round_id))
    if game is None:  # pragma: no cover - FK ang pumipigil dito
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That round does not exist."
        )
    return game


def _card_payload(card: BingoCard) -> dict[str, object]:
    numbers = list(card.numbers)
    return {
        "serial": card.serial,
        "numbers": numbers,
        "rows": [numbers[row * GRID_SIZE : (row + 1) * GRID_SIZE] for row in range(GRID_SIZE)],
    }


@router.get("/play/{token}", response_class=HTMLResponse, include_in_schema=False)
async def play_board(
    request: Request,
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    player = await _player_by_token(db, token)

    ticket = await db.scalar(
        select(EliminationTicket).where(EliminationTicket.player_id == player.id)
    )
    if ticket is not None:
        game = await db.scalar(select(GameRound).where(GameRound.id == ticket.round_id))
        if game is None:  # pragma: no cover - FK ang pumipigil dito
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="That round does not exist."
            )
        return templates.TemplateResponse(
            request,
            "play_ticket.html",
            {
                "token": token,
                "player_name": player.given_name,
                "round": await rounds.round_summary(db, game),
                "numbers": await elimination.numbers_for_ticket(db, ticket.id),
                "is_out": ticket.eliminated_at is not None,
                "out_on": ticket.eliminated_by_ball,
                "is_winner": ticket.is_winner,
            },
        )

    cards = await rounds.cards_for_player(db, player.id)
    if not cards:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="This board has no cards."
        )

    game = await _round_of(db, cards)
    return templates.TemplateResponse(
        request,
        "play.html",
        {
            "token": token,
            "player_name": player.given_name,
            "round": await rounds.round_summary(db, game),
            "cards": [_card_payload(card) for card in cards],
            "pattern_groups": pattern_cell_groups(game.pattern),
            "total_balls": TOTAL_BALLS,
            # Kapag walang binibilang ang app, malayang makakapindot ang player.
            "free_marking": game.caller_mode == CALLER_OFFLINE,
        },
    )


@router.websocket("/api/play/{token}/events")
async def play_events(
    websocket: WebSocket,
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Live feed para sa phone ng player. Read-only ang stream na ito."""
    await websocket.accept()

    player = await db.scalar(select(Player).where(Player.play_token == token))
    if player is None:
        await websocket.close(code=4404)
        return

    # Ang player ay may card (classic) o may ticket (elimination).
    round_id: str | None = None
    cards = await rounds.cards_for_player(db, player.id)
    if cards:
        round_id = cards[0].round_id
    else:
        ticket = await db.scalar(
            select(EliminationTicket).where(EliminationTicket.player_id == player.id)
        )
        round_id = ticket.round_id if ticket else None

    if round_id is None:
        await websocket.close(code=4404)
        return

    game = await db.scalar(select(GameRound).where(GameRound.id == round_id))
    if game is None:  # pragma: no cover
        await websocket.close(code=4404)
        return

    snapshot = await rounds.round_summary(db, game)
    async with get_broker().subscribe(round_topic(game.id)) as queue:
        try:
            await websocket.send_json({"event": "snapshot", **snapshot})
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=_WS_PING_SECONDS)
                except TimeoutError:
                    await websocket.send_json({"event": "ping"})
                    continue
                await websocket.send_json(payload)
        except WebSocketDisconnect:
            return
        except RuntimeError:
            return


@router.post("/api/play/{token}/bingo", response_model=ClaimResponse)
async def claim_bingo(
    request: Request,
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ClaimResponse:
    """Ang pinindot na BINGO. Ang server ang nagve-verify, hindi ang phone.

    Walang marking data na tinatanggap mula sa client. Ang naka-store na card
    layout at ang draw table lang ang basehan.
    """
    settings = get_settings()
    enforce_rate_limit(
        request,
        bucket="bingo_claim",
        limit=settings.bingo_rate_limit_per_minute,
    )

    player = await _player_by_token(db, token)
    cards = await rounds.cards_for_player(db, player.id)
    if not cards:
        ticket = await db.scalar(
            select(EliminationTicket).where(EliminationTicket.player_id == player.id)
        )
        if ticket is not None:
            # Walang ipipindot sa elimination: awtomatiko ang pagtanggal.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This is an elimination round, so there is nothing to claim.",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="This board has no cards."
        )

    game = await _round_of(db, cards)
    try:
        outcome = await rounds.verify_claim(db, game=game, player=player, cards=cards)
    except rounds.RoundError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if outcome.verified:
        await get_broker().publish(
            round_topic(game.id),
            {
                "event": "round_won",
                "player_name": player.given_name,
                "card_serial": outcome.card_serial,
                "draw_count": outcome.draw_count,
                "is_first_winner": outcome.is_first_winner,
            },
        )
    elif outcome.reason == CLAIM_ANNOUNCED:
        # Offline mode: walang draw table na maihahambing, kaya ang host ang
        # titingin. Ipinapadala ang naka-store na card sa caller screen para
        # may tiningnan siya — galing sa server ang layout, hindi sa phone.
        winning = next((c for c in cards if c.serial == outcome.card_serial), cards[0])
        await get_broker().publish(
            round_topic(game.id),
            {
                "event": "bingo_announced",
                "player_name": player.given_name,
                "cards": [_card_payload(card) for card in cards],
                "card_serial": winning.serial,
            },
        )

    return ClaimResponse(
        verified=outcome.verified,
        reason=outcome.reason,
        missing=outcome.missing,
        draw_count=outcome.draw_count,
        is_first_winner=outcome.is_first_winner,
        card_serial=outcome.card_serial,
        message=_message_for(outcome),
    )


def _message_for(outcome: rounds.ClaimOutcome) -> str:
    if outcome.verified and outcome.is_first_winner:
        return f"BINGO! You won with {outcome.card_serial}."
    if outcome.verified:
        return f"BINGO! You tied with someone on ball {outcome.draw_count}."
    if outcome.reason == CLAIM_ANNOUNCED:
        return "BINGO called. The host is checking your card."
    if outcome.reason == "round_not_drawing":
        return "The round has not started yet."
    if outcome.reason == "late":
        return "Someone already won on fewer balls."
    if outcome.missing == 1:
        return "One away. The pattern is not complete yet."
    return f"Not complete yet. {outcome.missing} more numbers to go."
