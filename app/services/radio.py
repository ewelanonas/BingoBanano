"""Banano Radio: ang host ang kumakabit sa Spotify, ang mga bisita ang pumipili.

Isa lang ang Spotify account na kasangkot — ang host. Ang mga bisita ay walang
Spotify, walang login, at walang token. Ito ang buong dahilan kung bakit
gumagana ang feature na ito sa loob ng Development Mode ng Spotify: limang
authenticated user lang ang pinapayagan doon, at isa lang ang ginagamit natin.

## Bakit nasa memory ang refresh token

Ang refresh token ay pang-anim-na-buwang access sa Spotify queue ng host. Walang
encryption-at-rest sa repo na ito at SQLite file ang database, kaya ang
pagsusulat nito sa disk ay paglalagay ng pangmatagalang credential sa isang
file na madaling makopya. Kaya nasa memory lang ito, kasunod ng ginawa sa
`OperatorSessionStore`.

Ang kapalit: kapag nag-restart ang server — kasama ang auto-reload kapag may
nabago sa code — kailangang pindutin muli ng host ang Connect. Dalawang pindot
lang, at hindi nawawala ang bingo state dahil nasa DB naman iyon.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    SONG_COUNTED,
    SONG_FAILED,
    SONG_PLAYED,
    SONG_QUEUED,
    Player,
    SongRequest,
)
from app.domain.rng import new_play_token
from app.services import spotify
from app.services.identity import utcnow

# Ilang segundo bago ang expiry tayo nag-re-refresh. Kung hihintayin pa nating
# mag-401, ang bisita ang makakakita ng error na wala namang kasalanan.
_REFRESH_MARGIN_SECONDS = 60.0

# Gaano katagal bago mawala ang isang hindi nagamit na OAuth state.
_STATE_TTL_SECONDS = 600.0

RADIO_TOPIC = "radio"


class RadioError(RuntimeError):
    """Tinanggihan ang request. Ang `reason` ay para sa audit at sa host."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class RadioNotConnected(RadioError):
    def __init__(self) -> None:
        super().__init__(
            "Banano Radio is not connected to Spotify yet. Ask the host to hook it up.",
            reason="not_connected",
        )


@dataclass(frozen=True, slots=True)
class Connection:
    refresh_token: str
    access_token: str
    # `time.monotonic()`, hindi wall clock: hindi tumatalab ang pagpalit ng oras
    # ng makina at hindi kailangang mag-isip ng timezone.
    expires_at: float
    account_name: str
    scope: str


@dataclass(frozen=True, slots=True)
class RadioStatus:
    configured: bool
    connected: bool
    accepting: bool
    account_name: str


class ConnectionStore:
    """May hawak ng isang Spotify connection. Isang host, isang party."""

    def __init__(self) -> None:
        self._connection: Connection | None = None
        self._accepting = True
        self._lock = asyncio.Lock()

    def connect(self, grant: spotify.TokenGrant, *, account_name: str) -> None:
        if grant.refresh_token is None:
            raise RadioError(
                "Spotify did not return a refresh token, so the connection would "
                "drop after an hour. Try connecting again.",
                reason="no_refresh_token",
            )
        self._connection = Connection(
            refresh_token=grant.refresh_token,
            access_token=grant.access_token,
            expires_at=time.monotonic() + grant.expires_in,
            account_name=account_name,
            scope=grant.scope,
        )
        self._accepting = True

    def disconnect(self) -> None:
        self._connection = None

    @property
    def connected(self) -> bool:
        return self._connection is not None

    @property
    def accepting(self) -> bool:
        """`False` kapag pinatay ng host ang pagtanggap ng request.

        Kailangan ito ng host sa dulo ng party: hindi puwedeng patayin ang
        Spotify para tumigil ang mga request, dahil doon din nakikinig ang lahat.
        """
        return self._accepting and self.connected

    def set_accepting(self, value: bool) -> None:
        self._accepting = value

    def status(self, settings: Settings) -> RadioStatus:
        return RadioStatus(
            configured=settings.radio_configured(),
            connected=self.connected,
            accepting=self.accepting,
            account_name=self._connection.account_name if self._connection else "",
        )

    async def access_token(self, settings: Settings) -> str:
        """Ang kasalukuyang access token, ni-refresh kung kailangan.

        Ang lock ang pumipigil sa dalawampung bisitang sabay-sabay na
        nagre-request na maging dalawampung refresh call sa Spotify.
        """
        async with self._lock:
            connection = self._connection
            if connection is None:
                raise RadioNotConnected

            if connection.expires_at - _REFRESH_MARGIN_SECONDS > time.monotonic():
                return connection.access_token

            try:
                grant = await spotify.refresh_grant(
                    client_id=settings.spotify_client_id,
                    client_secret=settings.spotify_client_secret,
                    refresh_token=connection.refresh_token,
                )
            except spotify.SpotifyAuthError:
                # Patay na ang refresh token. Huwag itong itago: kailangang
                # makita ng host na disconnected na siya.
                self._connection = None
                raise

            self._connection = replace(
                connection,
                access_token=grant.access_token,
                expires_at=time.monotonic() + grant.expires_in,
                # Kapag nag-rotate si Spotify, ang bago ang susunod na gagamitin.
                refresh_token=grant.refresh_token or connection.refresh_token,
                scope=grant.scope or connection.scope,
            )
            return grant.access_token


