"""Banano Radio: request ng kanta mula sa phone ng bisita papunta sa Spotify ng host.

Dalawang audience ang nasa file na ito at magkaiba ang authorization ng bawat isa.

**Host.** Ang `/operator/radio*` at ang `/api/radio/*` na walang token ay
operator-only, kagaya ng kiosk. Ang host lang ang kumakabit sa Spotify.

**Bisita.** Ang `/radio/{token}` ay ang parehong capability URL na ginagamit sa
live board: ang `Player.play_token`. Walang bagong login, walang cookie, at
walang Spotify account na kailangan sa panig ng bisita.

Isang bagay na sadyang ganito: ang pamagat at haba ng kanta ay kinukuha ng
SERVER mula sa Spotify at hindi tinatanggap mula sa phone. Kung ang phone ang
magsasabi ng haba, ang duration cap ay puwedeng lampasan sa pagsisinungaling lang.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Annotated, Any
from urllib.parse import urlsplit

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
from fastapi.params import Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import templates
from app.api.security import (
    OPERATOR_COOKIE,
    enforce_rate_limit,
    get_session_store,
    require_operator,
)
from app.config import RadioNotConfiguredError, Settings, get_settings
from app.db.models import SONG_FAILED, SONG_QUEUED, Player
from app.db.session import get_db
from app.services import audit, radio, spotify
from app.services.events import get_broker

router = APIRouter()

_WS_PING_SECONDS = 20.0

# Ang mga ito ay problema sa dulong Spotify, hindi sa panuntunan natin. Ibang
# HTTP status para malaman ng host kung sino ang dapat aksyunan.
_UPSTREAM_REASONS = frozenset(
    {"premium_required", "no_active_device", "network", "upstream_rate_limited"}
)


class RequestSongBody(BaseModel):
    # `spotify:track:` + 22 characters. Sapat na ang 64 na hangganan; ang
    # tamang shape ay sinusuri ng `spotify.is_track_uri`.
    track_uri: Annotated[str, Field(min_length=1, max_length=64)]


class AcceptingBody(BaseModel):
    accepting: bool


class SearchResult(BaseModel):
    uri: str
    name: str
    artist: str
    duration_ms: int
    art_url: str


class RequestSongResponse(BaseModel):
    queued: bool
    message: str
    track_name: str
    artist_name: str


async def _player_by_token(db: AsyncSession, token: str) -> Player:
    player = await db.scalar(select(Player).where(Player.play_token == token))
    if player is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That radio does not exist."
        )
    return player


def _radio_settings(*, for_guest: bool) -> Settings:
    """Ang settings, o 503 kapag walang Spotify credentials.

    Iba ang sinasabi sa bisita: walang saysay sa kanya ang tagubilin tungkol sa
    `.env`, at hindi rin dapat ipaalam sa kahit sino ang setup ng server.
    """
    settings = get_settings()
    try:
        settings.require_radio()
    except RadioNotConfiguredError as exc:
        detail = "Banano Radio is not set up on this party yet." if for_guest else str(exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail) from exc
    return settings


def _redirect_warnings(request: Request, settings: Settings) -> list[str]:
    """Bantayan ang pinakamadaling pagkakamalian sa setup ng Spotify.

    Ang redirect URI ay dapat naka-register nang salitang-salita sa dashboard,
    kaya hindi ito puwedeng hulaan mula sa request gaya ng ginagawa sa QR base
    URL. Ang kapalit: puwedeng hindi tumugma ang address na binuksan ng host at
    ang address na nakasulat sa `.env`.

    Kapag nangyari iyon, ang sasabihin ng Spotify ay `INVALID_CLIENT: Invalid
    redirect URI` — at hindi nito sasabihin kung alin ang mali. Dito iyon
    sinasabi.
    """
    configured = urlsplit(settings.spotify_redirect_uri)
    browsing_host = request.headers.get("host", "")

    if not configured.netloc or not browsing_host:
        return []
    if configured.netloc.lower() == browsing_host.lower():
        return []

    return [
        f"You opened this page on {browsing_host}, but BINGO_SPOTIFY_REDIRECT_URI "
        f"points at {configured.netloc}. Spotify will send you back to "
        f"{configured.netloc} after sign-in, which this browser may not be able to "
        f"reach. Either open the host lobby at {configured.scheme}://"
        f"{configured.netloc} instead, or register "
        f"{request.url.scheme}://{browsing_host}/operator/radio/callback in your "
        "Spotify dashboard and point BINGO_SPOTIFY_REDIRECT_URI at it. Spotify "
        "matches the URI character for character."
    ]


def _spotify_http_error(exc: spotify.SpotifyError) -> HTTPException:
    upstream = exc.reason in _UPSTREAM_REASONS or isinstance(exc, spotify.SpotifyAuthError)
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY if upstream else status.HTTP_400_BAD_REQUEST,
        detail=str(exc),
    )


def _radio_http_error(exc: radio.RadioError) -> HTTPException:
    if isinstance(exc, radio.RadioNotConnected):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    # Sarili nating panuntunan ang bumara: cooldown, duplicate, sobrang haba.
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


# --------------------------------------------------------------------------
# Host: pagkabit ng Spotify
# --------------------------------------------------------------------------


@router.get("/operator/radio", response_class=HTMLResponse, include_in_schema=False)
async def radio_console(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    error: str | None = None,
) -> Response:
    session = get_session_store().get(request.cookies.get(OPERATOR_COOKIE))
    if session is None:
        return RedirectResponse("/operator", status_code=status.HTTP_303_SEE_OTHER)

    settings = get_settings()
    store = radio.get_connection_store()
    state = store.status(settings)

    return templates.TemplateResponse(
        request,
        "operator_radio.html",
        {
            "csrf_token": session.csrf_token,
            "configured": state.configured,
            "connected": state.connected,
            "accepting": state.accepting,
            "account_name": state.account_name,
            "redirect_uri": settings.spotify_redirect_uri,
            "cooldown_seconds": settings.radio_request_cooldown_seconds,
            "max_track_minutes": settings.radio_max_track_seconds // 60,
            "requests": await radio.recent_requests(db),
            "error": error,
            "warnings": _redirect_warnings(request, settings) if state.configured else [],
        },
    )


@router.get("/operator/radio/connect", include_in_schema=False)
async def radio_connect(request: Request) -> Response:
    """Ipadala ang browser ng host sa Spotify sign-in.

    Top-level navigation ito, kaya hindi puwedeng magpadala ng CSRF header —
    ang cookie session ang tinitingnan, kagaya ng ginagawa ng `/kiosk`. Wala
    namang binabago ang route na ito: nagbibigay lang ito ng bagong `state`.
    """
    if get_session_store().get(request.cookies.get(OPERATOR_COOKIE)) is None:
        return RedirectResponse("/operator", status_code=status.HTTP_303_SEE_OTHER)

    settings = get_settings()
    if not settings.radio_configured():
        return RedirectResponse(
            "/operator/radio?error=not_configured",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    url = spotify.authorize_url(
        client_id=settings.spotify_client_id,
        redirect_uri=settings.spotify_redirect_uri,
        state=radio.get_state_store().issue(),
    )
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/operator/radio/callback", response_class=HTMLResponse, include_in_schema=False)
async def radio_callback(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """Ang route na binabalikan ng Spotify pagkatapos mag-sign in ang host.

    Ang authorization dito ay ang single-use `state`, at hindi ang operator
    cookie. Dalawang dahilan: ang cookie natin ay `SameSite=strict` kaya hindi
    ito ipinapadala ng browser sa isang cross-site na pagbalik mula sa
    accounts.spotify.com, at ang `state` naman ay ibinibigay lang sa
    naka-authenticate na host, 256-bit, single-use, at panandalian. Iyon ang
    eksaktong trabaho ng `state` sa OAuth.

    Isang page ang isinasagot at hindi redirect. Kapag redirect, ang buong chain
    ay galing pa rin sa cross-site na simula, kaya ibabaon ng browser ang
    `SameSite=strict` na cookie at mababato ang host pabalik sa login kahit
    naka-sign in siya. Sa pindot ng link sa page na ito, same-site na ito.
    """
    settings = get_settings()

    def page(ok: bool, message: str) -> Response:
        return templates.TemplateResponse(
            request,
            "operator_radio_callback.html",
            {"ok": ok, "message": message},
            status_code=status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST,
        )

    # Fail closed: kahit isang butas sa state ay hindi puwedeng dumaan.
    if not radio.get_state_store().consume(state):
        await audit.record(
            db,
            event="radio.connect",
            outcome=audit.OUTCOME_DENIED,
            detail={"reason": "bad_state"},
        )
        await db.commit()
        return page(
            False,
            "That sign-in link is stale or was already used. Start the connection "
            "again from the Banano Radio page.",
        )

    if error or not code:
        await audit.record(
            db,
            event="radio.connect",
            outcome=audit.OUTCOME_DENIED,
            # Ang `error` ay galing sa Spotify at pinapaikli bago itala.
            detail={"reason": (error or "no_code")[:48]},
        )
        await db.commit()
        return page(False, "Spotify did not approve the connection. Nothing was changed.")

    try:
        grant = await spotify.exchange_code(
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
            redirect_uri=settings.spotify_redirect_uri,
            code=code,
        )
        name = await spotify.account_name(access_token=grant.access_token)
        radio.get_connection_store().connect(grant, account_name=name)
    except (spotify.SpotifyError, radio.RadioError) as exc:
        await audit.record(
            db,
            event="radio.connect",
            outcome=audit.OUTCOME_ERROR,
            detail={"reason": getattr(exc, "reason", "unknown")},
        )
        await db.commit()
        return page(False, str(exc))

    radio.get_now_playing_cache().invalidate()
    # Walang pangalan ng account sa audit detail: iyon ay PII ng host.
    await audit.record(db, event="radio.connect", outcome=audit.OUTCOME_OK)
    await db.commit()
    await get_broker().publish(radio.RADIO_TOPIC, {"event": "radio_opened"})
    return page(True, f"Connected to Spotify as {name}. Guests can pick songs now.")


@router.get("/api/radio/status", dependencies=[Depends(require_operator)])
async def radio_status(db: Annotated[AsyncSession, Depends(get_db)]) -> dict[str, Any]:
    settings = get_settings()
    store = radio.get_connection_store()
    state = store.status(settings)

    payload: dict[str, Any] = {
        "configured": state.configured,
        "connected": state.connected,
        "accepting": state.accepting,
        "account_name": state.account_name,
        "now_playing": None,
        "problem": "",
        "requests": await radio.recent_requests(db),
    }
    if not state.connected:
        return payload

    try:
        current = await radio.get_now_playing_cache().get(store, settings)
        payload["now_playing"] = radio.now_playing_payload(current)
    except (spotify.SpotifyError, radio.RadioError) as exc:
        # Huwag ipatumba ang buong console dahil lang sarado ang Spotify app ng
        # host. Ipakita ang dahilan at ipagpatuloy.
        payload["problem"] = str(exc)
        payload["connected"] = store.connected
    return payload


@router.post("/api/radio/accepting", dependencies=[Depends(require_operator)])
async def set_accepting(payload: AcceptingBody) -> dict[str, bool]:
    """Ang switch na kailangan ng host sa dulo ng party.

    Hindi puwedeng patayin ang Spotify para tumigil ang mga request — doon din
    nakikinig ang lahat.
    """
    radio.get_connection_store().set_accepting(payload.accepting)
    await get_broker().publish(
        radio.RADIO_TOPIC,
        {"event": "radio_accepting", "accepting": radio.get_connection_store().accepting},
    )
    return {"accepting": radio.get_connection_store().accepting}


@router.post("/api/radio/skip", dependencies=[Depends(require_operator)])
async def skip_track() -> dict[str, bool]:
    settings = _radio_settings(for_guest=False)
    store = radio.get_connection_store()
    try:
        token = await store.access_token(settings)
        await spotify.skip_next(access_token=token)
    except radio.RadioError as exc:
        raise _radio_http_error(exc) from exc
    except spotify.SpotifyError as exc:
        raise _spotify_http_error(exc) from exc

    radio.get_now_playing_cache().invalidate()
    return {"skipped": True}


@router.post("/api/radio/disconnect", dependencies=[Depends(require_operator)])
async def disconnect(db: Annotated[AsyncSession, Depends(get_db)]) -> dict[str, bool]:
    radio.reset()
    await audit.record(db, event="radio.disconnect", outcome=audit.OUTCOME_OK)
    await db.commit()
    await get_broker().publish(radio.RADIO_TOPIC, {"event": "radio_closed"})
    return {"connected": False}


# --------------------------------------------------------------------------
# Bisita: paghahanap at pag-request
# --------------------------------------------------------------------------


@router.get("/radio/{token}", response_class=HTMLResponse, include_in_schema=False)
async def radio_page(
    request: Request,
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    player = await _player_by_token(db, token)
    settings = get_settings()
    store = radio.get_connection_store()

    return templates.TemplateResponse(
        request,
        "radio.html",
        {
            "token": token,
            "player_name": player.given_name,
            # Ang bisita ay hindi kailangang makaalam kung bakit patay: pareho
            # ang ipinapakita kung walang credentials o walang naka-connect.
            "live": store.connected,
            "accepting": store.accepting,
            "cooldown_seconds": settings.radio_request_cooldown_seconds,
            "max_track_minutes": settings.radio_max_track_seconds // 60,
            "requests": await radio.recent_requests(db, limit=15),
        },
    )


@router.get("/api/radio/{token}/search", response_model=list[SearchResult])
async def search(
    request: Request,
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[str, Query(max_length=120)] = "",
) -> list[SearchResult]:
    settings = _radio_settings(for_guest=True)
    enforce_rate_limit(
        request,
        bucket="radio_search",
        limit=settings.radio_search_rate_limit_per_minute,
    )
    await _player_by_token(db, token)

    query = q.strip()
    if not query:
        return []

    try:
        token_value = await radio.get_connection_store().access_token(settings)
        found = await spotify.search_tracks(access_token=token_value, query=query)
    except radio.RadioError as exc:
        raise _radio_http_error(exc) from exc
    except spotify.SpotifyError as exc:
        raise _spotify_http_error(exc) from exc

    return [
        SearchResult(
            uri=track.uri,
            name=track.name,
            artist=track.artist,
            duration_ms=track.duration_ms,
            art_url=track.art_url,
        )
        for track in found
    ]


@router.post("/api/radio/{token}/request", response_model=RequestSongResponse)
async def request_song(
    request: Request,
    token: str,
    payload: RequestSongBody,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RequestSongResponse:
    settings = _radio_settings(for_guest=True)
    enforce_rate_limit(
        request,
        bucket="radio_request",
        limit=settings.radio_request_rate_limit_per_minute,
    )
    player = await _player_by_token(db, token)

    store = radio.get_connection_store()
    try:
        access = await store.access_token(settings)
        # Ang server ang kumukuha ng metadata. Ang phone ay nagbibigay lang ng URI.
        track = await spotify.fetch_track(access_token=access, track_uri=payload.track_uri)
        await radio.guard_request(db, settings, player=player, track=track)
    except radio.RadioError as exc:
        await audit.record(
            db,
            event="radio.request",
            outcome=audit.OUTCOME_DENIED,
            player_id=player.id,
            detail={"reason": exc.reason},
        )
        await db.commit()
        raise _radio_http_error(exc) from exc
    except spotify.SpotifyError as exc:
        await audit.record(
            db,
            event="radio.request",
            outcome=audit.OUTCOME_ERROR,
            player_id=player.id,
            detail={"reason": exc.reason},
        )
        await db.commit()
        raise _spotify_http_error(exc) from exc

    try:
        await spotify.add_to_queue(access_token=access, track_uri=track.uri)
    except spotify.SpotifyError as exc:
        # Itala pa rin ang bumagsak. Kung hindi, ang host ay walang matitingnan
        # kapag tahimik ang speaker at walang pumapasok na kanta.
        record = await radio.record_request(
            db, player=player, track=track, status=SONG_FAILED, failure_reason=exc.reason
        )
        await audit.record(
            db,
            event="radio.request",
            outcome=audit.OUTCOME_ERROR,
            player_id=player.id,
            detail={"reason": exc.reason},
        )
        await db.commit()
        await get_broker().publish(
            radio.RADIO_TOPIC,
            {
                "event": "song_requested",
                **radio.request_payload(record, requested_by=player.given_name),
            },
        )
        raise _spotify_http_error(exc) from exc

    record = await radio.record_request(db, player=player, track=track, status=SONG_QUEUED)
    await audit.record(
        db,
        event="radio.request",
        outcome=audit.OUTCOME_OK,
        player_id=player.id,
        # Walang pamagat ng kanta dito. Ang audit trail ay para sa "anong
        # nangyari", hindi para sa panlasa sa musika ng bisita.
        detail={"duration_ms": track.duration_ms},
    )
    await db.commit()

    await get_broker().publish(
        radio.RADIO_TOPIC,
        {
            "event": "song_requested",
            **radio.request_payload(record, requested_by=player.given_name),
        },
    )
    return RequestSongResponse(
        queued=True,
        message=f"{track.name} is in the queue. Salamat, {player.given_name}!",
        track_name=track.name,
        artist_name=track.artist,
    )


@router.get("/api/radio/{token}/now-playing")
async def guest_now_playing(
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Kasalukuyang tumutugtog. Naka-cache, kaya walang upstream call kada poll."""
    await _player_by_token(db, token)
    settings = get_settings()
    store = radio.get_connection_store()

    if not settings.radio_configured() or not store.connected:
        return {"live": False, "accepting": False, "now_playing": None}

    result: dict[str, Any] = {"live": True, "accepting": store.accepting, "now_playing": None}
    with contextlib.suppress(spotify.SpotifyError, radio.RadioError):
        # Kapag sarado ang Spotify ng host, walang tumutugtog — at wala ring
        # dapat ipakita sa bisita tungkol sa setup ng server.
        current = await radio.get_now_playing_cache().get(store, settings)
        result["now_playing"] = radio.now_playing_payload(current)
    return result


