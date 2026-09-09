"""QR pairing endpoints at ang player-facing claim flow."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import urlsplit

import segno
from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import templates
from app.api.security import enforce_rate_limit, require_operator
from app.config import Settings, get_settings
from app.db.models import PAIRING_PENDING, ROUND_OPEN, Player
from app.db.session import get_db
from app.domain.cards import Card
from app.domain.rng import new_play_token, system_randomizer
from app.services import audit, issuance, rounds
from app.services import pairing as pairing_service
from app.services.events import get_broker
from app.services.identity import utcnow

router = APIRouter()

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
_WS_PING_SECONDS = 20.0


class CreatePairingRequest(BaseModel):
    round_id: str
    card_count: int | None = Field(default=None, ge=1, le=24)
    kiosk_label: str = Field(default="web", max_length=64)


class CreatePairingResponse(BaseModel):
    id: str
    round_id: str
    pair_url: str
    qr_data_uri: str
    expires_at: datetime
    card_count: int
    pattern: str
    warnings: list[str]


class PairingStatusResponse(BaseModel):
    id: str
    round_id: str
    status: str
    expires_at: datetime
    card_count: int


def _pair_url(settings: Settings, nonce: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/pair/{nonce}"


def _base_url_warnings(settings: Settings) -> list[str]:
    """Bantayan ang pinakamadaling pagkakamalian sa QR pairing.

    Kung `localhost` ang naka-encode sa QR, ang phone na mag-scan ay
    magre-resolve niyan sa sarili niya at mabibigo ang pairing.
    """
    host = urlsplit(settings.public_base_url).hostname or ""
    warnings: list[str] = []
    if host in _LOCAL_HOSTS:
        warnings.append(
            "Ang BINGO_PUBLIC_BASE_URL ay nakaturo sa localhost. Gumagana ito sa "
            "parehong makina lang. Para ma-scan ng phone, palitan ng LAN IP "
            "(halimbawa http://192.168.1.10:8000) at i-bind ang uvicorn sa 0.0.0.0."
        )
    if urlsplit(settings.public_base_url).scheme != "https" and host not in _LOCAL_HOSTS:
        warnings.append(
            "Plain HTTP ang base URL. Sa production ay dapat HTTPS — dumadaan dito "
            "ang nonce at ang personal na detalye ng player."
        )
    return warnings


@router.post(
    "/api/pairing",
    response_model=CreatePairingResponse,
    dependencies=[Depends(require_operator)],
)
async def create_pairing(
    request: Request,
    payload: CreatePairingRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CreatePairingResponse:
    settings = get_settings()
    enforce_rate_limit(
        request,
        bucket="pairing_create",
        limit=settings.pairing_rate_limit_per_minute,
    )

    game = await rounds.get_round(db, payload.round_id)
    if game is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That round does not exist."
        )
    if game.status != ROUND_OPEN:
        # Pagkasimula ng draws ay sarado na ang pagpasok, para pareho ang bilang
        # ng bola na nakita ng lahat ng card sa round.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The round has already started, so joining is closed.",
        )

    card_count = payload.card_count or settings.default_card_count
    if card_count > settings.max_card_count:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"The maximum is {settings.max_card_count} cards per QR.",
        )

    record = await pairing_service.create_pairing(
        db,
        settings,
        round_id=game.id,
        card_count=card_count,
        kiosk_label=payload.kiosk_label,
    )
    await audit.record(
        db,
        event="pairing.created",
        outcome=audit.OUTCOME_OK,
        nonce=record.nonce,
        detail={"card_count": card_count, "round": game.id},
    )
    await db.commit()

    url = _pair_url(settings, record.nonce)
    qr = segno.make(url, error="m")

    return CreatePairingResponse(
        id=record.id,
        round_id=game.id,
        pair_url=url,
        qr_data_uri=qr.svg_data_uri(scale=6, dark="#101828", light="#ffffff"),
        expires_at=record.expires_at,
        card_count=record.card_count,
        pattern=game.pattern,
        warnings=_base_url_warnings(settings),
    )


@router.get(
    "/api/pairing/{pairing_id}",
    response_model=PairingStatusResponse,
    dependencies=[Depends(require_operator)],
)
async def pairing_status(
    pairing_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PairingStatusResponse:
    record = await pairing_service.get_by_id(db, pairing_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That pairing does not exist."
        )
    # Sadyang wala ang nonce sa response. Ang nonce ay nasa QR lang.
    return PairingStatusResponse(
        id=record.id,
        round_id=record.round_id,
        status=record.status,
        expires_at=record.expires_at,
        card_count=record.card_count,
    )


@router.websocket("/api/pairing/{pairing_id}/events")
async def pairing_events(websocket: WebSocket, pairing_id: str) -> None:
    """Ipinapaalam sa kiosk page kung kailan na-claim ang QR."""
    await websocket.accept()
    broker = get_broker()

    async with broker.subscribe(f"pairing:{pairing_id}") as queue:
        try:
            await websocket.send_json({"event": "listening", "pairing_id": pairing_id})
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=_WS_PING_SECONDS)
                except TimeoutError:
                    await websocket.send_json({"event": "ping"})
                    continue
                await websocket.send_json(payload)
                if payload.get("event") == "cards_issued":
                    break
        except WebSocketDisconnect:
            return
        with contextlib.suppress(RuntimeError):
            await websocket.close()


@router.get("/pair/{nonce}", response_class=HTMLResponse, include_in_schema=False)
async def pair_landing(
    request: Request,
    nonce: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """Ang page na bumubukas kapag in-scan ang QR ng camera app ng phone."""
    record = await pairing_service.peek(db, nonce)
    if not pairing_service.is_claimable(record):
        reason = "used" if record is not None and record.status != PAIRING_PENDING else "expired"
        return templates.TemplateResponse(
            request,
            "pair_unavailable.html",
            {"reason": reason},
            status_code=status.HTTP_410_GONE,
        )

    assert record is not None
    game = await rounds.get_round(db, record.round_id)
    return templates.TemplateResponse(
        request,
        "pair.html",
        {
            "nonce": nonce,
            "card_count": record.card_count,
            "pattern": game.pattern if game else "any_line",
            "expires_at": record.expires_at,
        },
    )


@router.post("/pair/{nonce}/claim", response_class=HTMLResponse, include_in_schema=False)
async def pair_claim(
    request: Request,
    nonce: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    given_name: Annotated[str, Form(min_length=1, max_length=64)],
) -> Response:
    settings = get_settings()
    settings.require_secrets()
    enforce_rate_limit(
        request,
        bucket="pairing_claim",
        limit=settings.pairing_rate_limit_per_minute,
    )

    record = await pairing_service.peek(db, nonce)
    if not pairing_service.is_claimable(record):
        await audit.record(
            db,
            event="pairing.claim",
            outcome=audit.OUTCOME_DENIED,
            nonce=nonce,
            detail={"reason": "not_claimable"},
        )
        await db.commit()
        return templates.TemplateResponse(
            request,
            "pair_unavailable.html",
            {"reason": "expired"},
            status_code=status.HTTP_410_GONE,
        )
    assert record is not None

    nickname = given_name.strip()
    if not nickname:
        round_pattern = await _pattern_of(db, record.round_id)
        return _claim_error(request, record, nonce, round_pattern, "Please enter a nickname.")

    player = Player(
        id=uuid.uuid4().hex,
        given_name=nickname,
        play_token=new_play_token(),
        created_at=utcnow(),
    )
    db.add(player)
    await db.flush()

    try:
        claimed = await pairing_service.consume(db, nonce=nonce, player_id=player.id)
        cards = await issuance.issue_cards(
            db, pairing=claimed, player=player, rng=system_randomizer()
        )
    except (pairing_service.PairingUnavailableError, issuance.IssuanceError) as exc:
        await db.rollback()
        await audit.record(
            db,
            event="pairing.claim",
            outcome=audit.OUTCOME_DENIED,
            nonce=nonce,
            detail={"reason": type(exc).__name__},
        )
        await db.commit()
        return templates.TemplateResponse(
            request,
            "pair_unavailable.html",
            {"reason": "used"},
            status_code=status.HTTP_409_CONFLICT,
        )

    await audit.record(
        db,
        event="pairing.claim",
        outcome=audit.OUTCOME_OK,
        nonce=nonce,
        player_id=player.id,
        detail={"cards": len(cards)},
    )
    await db.commit()

    game = await rounds.get_round(db, claimed.round_id)
    pattern = game.pattern if game else "any_line"

    await get_broker().publish(
        f"pairing:{claimed.id}",
        {
            "event": "cards_issued",
            "pairing_id": claimed.id,
            "round_id": claimed.round_id,
            "player_name": player.given_name,
            "pattern": pattern,
            "cards": [_card_payload(card) for card in cards],
        },
    )

    return templates.TemplateResponse(
        request,
        "pair_result.html",
        {
            "player_name": player.given_name,
            "pattern": pattern,
            "cards": [_card_payload(card) for card in cards],
            # Dito na siya mananatili habang tumatakbo ang laro.
            "play_url": f"/play/{player.play_token}",
        },
    )


def _card_payload(card: Card) -> dict[str, Any]:
    return {"serial": card.serial, "rows": card.rows()}


async def _pattern_of(db: AsyncSession, round_id: str) -> str:
    game = await rounds.get_round(db, round_id)
    return game.pattern if game else "any_line"


def _claim_error(
    request: Request,
    record: Any,  # noqa: ANN401 - PairingSession, iniiwasan ang circular typing
    nonce: str,
    pattern: str,
    message: str,
) -> Response:
    return templates.TemplateResponse(
        request,
        "pair.html",
        {
            "nonce": nonce,
            "card_count": record.card_count,
            "pattern": pattern,
            "expires_at": record.expires_at,
            "error": message,
        },
        status_code=status.HTTP_400_BAD_REQUEST,
    )
