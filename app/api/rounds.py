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
from app.db.session import get_db
from app.domain.draws import TOTAL_BALLS
from app.domain.patterns import PATTERNS, pattern_cell_groups
from app.domain.rng import system_randomizer
from app.services import audit, rounds
from app.services.events import get_broker

router = APIRouter()

_WS_PING_SECONDS = 20.0


def round_topic(round_id: str) -> str:
    return f"round:{round_id}"


class CreateRoundRequest(BaseModel):
    pattern: str = "any_line"
    label: str = Field(default="", max_length=64)


class RoundResponse(BaseModel):
    id: str
    join_code: str
    pattern: str
    status: str
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
        game = await rounds.create_round(db, pattern=payload.pattern, label=payload.label)
    except rounds.RoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    await audit.record(
        db,
        event="round.created",
        outcome=audit.OUTCOME_OK,
        detail={"round": game.id, "pattern": game.pattern},
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
) -> DrawResponse:
    game = await rounds.get_round(db, round_id)
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That round does not exist."
        )
    try:
        result = await rounds.draw_next(db, game, system_randomizer())
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
    return DrawResponse(
        ball=result.ball,
        call=result.call,
        sequence_no=result.sequence_no,
        remaining=result.remaining,
        status=game.status,
    )


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
    }
    return templates.TemplateResponse(request, "caller.html", context)