@router.websocket("/api/radio/host-events")
async def host_radio_events(websocket: WebSocket) -> None:
    """Parehong feed, pero para sa host console.

    Hiwalay na path dahil walang `play_token` ang host. Ang operator cookie ang
    tinitingnan: ipinapadala iyon ng browser sa WS handshake, at same-site naman
    ang koneksyon kaya hindi ito nahaharangan ng `SameSite=strict`. Walang CSRF
    check dito dahil read-only ang stream — walang naibabago sa pakikinig.
    """
    if get_session_store().get(websocket.cookies.get(OPERATOR_COOKIE)) is None:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    await _pump_radio_events(websocket)


@router.websocket("/api/radio/{token}/events")
async def radio_events(
    websocket: WebSocket,
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Live na feed ng mga hiniling na kanta.

    Push ito at hindi poll: ang bagong request ay galing sa isang tao, kaya
    walang upstream na binabayaran para malaman ito.
    """
    await websocket.accept()

    player = await db.scalar(select(Player).where(Player.play_token == token))
    if player is None:
        await websocket.close(code=4404)
        return

    await _pump_radio_events(websocket)


async def _pump_radio_events(websocket: WebSocket) -> None:
    async with get_broker().subscribe(radio.RADIO_TOPIC) as queue:
        try:
            await websocket.send_json(
                {
                    "event": "listening",
                    "accepting": radio.get_connection_store().accepting,
                }
            )
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
