"""Manipis na async client para sa Spotify Web API.

Walang FastAPI at walang SQLAlchemy dito, at wala ring naka-store na state — ang
access token ay ipinapasa bilang argumento. Ang may hawak ng token ay ang
`app/services/radio.py`.

Tatlong bagay ang dapat malaman bago galawin ito:

1. **Premium ang kailangan.** Ang `POST /me/player/queue` ay 403 sa libreng
   account. Kailangan din ng Spotify na Premium ang may-ari ng isang Development
   Mode na app, kaya walang paraan para umiwas dito.
2. **Kailangan ng aktibong device.** Ang queue ay pumapasok sa kung saan
   TUMUTUGTOG ngayon ang host. Kapag sarado ang Spotify app niya, 404
   `NO_ACTIVE_DEVICE` ang sagot at hindi ito bug ng app natin.
3. **Ang search ay may hangganan na 10** kada request sa Development Mode
   (Feb 2026 na pagbabago), at wala nang `popularity` sa track object — kaya
   hindi puwedeng i-sort ayon sa sikat.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

ACCOUNTS_BASE = "https://accounts.spotify.com"
API_BASE = "https://api.spotify.com/v1"

# `user-modify-playback-state` ang nagpapasok ng kanta sa queue at nag-skip.
# Ang dalawa pang read scope ay para sa now-playing na ipinapakita sa mga bisita.
# Wala tayong hinihinging scope sa library o sa playlist: hindi natin binabago
# ang account ng host, ang queue lang ng kasalukuyang playback.
SCOPES = (
    "user-modify-playback-state",
    "user-read-playback-state",
    "user-read-currently-playing",
)

# Ang track URI ay galing sa phone ng bisita, kaya hindi ito pinagkakatiwalaan.
# Ipinapasok natin ito sa isang query string papuntang Spotify, kaya i-validate
# ang shape bago pa ito lumabas ng proseso.
_TRACK_URI = re.compile(r"^spotify:track:[A-Za-z0-9]{22}$")

_HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_client: httpx.AsyncClient | None = None


class SpotifyError(RuntimeError):
    """Bumagsak ang usapan sa Spotify.

    Ang `reason` ay maikling machine-readable na code na puwedeng ipakita sa
    host at itala sa audit. Ang `str(exc)` ay ang user-facing na pangungusap.
    """

    def __init__(self, message: str, *, reason: str = "spotify_error") -> None:
        super().__init__(message)
        self.reason = reason


class SpotifyAuthError(SpotifyError):
    """Hindi na tanggap ang token. Kailangang mag-connect muli ang host."""

    def __init__(self, message: str) -> None:
        super().__init__(message, reason="reauthorize")


@dataclass(frozen=True, slots=True)
class TokenGrant:
    access_token: str
    expires_in: int
    scope: str
    # Sa refresh ay hindi laging nagbabalik ng bago si Spotify. Kapag `None`,
    # panatilihin ang dati.
    refresh_token: str | None


@dataclass(frozen=True, slots=True)
class Track:
    uri: str
    name: str
    artist: str
    duration_ms: int
    art_url: str

    @property
    def duration_seconds(self) -> int:
        return self.duration_ms // 1000


@dataclass(frozen=True, slots=True)
class NowPlaying:
    is_playing: bool
    track: Track | None
    progress_ms: int
    device_name: str


def is_track_uri(candidate: str) -> bool:
    return bool(_TRACK_URI.match(candidate))


def get_client() -> httpx.AsyncClient:
    """Isang shared client para may connection pooling at TLS reuse.

    Ang search-as-you-type ay maraming maliit na request. Kung bagong client
    kada keystroke, bagong TLS handshake din kada keystroke.
    """
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT)
    return _client


async def aclose_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def authorize_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    """Ang URL kung saan ipapadala ang browser ng host para mag-sign in."""
    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": " ".join(SCOPES),
            # Laging ipakita ang consent screen. Ganito lang malinaw sa host kung
            # aling Spotify account ang ikinakabit niya — madaling makabit ang
            # maling account kapag may dalawang naka-log in sa browser.
            "show_dialog": "true",
        }
    )
    return f"{ACCOUNTS_BASE}/authorize?{query}"


def _basic_auth(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode()
    return f"Basic {base64.b64encode(raw).decode()}"


async def _token_request(
    *,
    client_id: str,
    client_secret: str,
    form: dict[str, str],
) -> TokenGrant:
    try:
        response = await get_client().post(
            f"{ACCOUNTS_BASE}/api/token",
            data=form,
            headers={
                # Confidential client: ang secret ay nananatili sa server at
                # hindi kailanman umaabot sa browser, kaya Basic auth ang tama
                # dito at hindi PKCE.
                "Authorization": _basic_auth(client_id, client_secret),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
    except httpx.HTTPError as exc:
        raise SpotifyError(
            "Could not reach Spotify. Check this machine's internet connection.",
            reason="network",
        ) from exc

    if response.status_code >= 400:
        # Ang body dito ay puwedeng may kasamang detalye ng credential problem.
        # Hindi ito ipinapakita sa user; ang code lang ang ipinapaalam.
        body = _safe_json(response)
        code = str(body.get("error", "")) or f"http_{response.status_code}"
        if code in {"invalid_grant", "invalid_client"}:
            raise SpotifyAuthError(
                "Spotify rejected the sign-in. Connect the account again, and "
                "check that the Client ID, Client Secret and Redirect URI match "
                "the app in your Spotify dashboard."
            )
        raise SpotifyError("Spotify refused the token request.", reason=code)

    payload = _safe_json(response)
    access = str(payload.get("access_token", ""))
    if not access:
        raise SpotifyError("Spotify returned no access token.", reason="empty_token")

    rotated = payload.get("refresh_token")
    return TokenGrant(
        access_token=access,
        expires_in=int(payload.get("expires_in", 3600)),
        scope=str(payload.get("scope", "")),
        refresh_token=str(rotated) if rotated else None,
    )


async def exchange_code(
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
) -> TokenGrant:
    return await _token_request(
        client_id=client_id,
        client_secret=client_secret,
        form={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )


async def refresh_grant(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> TokenGrant:
    """Bagong access token gamit ang refresh token.

    Tandaan: nag-e-expire na rin ang refresh token (anim na buwan, simula sa
    pagbabago ng Hunyo 2026). Kapag nangyari, `SpotifyAuthError` ang lalabas at
    kailangang mag-sign in muli ang host.
    """
    return await _token_request(
        client_id=client_id,
        client_secret=client_secret,
        form={"grant_type": "refresh_token", "refresh_token": refresh_token},
    )


async def _api_call(
    method: str,
    path: str,
    *,
    access_token: str,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    try:
        response = await get_client().request(
            method,
            f"{API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except httpx.HTTPError as exc:
        raise SpotifyError(
            "Could not reach Spotify. Check this machine's internet connection.",
            reason="network",
        ) from exc

    if response.status_code == 401:
        raise SpotifyAuthError("The Spotify session expired. Connect the account again.")
    if response.status_code == 403:
        # Ito ang pinakamadalas sa totoong buhay: libreng account ang naka-kabit.
        raise SpotifyError(
            "Spotify refused this action. The connected account needs Spotify Premium.",
            reason="premium_required",
        )
    if response.status_code == 404:
        raise SpotifyError(
            "Spotify has no active device. Open Spotify on the party speaker and "
            "start playing something, then try again.",
            reason="no_active_device",
        )
    if response.status_code == 429:
        raise SpotifyError(
            "Spotify is rate limiting us. Give it a minute.",
            reason="upstream_rate_limited",
        )
    if response.status_code >= 400:
        raise SpotifyError(
            "Spotify could not handle that request.",
            reason=f"http_{response.status_code}",
        )
    return response


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _track_from(raw: dict[str, Any]) -> Track:
    artists = raw.get("artists") or []
    names = [str(a.get("name", "")) for a in artists if isinstance(a, dict)]
    album = raw.get("album") if isinstance(raw.get("album"), dict) else {}
    images = album.get("images") or []
    art = ""
    if images and isinstance(images[-1], dict):
        # Ang huli ay ang pinakamaliit. Mobile data ang karamihan ng bisita.
        art = str(images[-1].get("url", ""))
    return Track(
        uri=str(raw.get("uri", "")),
        name=str(raw.get("name", "")) or "Unknown track",
        artist=", ".join(n for n in names if n) or "Unknown artist",
        duration_ms=int(raw.get("duration_ms") or 0),
        art_url=art,
    )


async def search_tracks(*, access_token: str, query: str, limit: int = 10) -> list[Track]:
    """Maghanap ng kanta. Ang `limit` ay may hangganang 10 sa Development Mode."""
    response = await _api_call(
        "GET",
        "/search",
        access_token=access_token,
        params={"q": query, "type": "track", "limit": str(min(limit, 10))},
    )
    payload = _safe_json(response)
    tracks = payload.get("tracks") if isinstance(payload.get("tracks"), dict) else {}
    items = tracks.get("items") or []
    found = [_track_from(item) for item in items if isinstance(item, dict)]
    # Ang walang URI ay hindi ma-queue, kaya walang saysay ipakita.
    return [track for track in found if is_track_uri(track.uri)]


async def fetch_track(*, access_token: str, track_uri: str) -> Track:
    """Ang server ang kumukuha ng pamagat at haba, hindi ang phone.

    Kung ang phone ang magsasabi ng haba, ang duration cap ay puwedeng lampasan
    sa pagsisinungaling lang.
    """
    if not is_track_uri(track_uri):
        raise SpotifyError("That is not a Spotify track.", reason="bad_uri")
    track_id = track_uri.rsplit(":", 1)[-1]
    response = await _api_call("GET", f"/tracks/{track_id}", access_token=access_token)
    return _track_from(_safe_json(response))


async def add_to_queue(*, access_token: str, track_uri: str) -> None:
    if not is_track_uri(track_uri):
        raise SpotifyError("That is not a Spotify track.", reason="bad_uri")
    await _api_call(
        "POST",
        "/me/player/queue",
        access_token=access_token,
        params={"uri": track_uri},
    )


async def skip_next(*, access_token: str) -> None:
    await _api_call("POST", "/me/player/next", access_token=access_token)


async def now_playing(*, access_token: str) -> NowPlaying:
    response = await _api_call("GET", "/me/player", access_token=access_token)
    if response.status_code == 204:
        # Walang bukas na Spotify. Hindi ito error — patay lang ang tugtog.
        return NowPlaying(is_playing=False, track=None, progress_ms=0, device_name="")

    payload = _safe_json(response)
    item = payload.get("item") if isinstance(payload.get("item"), dict) else None
    device = payload.get("device") if isinstance(payload.get("device"), dict) else {}
    return NowPlaying(
        is_playing=bool(payload.get("is_playing")),
        track=_track_from(item) if item else None,
        progress_ms=int(payload.get("progress_ms") or 0),
        device_name=str(device.get("name", "")),
    )


async def account_name(*, access_token: str) -> str:
    """Palayaw lang ng naka-kabit na account, para makita ng host kung tama.

    Sinasadyang hindi hinahawakan ang email: tinanggal na ito ng Spotify sa
    `GET /me` para sa Development Mode, at hindi rin natin kailangan.
    """
    response = await _api_call("GET", "/me", access_token=access_token)
    payload = _safe_json(response)
    return str(payload.get("display_name") or payload.get("id") or "Spotify account")