class StateStore:
    """One-time `state` values para sa OAuth callback.

    Ang `state` ay hindi opsyonal na dekorasyon: kung wala ito, kayang i-trick
    ang naka-login na host na pindutin ang isang callback URL na ginawa ng iba,
    at maikakabit ang Spotify account ng ibang tao sa party niya. Single-use at
    short-lived ito, kagaya ng pairing nonce.
    """

    def __init__(self) -> None:
        self._issued: dict[str, float] = {}

    def issue(self) -> str:
        self._purge()
        state = secrets.token_urlsafe(32)
        self._issued[state] = time.monotonic() + _STATE_TTL_SECONDS
        return state

    def consume(self, state: str | None) -> bool:
        self._purge()
        if not state:
            return False
        # `pop` ang siyang single-use: ang pangalawang beses ay wala nang mabubunot.
        expires_at = self._issued.pop(state, None)
        return expires_at is not None and expires_at > time.monotonic()

    def _purge(self) -> None:
        now = time.monotonic()
        for state in [s for s, exp in self._issued.items() if exp <= now]:
            del self._issued[state]


class NowPlayingCache:
    """Isang upstream call kada TTL, gaano man karaming bisita ang nakatingin.

    Sa isang party, bawat phone ay may bukas na now-playing. Kung tuwid na
    dadaan ang bawat poll sa Spotify, ang app natin ang mag-rate-limit sa sarili.
    """

    def __init__(self) -> None:
        self._value: spotify.NowPlaying | None = None
        self._fresh_until = 0.0
        self._lock = asyncio.Lock()

    async def get(self, store: ConnectionStore, settings: Settings) -> spotify.NowPlaying:
        async with self._lock:
            if self._value is not None and self._fresh_until > time.monotonic():
                return self._value
            token = await store.access_token(settings)
            value = await spotify.now_playing(access_token=token)
            self._value = value
            self._fresh_until = time.monotonic() + settings.radio_now_playing_cache_seconds
            return value

    def invalidate(self) -> None:
        self._fresh_until = 0.0


_connections = ConnectionStore()
_states = StateStore()
_now_playing = NowPlayingCache()

# Ang huling track na na-reconcile na. Sa isang party, ang host console at ang
# bawat phone ay nagpo-poll ng now-playing, kaya kung walang bantay na ito ay
# isang database query kada poll kada bisita. Iisa lang naman ang kasagutan
# hanggang magpalit ng kanta.
_reconciled_uri: str | None = None


def claim_reconcile(playing_uri: str | None) -> bool:
    """`True` kapag bago ang tumutugtog at kailangang tingnan ang listahan."""
    global _reconciled_uri
    if playing_uri == _reconciled_uri:
        return False
    _reconciled_uri = playing_uri
    return True


def get_connection_store() -> ConnectionStore:
    return _connections


def get_state_store() -> StateStore:
    return _states


def get_now_playing_cache() -> NowPlayingCache:
    return _now_playing


def reset() -> None:
    """Ibalik sa blangko. Para sa tests, at para sa `disconnect` ng host."""
    global _reconciled_uri
    _connections.disconnect()
    _connections.set_accepting(True)
    _now_playing.invalidate()
    _reconciled_uri = None


