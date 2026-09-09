"""Caller-side routes: gumawa ng round, magsimula, mag-draw, at manood."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated, Any

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
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import templates
from app.api.security import OPERATOR_COOKIE, get_session_store, require_operator
from app.db.models import (
    CALLER_AUTO,
    CALLER_MODES,
    GAME_CLASSIC,
    GAME_ELIMINATION,
    GAME_TYPES,
)
from app.db.session import get_db
from app.domain.draws import BALL_MAX, BALL_MIN, TOTAL_BALLS
from app.domain.patterns import PATTERNS, pattern_cell_groups
from app.domain.rng import system_randomizer
from app.services import audit, elimination, rounds
from app.services.events import get_broker

router = APIRouter()

_WS_PING_SECONDS = 20.0


def round_topic(round_id: str) -> str:
    return f"round:{round_id}"


class CreateRoundRequest(BaseModel):
    game_type: str = GAME_CLASSIC
    pattern: str = "any_line"
    label: str = Field(default="", max_length=64)
    caller_mode: str = CALLER_AUTO


class ConfirmWinRequest(BaseModel):
    """Ang card na pinasyahan ng host bilang panalo sa cards-only mode."""

    card_serial: str


class DrawRequest(BaseModel):
    # Kailangan sa manual mode: ang bolang lumabas sa pisikal na tambiolo.
    # Dapat walang laman sa auto mode.
    ball: int | None = Field(default=None, ge=BALL_MIN, le=BALL_MAX)


class RoundResponse(BaseModel):
    id: str
    join_code: str
    game_type: str
    pattern: str
    caller_mode: str
    status: str
    survivors: int | None = None
    label: str
    drawn: list[int]
    draw_count: int
    remaining: int
    last_call: str | None
    card_count: int
    player_count: int
    winning_draw_count: int | None


class DrawResponse(BaseModel):
    ball: int
    call: str
    sequence_no: int
    remaining: int
    status: str


class RoundListItem(BaseModel):
    id: str
    join_code: str
    pattern: str
    status: str
    created_at: datetime


async def _summary(db: AsyncSession, round_id: str) -> RoundResponse:
    game = await rounds.get_round(db, round_id)
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That round does not exist."
        )
    return RoundResponse(**await rounds.round_summary(db, game))  # type: ignore[arg-type]


@router.post(
    "/api/rounds",
    response_model=RoundResponse,
    dependencies=[Depends(require_operator)],
)
async def create_round(
    payload: CreateRoundRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RoundResponse:
    try:
        game = await rounds.create_round(
            db,
            pattern=payload.pattern,
            label=payload.label,
            caller_mode=payload.caller_mode,
            game_type=payload.game_type,
        )
    except rounds.RoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    await audit.record(
        db,
        event="round.created",
        outcome=audit.OUTCOME_OK,
        detail={"round": game.id, "pattern": game.pattern, "caller_mode": game.caller_mode},
    )
    await db.commit()
    return await _summary(db, game.id)


@router.get(
    "/api/rounds/{round_id}",
    response_model=RoundResponse,
    dependencies=[Depends(require_operator)],
)
async def read_round(
    round_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RoundResponse:
    return await _summary(db, round_id)


@router.post(
    "/api/rounds/{round_id}/start",
    response_model=RoundResponse,
    dependencies=[Depends(require_operator)],
)
async def start_round(
    round_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RoundResponse:
    game = await rounds.get_round(db, round_id)
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That round does not exist."
        )
    try:
        await rounds.start_round(db, game)
    except rounds.RoundError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await audit.record(
        db, event="round.started", outcome=audit.OUTCOME_OK, detail={"round": game.id}
    )
    await db.commit()

    summary = await _summary(db, round_id)
    await get_broker().publish(
        round_topic(round_id),
        {"event": "round_started", "pattern": summary.pattern, "status": summary.status},
    )
    return summary


@router.post(
    "/api/rounds/{round_id}/draw",
    response_model=DrawResponse,
    dependencies=[Depends(require_operator)],
)
async def draw_ball(
    round_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    payload: DrawRequest | None = None,
) -> DrawResponse:
    game = await rounds.get_round(db, round_id)
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That round does not exist."
        )
    try:
        result = await rounds.draw_next(
            db,
            game,
            system_randomizer(),
            ball=payload.ball if payload else None,
        )
    except rounds.RoundError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await get_broker().publish(
        round_topic(round_id),
        {
            "event": "ball_drawn",
            "ball": result.ball,
            "call": result.call,
            "sequence_no": result.sequence_no,
            "remaining": result.remaining,
        },
    )

    if game.game_type == GAME_ELIMINATION:
        drawn = set(await rounds.drawn_balls(db, round_id))
        effect = await elimination.apply_draw(
            db, game=game, drawn=drawn, draw_count=result.sequence_no
        )
        if effect.knocked_out:
            await get_broker().publish(
                round_topic(round_id),
                {
                    "event": "players_eliminated",
                    "ball": result.ball,
                    "call": result.call,
                    "draw_count": result.sequence_no,
                    "players": [
                        {"player_name": knock.player_name, "serial": knock.card_serial}
                        for knock in effect.knocked_out
                    ],
                    "survivors": effect.survivors,
                },
            )
        if effect.winners:
            await get_broker().publish(
                round_topic(round_id),
                {
                    "event": "elimination_over",
                    "winners": effect.winners,
                    "draw_count": result.sequence_no,
                },
            )

    return DrawResponse(
        ball=result.ball,
        call=result.call,
        sequence_no=result.sequence_no,
        remaining=result.remaining,
        status=game.status,
    )


@router.post(
    "/api/rounds/{round_id}/confirm-win",
    response_model=RoundResponse,
    dependencies=[Depends(require_operator)],
)
async def confirm_win(
    round_id: str,
    payload: ConfirmWinRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RoundResponse:
    """Ang host ang nagpapasya sa cards-only mode. Ito ang pagtatala ng pasya."""
    game = await rounds.get_round(db, round_id)
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That round does not exist."
        )
    try:
        winner = await rounds.confirm_offline_win(db, game=game, card_serial=payload.card_serial)
    except rounds.RoundError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await audit.record(
        db,
        event="round.win_confirmed",
        outcome=audit.OUTCOME_OK,
        detail={"round": game.id, "card": payload.card_serial},
    )
    await db.commit()
    await get_broker().publish(
        round_topic(round_id),
        {
            "event": "round_won",
            "player_name": winner,
            "card_serial": payload.card_serial,
            "draw_count": 0,
            "is_first_winner": True,
            # Sinasabi nito sa mga page na huwag magbanggit ng bola: walang
            # naitalang sequence sa mode na ito.
            "confirmed_by_host": True,
        },
    )
    return await _summary(db, round_id)


@router.post(
    "/api/rounds/{round_id}/close",
    response_model=RoundResponse,
    dependencies=[Depends(require_operator)],
)
async def close_round(
    round_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RoundResponse:
    game = await rounds.get_round(db, round_id)
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That round does not exist."
        )
    try:
        await rounds.close_round(db, game)
    except rounds.RoundError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await audit.record(
        db, event="round.closed", outcome=audit.OUTCOME_OK, detail={"round": game.id}
    )
    await db.commit()
    await get_broker().publish(round_topic(round_id), {"event": "round_closed"})
    return await _summary(db, round_id)


@router.websocket("/api/rounds/{round_id}/events")
async def round_events(
    websocket: WebSocket,
    round_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Live feed ng round: bawat bola, panalo, at pagsara.

    Naghahatid din ng buong state sa simula, para tama pa rin ang board kahit
    nag-reconnect ang caller screen sa kalagitnaan ng laro.
    """
    await websocket.accept()
    game = await rounds.get_round(db, round_id)
    if game is None:
        await websocket.close(code=4404)
        return

    snapshot = await rounds.round_summary(db, game)
    async with get_broker().subscribe(round_topic(round_id)) as queue:
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


@router.get("/caller/{round_id}", response_class=HTMLResponse, include_in_schema=False)
async def caller_screen(
    request: Request,
    round_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    session = get_session_store().get(request.cookies.get(OPERATOR_COOKIE))
    if session is None:
        return RedirectResponse("/operator", status_code=status.HTTP_303_SEE_OTHER)

    game = await rounds.get_round(db, round_id)
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That round does not exist."
        )

    context: dict[str, Any] = {
        "csrf_token": session.csrf_token,
        "round": await rounds.round_summary(db, game),
        "pattern_groups": pattern_cell_groups(game.pattern),
        "total_balls": TOTAL_BALLS,
        "patterns": sorted(PATTERNS),
        "caller_modes": CALLER_MODES,
        "game_types": GAME_TYPES,
        "roster": await elimination.roster(db, game.id, set(await rounds.drawn_balls(db, game.id)))
        if game.game_type == GAME_ELIMINATION
        else [],
    }
    return templates.TemplateResponse(request, "caller.html", context)
