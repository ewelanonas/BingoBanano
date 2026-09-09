"""QR pairing endpoints at ang player-facing claim flow."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import datetime
from ipaddress import ip_address
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


def resolve_base_url(request: Request, settings: Settings) -> str:
    """Ang base URL na ilalagay sa QR.

    Galing mismo sa request kapag ang host ay pumasok sa address na abot din ng
    mga bisita — tunnel hostname o LAN IP. Ganito, hindi na kailangang isulat
    ang tunnel URL sa `.env` at hindi na kailangang i-restart ang server kada
    bagong tunnel. Iyon ang dating pinagmumulan ng mga patay na QR.

    Kapag `localhost` ang ginamit ng host, hindi puwedeng gamitin iyon — ang
    phone ang magiging localhost. Doon lang bumabagsak sa naka-configure na
    `BINGO_PUBLIC_BASE_URL`.
    """
    host_header = request.headers.get("host", "")
    hostname = urlsplit(f"//{host_header}").hostname or ""

    if host_header and hostname not in _LOCAL_HOSTS:
        return f"{request.url.scheme}://{host_header}"

    return settings.public_base_url.rstrip("/")


def _pair_url(base_url: str, nonce: str) -> str:
    return f"{base_url.rstrip('/')}/pair/{nonce}"


def _is_private_host(host: str) -> bool:
    """`True` kung LAN address ang host, `False` kung public na pangalan o IP."""
    if host in _LOCAL_HOSTS:
        return True
    try:
        return ip_address(host).is_private
    except ValueError:
        # Hostname, hindi IP. Ipinapalagay na public.
        return False


def _base_url_warnings(base_url: str) -> list[str]:
    """Bantayan ang mga madaling pagkakamalian sa pag-set up ng pairing.

    Tatlong bagay ang hinahanap: localhost na hindi maaabot ng phone, plain
    HTTP na abot ng internet, at LAN address na hindi maaabot ng bisitang nasa
    mobile data.
    """
    parts = urlsplit(base_url)
    host = parts.hostname or ""
    warnings: list[str] = []

    if host in _LOCAL_HOSTS or not host:
        warnings.append(
            "The QR would point at localhost, which only works on this machine. "
            "Open the host lobby using your LAN IP instead of 127.0.0.1, or set "
            "BINGO_PUBLIC_BASE_URL. For guests on mobile data, run a tunnel: "
            ".\\scripts\\start-tunnel.ps1"
        )
    elif _is_private_host(host):
        warnings.append(
            f"The QR points at {host}, a LAN address. Guests must be on the same "
            "Wi-Fi. Anyone on mobile data or another network will time out — run "
            ".\\scripts\\start-tunnel.ps1 to get a public HTTPS link instead."
        )
    elif parts.scheme != "https":
        # Public na host pero walang TLS: dito talaga delikado.
        warnings.append(
            f"The QR points at {host} over plain HTTP, reachable from the "
            "internet. The host key and the join links would travel unencrypted. "
            "Use an HTTPS tunnel or put TLS in front before sharing this link."
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

    base_url = resolve_base_url(request, settings)
    url = _pair_url(base_url, record.nonce)
    qr = segno.make(url, error="m")

    return CreatePairingResponse(
        id=record.id,
        round_id=game.id,
        pair_url=url,
        qr_data_uri=qr.svg_data_uri(scale=6, dark="#101828", light="#ffffff"),
        expires_at=record.expires_at,
        card_count=record.card_count,
        pattern=game.pattern,
        warnings=_base_url_warnings(base_url),
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