async def create_guest(db: AsyncSession, *, nickname: str) -> Player:
    """Bisitang pumasok sa radio QR at walang bingo card.

    Parehong `Player` row gaya ng bumunot ng cards, at hindi bagong table.
    Palayaw lang ang naitatala, kagaya ng dati, at ang `play_token` ang siyang
    capability URL niya papunta sa radio.

    Isang `Player` na walang card ay ligtas: ang `/play/{token}` ay 404 kapag
    walang cards, at ang history ay nakalista ayon sa card, kaya hindi lumilitaw
    ang taong ito sa mga bingo page.
    """
    player = Player(
        given_name=nickname[:64],
        play_token=new_play_token(),
        created_at=utcnow(),
    )
    db.add(player)
    await db.flush()
    return player


async def guest_by_token(db: AsyncSession, token: str | None) -> Player | None:
    if not token:
        return None
    return await db.scalar(select(Player).where(Player.play_token == token))


async def guard_request(
    db: AsyncSession,
    settings: Settings,
    *,
    player: Player,
    track: spotify.Track,
) -> None:
    """Itapon ang request bago pa ito umabot sa Spotify.

    Tatlong hangganan, at bawat isa ay may pinagdaanan sa totoong party:
    sobrang haba ng kanta, paulit-ulit na kanta, at isang bisitang naging DJ.
    """
    store = get_connection_store()
    if not store.connected:
        raise RadioNotConnected
    if not store.accepting:
        raise RadioError(
            "The host closed song requests for now.",
            reason="closed",
        )

    if track.duration_seconds > settings.radio_max_track_seconds:
        minutes = settings.radio_max_track_seconds // 60
        raise RadioError(
            f"That track is longer than {minutes} minutes. Pick a shorter one.",
            reason="too_long",
        )

    now = utcnow()

    if settings.radio_duplicate_window_minutes:
        window_start = now - timedelta(minutes=settings.radio_duplicate_window_minutes)
        # Kasama ang `played` dito. Kung `queued` lang ang titingnan, ang kantang
        # katapos-tapos lang ay puwede nang i-request muli, at ang paulit-ulit na
        # kanta ang eksaktong pinipigilan ng window na ito.
        already = await db.scalar(
            select(SongRequest.status)
            .where(
                SongRequest.track_uri == track.uri,
                SongRequest.status.in_(SONG_COUNTED),
                SongRequest.requested_at >= window_start,
            )
            .order_by(SongRequest.requested_at.desc())
            .limit(1)
        )
        if already is not None:
            raise RadioError(
                f"{track.name} just played."
                if already == SONG_PLAYED
                else f"{track.name} is already in the queue.",
                reason="duplicate",
            )

    if settings.radio_request_cooldown_seconds:
        cooldown_start = now - timedelta(seconds=settings.radio_request_cooldown_seconds)
        # `SONG_COUNTED` at hindi `SONG_QUEUED`: kapag nagsimula nang tumugtog ang
        # kanta ng bisita, hindi dapat mag-reset ang cooldown niya.
        recent = await db.scalar(
            select(SongRequest.requested_at)
            .where(
                SongRequest.player_id == player.id,
                SongRequest.status.in_(SONG_COUNTED),
                SongRequest.requested_at >= cooldown_start,
            )
            .order_by(SongRequest.requested_at.desc())
            .limit(1)
        )
        if recent is not None:
            wait = settings.radio_request_cooldown_seconds - int((now - recent).total_seconds())
            raise RadioError(
                f"You just picked a song. Try again in {max(wait, 1)}s so everyone gets a turn.",
                reason="cooldown",
            )


async def record_request(
    db: AsyncSession,
    *,
    player: Player,
    track: spotify.Track,
    status: str,
    failure_reason: str = "",
) -> SongRequest:
    """Itala ang request, tinanggap man o hindi.

    Ang bumagsak ay itinatala din. Iyon lang ang paraan para malaman ng host na
    sarado ang Spotify niya at hindi pumapasok ang kahit ano.
    """
    record = SongRequest(
        player_id=player.id,
        track_uri=track.uri,
        track_name=track.name[:200],
        artist_name=track.artist[:200],
        duration_ms=track.duration_ms,
        requested_at=utcnow(),
        status=status,
        failure_reason=failure_reason[:64],
    )
    db.add(record)
    await db.flush()
    return record


async def pending_requests(db: AsyncSession, *, limit: int = 30) -> list[dict[str, object]]:
    """Ang mga kantang naghihintay pa. Naunang hiniling, naunang tutugtog.

    Pataas ang pagkakasunod dahil queue ito at hindi history: ang nasa itaas ay
    ang susunod na tutugtog. Ang natapos na at ang bumagsak ay wala na dito.
    """
    rows = (
        await db.execute(
            select(SongRequest, Player.given_name)
            .join(Player, Player.id == SongRequest.player_id)
            .where(SongRequest.status == SONG_QUEUED)
            .order_by(SongRequest.requested_at)
            .limit(limit)
        )
    ).all()
    return [request_payload(record, requested_by=name) for record, name in rows]


async def mark_played(db: AsyncSession, *, up_to: datetime) -> list[str]:
    """Tanggalin sa queue ang lahat ng hiniling hanggang sa `up_to`.

    Ibinabalik ang mga id na naapektuhan, para maalis din ito sa mga bukas na
    page nang hindi kailangang mag-reload.
    """
    ids = list(
        (
            await db.scalars(
                select(SongRequest.id).where(
                    SongRequest.status == SONG_QUEUED,
                    SongRequest.requested_at <= up_to,
                )
            )
        ).all()
    )
    if not ids:
        return []

    await db.execute(
        update(SongRequest)
        .where(SongRequest.id.in_(ids))
        .values(status=SONG_PLAYED)
        .execution_options(synchronize_session=False)
    )
    return ids


async def reconcile_played(db: AsyncSession, *, playing_uri: str | None) -> list[str]:
    """Ihanay ang listahan natin sa aktuwal na tumutugtog sa Spotify.

    Kapag ang tumutugtog ngayon ay isang hiniling na kanta, ang lahat ng hiniling
    bago pa nito ay tapos na — nalampasan na ng playback. Ang tumutugtog mismo ay
    tinatanggal din: may sariling now-playing panel na iyon, at dalawang beses
    lumitaw ang parehong kanta ang mismong ikinalilito.

    Ang paglahat hanggang sa tumutugtog ay siya ring pumupuno ng butas: kapag
    walang bukas na page, walang nagre-reconcile, at kapag bumukas muli ay
    naiwan na ang lima o anim na kanta. Isang pagtingin ang naglilinis ng lahat.

    Walang commit dito. Ang tumatawag ang nagsasara ng transaction, kagaya ng
    ibang service sa repo.
    """
    if not playing_uri:
        return []

    playing = await db.scalar(
        select(SongRequest.requested_at)
        .where(
            SongRequest.track_uri == playing_uri,
            SongRequest.status == SONG_QUEUED,
        )
        # Ang pinakamaaga ang siyang tumutugtog kung nadoble ang kanta sa queue.
        .order_by(SongRequest.requested_at)
        .limit(1)
    )
    if playing is None:
        # Kanta ng host mismo, o galing sa playlist niya. Walang gagalawin —
        # hindi natin alam kung nasaan sa queue ang mga hiniling.
        return []

    return await mark_played(db, up_to=playing)


def request_payload(record: SongRequest, *, requested_by: str) -> dict[str, object]:
    return {
        "id": record.id,
        "track_name": record.track_name,
        "artist_name": record.artist_name,
        "requested_by": requested_by,
        "requested_at": record.requested_at.isoformat(),
        "status": record.status,
        "failure_reason": record.failure_reason,
    }


def now_playing_payload(state: spotify.NowPlaying) -> dict[str, object]:
    track = state.track
    return {
        "is_playing": state.is_playing,
        "track_name": track.name if track else "",
        "artist_name": track.artist if track else "",
        "art_url": track.art_url if track else "",
        "duration_ms": track.duration_ms if track else 0,
        "progress_ms": state.progress_ms,
        "device_name": state.device_name,
    }


__all__ = [
    "RADIO_TOPIC",
    "SONG_FAILED",
    "SONG_PLAYED",
    "SONG_QUEUED",
    "ConnectionStore",
    "NowPlayingCache",
    "RadioError",
    "RadioNotConnected",
    "RadioStatus",
    "StateStore",
    "claim_reconcile",
    "create_guest",
    "get_connection_store",
    "get_now_playing_cache",
    "get_state_store",
    "guard_request",
    "guest_by_token",
    "mark_played",
    "now_playing_payload",
    "pending_requests",
    "reconcile_played",
    "record_request",
    "request_payload",
    "reset",
]
