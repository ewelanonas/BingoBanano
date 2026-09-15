"""Banano Radio: ang mga path na aktuwal na naaabuso o nasisira.

Ang mga sinusubok dito ay: fail closed kapag walang Spotify, replay ng OAuth
state, at ang tatlong guardrail sa panig ng bisita (cooldown, duplicate,
sobrang haba). Walang totoong network call — ang buong `app.services.spotify`
ay pinapalitan.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.config import get_settings
from app.db.models import SONG_FAILED, SONG_QUEUED, AuditEvent, Player, SongRequest
from app.db.session import get_session_factory
from app.services import radio, spotify
from app.services.identity import utcnow

CLIENT_ID = "test-spotify-client-id"
REDIRECT_URI = "http://127.0.0.1:8000/operator/radio/callback"

TRACK = spotify.Track(
    uri="spotify:track:4cOdK2wGLETKBW3PvgPWqT",
    name="Kailan",
    artist="Smokey Mountain",
    duration_ms=214_000,
    art_url="https://i.scdn.co/image/whatever",
)
OTHER_TRACK = spotify.Track(
    uri="spotify:track:1301WleyT98MSxVHPZCA6M",
    name="Harana",
    artist="Parokya ni Edgar",
    duration_ms=232_000,
    art_url="",
)


async def _play_token(client: AsyncClient, headers: dict[str, str], name: str = "Ana") -> str:
    """Dumaan sa totoong pairing flow para makakuha ng play token ng bisita."""
    game = await client.post("/api/rounds", json={"pattern": "any_line"}, headers=headers)
    assert game.status_code == 200, game.text
    pairing = await client.post(
        "/api/pairing", json={"round_id": game.json()["id"]}, headers=headers
    )
    assert pairing.status_code == 200, pairing.text
    nonce = str(pairing.json()["pair_url"]).rsplit("/", 1)[-1]

    claim = await client.post(f"/pair/{nonce}/claim", data={"given_name": name})
    assert claim.status_code == 303, claim.text
    # Ang redirect ay `/play/{token}?welcome=1`.
    return claim.headers["location"].removeprefix("/play/").split("?", 1)[0]


@pytest.fixture
async def configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    """May Spotify credentials ang server, pero walang naka-connect pa."""
    monkeypatch.setenv("BINGO_SPOTIFY_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("BINGO_SPOTIFY_CLIENT_SECRET", "test-spotify-client-secret")
    monkeypatch.setenv("BINGO_SPOTIFY_REDIRECT_URI", REDIRECT_URI)
    monkeypatch.setenv("BINGO_RADIO_REQUEST_COOLDOWN_SECONDS", "120")
    monkeypatch.setenv("BINGO_RADIO_MAX_TRACK_SECONDS", "600")
    monkeypatch.setenv("BINGO_RADIO_DUPLICATE_WINDOW_MINUTES", "60")
    # Naka-cache ang settings, kaya kailangang burahin bago makita ang bago.
    get_settings.cache_clear()
    yield client
    get_settings.cache_clear()


def connect_store() -> None:
    """Ikabit ang Spotify nang hindi dumadaan sa network."""
    radio.get_connection_store().connect(
        spotify.TokenGrant(
            access_token="test-access-token",
            expires_in=3600,
            scope=" ".join(spotify.SCOPES),
            refresh_token="test-refresh-token",
        ),
        account_name="Party Host",
    )


def stub_now_playing(monkeypatch: pytest.MonkeyPatch, track: spotify.Track | None) -> None:
    """Itakda ang tumutugtog sa Spotify, at burahin ang cache para makita agad."""

    async def fake(*, access_token: str) -> spotify.NowPlaying:
        return spotify.NowPlaying(
            is_playing=track is not None,
            track=track,
            progress_ms=1_000,
            device_name="Party Speaker",
        )

    monkeypatch.setattr(spotify, "now_playing", fake)
    radio.get_now_playing_cache().invalidate()


def stub_spotify(
    monkeypatch: pytest.MonkeyPatch,
    *,
    track: spotify.Track = TRACK,
    queue_error: spotify.SpotifyError | None = None,
) -> list[str]:
    """Palitan ang Spotify. Ibinabalik ang listahan ng na-queue na URI."""
    queued: list[str] = []

    async def fake_fetch(*, access_token: str, track_uri: str) -> spotify.Track:
        if not spotify.is_track_uri(track_uri):
            raise spotify.SpotifyError("That is not a Spotify track.", reason="bad_uri")
        return track if track_uri == track.uri else OTHER_TRACK

    async def fake_queue(*, access_token: str, track_uri: str) -> None:
        if queue_error is not None:
            raise queue_error
        queued.append(track_uri)

    async def fake_search(*, access_token: str, query: str, limit: int = 10) -> list[spotify.Track]:
        return [track]

    monkeypatch.setattr(spotify, "fetch_track", fake_fetch)
    monkeypatch.setattr(spotify, "add_to_queue", fake_queue)
    monkeypatch.setattr(spotify, "search_tracks", fake_search)
    return queued


# --------------------------------------------------------------------------
# Walang Spotify: patay ang radio, buhay ang bingo
# --------------------------------------------------------------------------


async def test_bingo_still_works_without_spotify(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Ang radio ay opsyonal. Hindi puwedeng hilahin nito ang buong app."""
    token = await _play_token(client, operator_headers)
    board = await client.get(f"/play/{token}")
    assert board.status_code == 200
    # Walang link sa radio kapag walang naka-kabit — patay na link iyon.
    assert "/radio/" not in board.text


async def test_guest_request_is_denied_when_not_configured(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    token = await _play_token(client, operator_headers)
    response = await client.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})
    assert response.status_code == 503
    # Walang tagubilin tungkol sa .env na napupunta sa bisita.
    assert "BINGO_SPOTIFY" not in response.text


async def test_guest_request_is_denied_when_configured_but_not_connected(
    configured: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Fail closed: may credentials pero walang naka-sign in na host."""
    token = await _play_token(configured, operator_headers)
    response = await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})
    assert response.status_code == 503

    async with get_session_factory()() as db:
        assert await db.scalar(select(SongRequest.id)) is None


async def test_host_page_shows_setup_when_not_configured(client: AsyncClient) -> None:
    login = await client.post("/operator/session", data={"api_key": "wrong-key"})
    assert login.status_code == 303

    from tests.conftest import TEST_API_KEY

    signed_in = await client.post("/operator/session", data={"api_key": TEST_API_KEY})
    assert signed_in.status_code == 303

    page = await client.get("/operator/radio")
    assert page.status_code == 200
    # Ang buong punto ng page kapag walang setup: sabihin ang eksaktong URI.
    assert REDIRECT_URI in page.text
    assert "Spotify Premium" in page.text


async def test_host_console_renders_when_connected(configured: AsyncClient) -> None:
    """Ang naka-connect na branch ng page ay may sariling markup at script.

    Kung walang test dito, ang isang maling Jinja variable sa branch na ito ay
    lalabas na lang sa gitna ng party.
    """
    from tests.conftest import TEST_API_KEY

    assert (
        await configured.post("/operator/session", data={"api_key": TEST_API_KEY})
    ).status_code == 303

    connect_store()
    page = await configured.get("/operator/radio")
    assert page.status_code == 200
    assert "Party Host" in page.text
    assert "Close requests" in page.text
    assert "/api/radio/host-events" in page.text


async def test_console_warns_when_redirect_uri_host_does_not_match(
    configured: AsyncClient,
) -> None:
    """Ang mismatch ay sinasabi bago pa pindutin ang Connect.

    Kung hindi, ang tanging makikita ng host ay `INVALID_CLIENT: Invalid
    redirect URI` mula sa Spotify, na hindi sinasabi kung alin ang mali.
    """
    from tests.conftest import TEST_API_KEY

    await configured.post("/operator/session", data={"api_key": TEST_API_KEY})

    # Ang `.env` ay nakatutok sa loopback, pero ang host ay dumaan sa tunnel.
    page = await configured.get(
        "/operator/radio", headers={"Host": "disk-propose-financial.ngrok-free.dev"}
    )
    assert page.status_code == 200
    assert "127.0.0.1:8000" in page.text
    assert "disk-propose-financial.ngrok-free.dev/operator/radio/callback" in page.text


async def test_console_has_no_warning_when_hosts_match(
    configured: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.conftest import TEST_API_KEY

    monkeypatch.setenv(
        "BINGO_SPOTIFY_REDIRECT_URI",
        "https://disk-propose-financial.ngrok-free.dev/operator/radio/callback",
    )
    get_settings.cache_clear()

    await configured.post("/operator/session", data={"api_key": TEST_API_KEY})
    page = await configured.get(
        "/operator/radio", headers={"Host": "disk-propose-financial.ngrok-free.dev"}
    )
    assert page.status_code == 200
    assert "character for character" not in page.text


async def test_guest_page_renders_search_when_live(
    configured: AsyncClient, operator_headers: dict[str, str]
) -> None:
    connect_store()
    token = await _play_token(configured, operator_headers)
    page = await configured.get(f"/radio/{token}")
    assert page.status_code == 200
    assert "Search for a song" in page.text
    assert f"/play/{token}" in page.text
    assert "The radio is off right now" not in page.text


async def test_operator_routes_require_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/radio/status")).status_code == 401
    assert (await client.post("/api/radio/skip")).status_code == 401
    assert (await client.post("/api/radio/disconnect")).status_code == 401
    # Ang HTML page ay nagre-redirect sa login at hindi nagbibigay ng 401.
    console = await client.get("/operator/radio")
    assert console.status_code == 303
    assert console.headers["location"] == "/operator"


# --------------------------------------------------------------------------
# OAuth callback
# --------------------------------------------------------------------------


async def test_callback_rejects_unknown_state(configured: AsyncClient) -> None:
    response = await configured.get(
        "/operator/radio/callback", params={"code": "abc", "state": "never-issued"}
    )
    assert response.status_code == 400
    assert not radio.get_connection_store().connected


async def test_callback_rejects_missing_state(configured: AsyncClient) -> None:
    response = await configured.get("/operator/radio/callback", params={"code": "abc"})
    assert response.status_code == 400
    assert not radio.get_connection_store().connected


async def test_state_is_single_use(
    configured: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ang pangalawang paggamit ng parehong state ay tinatanggihan.

    Kung hindi single-use, ang naka-save na callback URL ay puwedeng gamitin
    muli para ikabit ang Spotify ng ibang tao sa party ng host.
    """

    async def fake_exchange(**_: object) -> spotify.TokenGrant:
        return spotify.TokenGrant(
            access_token="test-access-token",
            expires_in=3600,
            scope=" ".join(spotify.SCOPES),
            refresh_token="test-refresh-token",
        )

    async def fake_name(**_: object) -> str:
        return "Party Host"

    monkeypatch.setattr(spotify, "exchange_code", fake_exchange)
    monkeypatch.setattr(spotify, "account_name", fake_name)

    state = radio.get_state_store().issue()

    first = await configured.get("/operator/radio/callback", params={"code": "abc", "state": state})
    assert first.status_code == 200
    assert radio.get_connection_store().connected

    second = await configured.get(
        "/operator/radio/callback", params={"code": "abc", "state": state}
    )
    assert second.status_code == 400


async def test_callback_records_denial_without_leaking_state(configured: AsyncClient) -> None:
    await configured.get("/operator/radio/callback", params={"state": "bogus"})

    async with get_session_factory()() as db:
        event = await db.scalar(select(AuditEvent).where(AuditEvent.event == "radio.connect"))
        assert event is not None
        assert event.outcome == "denied"
        # Ang state ay credential. Wala itong dapat na bakas sa audit row.
        assert "bogus" not in str(event.detail)
        assert event.nonce_hash is None


# --------------------------------------------------------------------------
# Bisita: happy path at ang tatlong guardrail
# --------------------------------------------------------------------------


async def test_guest_can_queue_a_song(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connect_store()
    queued = stub_spotify(monkeypatch)
    token = await _play_token(configured, operator_headers)

    response = await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["queued"] is True
    assert body["track_name"] == TRACK.name
    assert queued == [TRACK.uri]

    async with get_session_factory()() as db:
        record = await db.scalar(select(SongRequest))
        assert record is not None
        assert record.status == SONG_QUEUED
        # Ang pamagat ay galing sa Spotify at hindi sa phone.
        assert record.track_name == TRACK.name
        assert record.artist_name == TRACK.artist


async def test_radio_link_appears_on_the_play_page_once_connected(
    configured: AsyncClient, operator_headers: dict[str, str]
) -> None:
    connect_store()
    token = await _play_token(configured, operator_headers)
    board = await configured.get(f"/play/{token}")
    assert f"/radio/{token}" in board.text


async def test_audit_does_not_store_the_song_title(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ang audit ay para sa 'anong nangyari', hindi sa panlasa sa musika."""
    connect_store()
    stub_spotify(monkeypatch)
    token = await _play_token(configured, operator_headers)
    await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})

    async with get_session_factory()() as db:
        event = await db.scalar(select(AuditEvent).where(AuditEvent.event == "radio.request"))
        assert event is not None
        assert event.outcome == "ok"
        assert TRACK.name not in str(event.detail)


async def test_cooldown_blocks_the_same_guest_twice(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connect_store()
    queued = stub_spotify(monkeypatch)
    token = await _play_token(configured, operator_headers)

    first = await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})
    assert first.status_code == 200

    second = await configured.post(
        f"/api/radio/{token}/request", json={"track_uri": OTHER_TRACK.uri}
    )
    assert second.status_code == 409
    assert "everyone gets a turn" in second.json()["detail"]
    # Hindi umabot sa Spotify ang pangalawa.
    assert queued == [TRACK.uri]


async def test_duplicate_track_is_refused(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connect_store()
    stub_spotify(monkeypatch)
    ana = await _play_token(configured, operator_headers, "Ana")
    ben = await _play_token(configured, operator_headers, "Ben")

    assert (
        await configured.post(f"/api/radio/{ana}/request", json={"track_uri": TRACK.uri})
    ).status_code == 200

    # Ibang bisita, kaya hindi cooldown ang bumabara kundi ang duplicate check.
    second = await configured.post(f"/api/radio/{ben}/request", json={"track_uri": TRACK.uri})
    assert second.status_code == 409
    assert "already in the queue" in second.json()["detail"]


async def test_duplicate_check_expires_with_the_window(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pagkalipas ng window, puwede nang i-request muli ang kantang tapos na.

    Ang window ay tungkol sa "gaano katagal pagkatapos tumugtog", kaya kailangang
    lumabas na ito sa queue bago mag-usap tungkol sa expiry.
    """
    connect_store()
    stub_spotify(monkeypatch)
    ana = await _play_token(configured, operator_headers, "Ana")
    ben = await _play_token(configured, operator_headers, "Ben")

    assert (
        await configured.post(f"/api/radio/{ana}/request", json={"track_uri": TRACK.uri})
    ).status_code == 200

    stub_now_playing(monkeypatch, TRACK)
    await configured.get(f"/api/radio/{ana}/now-playing")

    async with get_session_factory()() as db:
        record = await db.scalar(select(SongRequest))
        assert record is not None
        assert record.status == "played"
        record.requested_at = utcnow() - timedelta(minutes=90)
        await db.commit()

    assert (
        await configured.post(f"/api/radio/{ben}/request", json={"track_uri": TRACK.uri})
    ).status_code == 200


async def test_a_queued_song_is_blocked_no_matter_how_old(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Habang nakapila, hindi puwedeng maulit — kahit lumipas na ang window.

    Ang window ay hindi nagpapahintulot ng dalawang kopya sa queue. Ito ang
    hangganang hawak ng unique index at hindi ng Python check.
    """
    connect_store()
    stub_spotify(monkeypatch)
    ana = await _play_token(configured, operator_headers, "Ana")
    ben = await _play_token(configured, operator_headers, "Ben")

    await configured.post(f"/api/radio/{ana}/request", json={"track_uri": TRACK.uri})

    async with get_session_factory()() as db:
        record = await db.scalar(select(SongRequest))
        assert record is not None
        record.requested_at = utcnow() - timedelta(minutes=90)
        await db.commit()

    blocked = await configured.post(f"/api/radio/{ben}/request", json={"track_uri": TRACK.uri})
    assert blocked.status_code == 409
    assert await _statuses() == ["queued"]


async def test_same_song_under_a_different_track_id_is_refused(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ang butas na aktuwal na natagpuan sa party.

    Ang isang awit ay may hiwalay na track ID kada paglabas — single, album,
    remaster, compilation — at magkatabi ang mga ito sa search results. Ang
    paghahambing ng URI lang ay nagpapapasok ng paulit-ulit na kanta sa isang
    pindot ng ibang linya sa parehong listahan.
    """
    remaster = spotify.Track(
        uri="spotify:track:5AbCdEfGhIjKlMnOpQrStU",
        name=f"{TRACK.name} - Remastered 2011",
        artist=f"{TRACK.artist}, Jolina Magdangal",
        duration_ms=TRACK.duration_ms,
        art_url="",
    )

    async def fake_fetch(*, access_token: str, track_uri: str) -> spotify.Track:
        return TRACK if track_uri == TRACK.uri else remaster

    async def fake_queue(*, access_token: str, track_uri: str) -> None:
        return None

    monkeypatch.setattr(spotify, "fetch_track", fake_fetch)
    monkeypatch.setattr(spotify, "add_to_queue", fake_queue)
    connect_store()
    ana = await _play_token(configured, operator_headers, "Ana")
    ben = await _play_token(configured, operator_headers, "Ben")

    assert (
        await configured.post(f"/api/radio/{ana}/request", json={"track_uri": TRACK.uri})
    ).status_code == 200

    blocked = await configured.post(f"/api/radio/{ben}/request", json={"track_uri": remaster.uri})
    assert blocked.status_code == 409
    assert "already in the queue" in blocked.json()["detail"]
    assert await _statuses() == ["queued"]


async def test_concurrent_requests_for_one_song_insert_once(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dobleng pindot at dalawang phone na sabay pumili.

    Ang Python check ay check-then-act: pareho munang makikitang wala pang row
    bago pa makapag-insert ang alinman. Ang unique index ang tunay na panangga,
    at hindi dapat umabot sa Spotify ang pangalawa — hindi na mababawi ang
    kantang nailagay na sa queue doon.
    """
    connect_store()
    queued = stub_spotify(monkeypatch)
    ana = await _play_token(configured, operator_headers, "Ana")
    ben = await _play_token(configured, operator_headers, "Ben")

    results = await asyncio.gather(
        configured.post(f"/api/radio/{ana}/request", json={"track_uri": TRACK.uri}),
        configured.post(f"/api/radio/{ben}/request", json={"track_uri": TRACK.uri}),
    )

    codes = sorted(response.status_code for response in results)
    assert codes == [200, 409]
    assert await _statuses() == ["queued"]
    assert queued == [TRACK.uri]


async def test_a_refused_song_does_not_block_the_queue_slot(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ang kantang hindi kailanman tumugtog ay hindi puwedeng bumara sa index.

    Kung mananatili itong `queued`, ang unique index ay hindi na papayag na
    ma-request muli ang kantang iyon habambuhay.
    """
    connect_store()
    stub_spotify(
        monkeypatch,
        queue_error=spotify.SpotifyError("no device", reason="no_active_device"),
    )
    ana = await _play_token(configured, operator_headers, "Ana")
    ben = await _play_token(configured, operator_headers, "Ben")

    assert (
        await configured.post(f"/api/radio/{ana}/request", json={"track_uri": TRACK.uri})
    ).status_code == 502
    assert await _statuses() == ["failed"]

    # Bumalik na ang Spotify. Puwede nang subukan muli ang parehong kanta.
    queued = stub_spotify(monkeypatch)
    assert (
        await configured.post(f"/api/radio/{ben}/request", json={"track_uri": TRACK.uri})
    ).status_code == 200
    assert queued == [TRACK.uri]


async def test_long_track_is_refused(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    long_track = spotify.Track(
        uri=TRACK.uri,
        name="Ambient Rain For Twenty Minutes",
        artist="Nobody",
        duration_ms=20 * 60 * 1000,
        art_url="",
    )
    connect_store()
    queued = stub_spotify(monkeypatch, track=long_track)
    token = await _play_token(configured, operator_headers)

    response = await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})
    assert response.status_code == 409
    assert "shorter" in response.json()["detail"]
    assert queued == []


async def test_non_spotify_uri_is_refused(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ang URI ay galing sa phone, kaya hindi ito pinagkakatiwalaan."""
    connect_store()
    stub_spotify(monkeypatch)
    token = await _play_token(configured, operator_headers)

    for candidate in ["not-a-uri", "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M", "spotify:track:x"]:
        response = await configured.post(
            f"/api/radio/{token}/request", json={"track_uri": candidate}
        )
        assert response.status_code == 400, candidate


async def test_host_can_close_requests(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connect_store()
    queued = stub_spotify(monkeypatch)
    token = await _play_token(configured, operator_headers)

    closed = await configured.post(
        "/api/radio/accepting", json={"accepting": False}, headers=operator_headers
    )
    assert closed.status_code == 200
    assert closed.json() == {"accepting": False}

    response = await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})
    assert response.status_code == 409
    assert "closed song requests" in response.json()["detail"]
    assert queued == []


async def test_failed_queue_is_still_recorded(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kapag sarado ang Spotify ng host, ang host ay dapat may matingnan."""
    connect_store()
    stub_spotify(
        monkeypatch,
        queue_error=spotify.SpotifyError(
            "Spotify has no active device.", reason="no_active_device"
        ),
    )
    token = await _play_token(configured, operator_headers)

    response = await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})
    assert response.status_code == 502
    assert "no active device" in response.json()["detail"]

    async with get_session_factory()() as db:
        record = await db.scalar(select(SongRequest))
        assert record is not None
        assert record.status == SONG_FAILED
        assert record.failure_reason == "no_active_device"


async def test_failed_request_does_not_burn_the_cooldown(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hindi kasalanan ng bisita kung sarado ang Spotify ng host."""
    connect_store()
    stub_spotify(
        monkeypatch,
        queue_error=spotify.SpotifyError("no device", reason="no_active_device"),
    )
    token = await _play_token(configured, operator_headers)
    assert (
        await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})
    ).status_code == 502

    # Ngayon gumagana na ang Spotify. Dapat makapasok agad ang bisita.
    stub_spotify(monkeypatch)
    assert (
        await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})
    ).status_code == 200


async def test_search_needs_a_real_play_token(
    configured: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    connect_store()
    stub_spotify(monkeypatch)
    response = await configured.get("/api/radio/made-up-token/search", params={"q": "harana"})
    assert response.status_code == 404


async def test_search_returns_tracks(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connect_store()
    stub_spotify(monkeypatch)
    token = await _play_token(configured, operator_headers)

    response = await configured.get(f"/api/radio/{token}/search", params={"q": "kailan"})
    assert response.status_code == 200
    assert response.json()[0]["name"] == TRACK.name

    # Blangkong query ay hindi umaabot sa Spotify.
    empty = await configured.get(f"/api/radio/{token}/search", params={"q": "   "})
    assert empty.status_code == 200
    assert empty.json() == []


async def test_guest_page_hides_search_when_radio_is_off(
    configured: AsyncClient, operator_headers: dict[str, str]
) -> None:
    token = await _play_token(configured, operator_headers)
    page = await configured.get(f"/radio/{token}")
    assert page.status_code == 200
    assert "The radio is off right now" in page.text


# --------------------------------------------------------------------------
# Ang generic na radio QR
# --------------------------------------------------------------------------


async def test_join_page_is_closed_when_radio_is_off(configured: AsyncClient) -> None:
    """Fail closed: walang Player na nagagawa kapag patay ang radio."""
    page = await configured.get("/radio")
    assert page.status_code == 503
    assert "The radio is off right now" in page.text

    joined = await configured.post("/radio/join", data={"given_name": "Ana"})
    assert joined.status_code == 503

    async with get_session_factory()() as db:
        assert await db.scalar(select(Player.id)) is None


async def test_guest_joins_the_radio_without_a_bingo_card(
    configured: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ang buong punto ng generic QR: walang bingo, tugtog lang."""
    connect_store()
    queued = stub_spotify(monkeypatch)

    landing = await configured.get("/radio")
    assert landing.status_code == 200
    assert "Start picking songs" in landing.text

    joined = await configured.post("/radio/join", data={"given_name": "Ana"})
    assert joined.status_code == 303
    token = joined.headers["location"].removeprefix("/radio/")
    assert token

    # Ang bisitang ito ay walang card, kaya patay ang bingo board niya. Iyon ang
    # tama, at hindi ito dapat pumigil sa radio.
    assert (await configured.get(f"/play/{token}")).status_code == 404

    radio_page = await configured.get(f"/radio/{token}")
    assert radio_page.status_code == 200
    assert "Search for a song" in radio_page.text

    picked = await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})
    assert picked.status_code == 200, picked.text
    assert queued == [TRACK.uri]


async def test_join_qr_is_reusable(configured: AsyncClient) -> None:
    """Hindi single-use ang QR na ito, kaya maraming bisita ang puwedeng sumali.

    Kailangang malinis ang cookie sa pagitan, dahil isang phone ang
    kinakatawan ng isang cookie jar.
    """
    connect_store()
    tokens = set()
    for name in ["Ana", "Ben", "Cely"]:
        joined = await configured.post("/radio/join", data={"given_name": name})
        assert joined.status_code == 303
        tokens.add(joined.headers["location"].removeprefix("/radio/"))
        configured.cookies.clear()

    assert len(tokens) == 3


async def test_returning_guest_keeps_the_same_identity(configured: AsyncClient) -> None:
    """Ang cookie ang humahawak sa cooldown.

    Kung bagong `Player` kada scan, ang cooldown ay walang saysay: sapat na ang
    muling pag-scan para makakuha ng bagong turn.
    """
    connect_store()
    first = await configured.post("/radio/join", data={"given_name": "Ana"})
    token = first.headers["location"].removeprefix("/radio/")

    # Muling pag-scan ng QR sa parehong phone.
    again = await configured.get("/radio")
    assert again.status_code == 303
    assert again.headers["location"] == f"/radio/{token}"

    # At ang pag-submit muli ng form ay hindi gumagawa ng pangalawang tao.
    resubmit = await configured.post("/radio/join", data={"given_name": "Ana Impostor"})
    assert resubmit.status_code == 303
    assert resubmit.headers["location"] == f"/radio/{token}"

    async with get_session_factory()() as db:
        players = (await db.execute(select(Player))).scalars().all()
        assert len(players) == 1
        assert players[0].given_name == "Ana"


async def test_cooldown_survives_a_rescan(
    configured: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    connect_store()
    queued = stub_spotify(monkeypatch)
    joined = await configured.post("/radio/join", data={"given_name": "Ana"})
    token = joined.headers["location"].removeprefix("/radio/")

    assert (
        await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})
    ).status_code == 200

    # Muling pag-scan, tapos subok muli. Parehong tao pa rin, kaya cooldown pa rin.
    rescan = await configured.get("/radio")
    same_token = rescan.headers["location"].removeprefix("/radio/")
    assert same_token == token

    second = await configured.post(
        f"/api/radio/{same_token}/request", json={"track_uri": OTHER_TRACK.uri}
    )
    assert second.status_code == 409
    assert queued == [TRACK.uri]


async def test_join_cookie_is_httponly(configured: AsyncClient) -> None:
    """Walang JavaScript na kailangang humawak sa capability token."""
    connect_store()
    joined = await configured.post("/radio/join", data={"given_name": "Ana"})
    cookie_header = joined.headers["set-cookie"]
    assert "bb_radio=" in cookie_header
    assert "HttpOnly" in cookie_header
    # `lax` at hindi `strict`: ang QR ay binubuksan ng camera app ng phone.
    assert "SameSite=lax" in cookie_header


async def test_join_is_rate_limited(
    configured: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ang endpoint na gumagawa ng row at bukas sa kahit sino ay may hangganan."""
    monkeypatch.setenv("BINGO_RADIO_JOIN_RATE_LIMIT_PER_MINUTE", "3")
    get_settings.cache_clear()
    connect_store()

    codes = []
    for index in range(5):
        response = await configured.post("/radio/join", data={"given_name": f"Guest{index}"})
        codes.append(response.status_code)
        configured.cookies.clear()

    assert codes[:3] == [303, 303, 303]
    assert codes[3:] == [429, 429]


async def test_host_console_shows_the_join_qr(configured: AsyncClient) -> None:
    from tests.conftest import TEST_API_KEY

    await configured.post("/operator/session", data={"api_key": TEST_API_KEY})
    connect_store()

    page = await configured.get("/operator/radio")
    assert page.status_code == 200
    assert "data:image/svg+xml" in page.text

    # Ang QR ay dumadaan sa parehong base-URL resolution gaya ng pairing QR:
    # galing sa address na binuksan ng host, hindi sa `.env`. Ganito, ang
    # tunnel URL ay hindi kailangang isulat kahit saan.
    assert "http://testserver/radio" in page.text

    tunneled = await configured.get(
        "/operator/radio", headers={"Host": "disk-propose-financial.ngrok-free.dev"}
    )
    assert "http://disk-propose-financial.ngrok-free.dev/radio" in tunneled.text


# --------------------------------------------------------------------------
# Paglilinis ng queue: ang natapos na ay umaalis sa listahan
# --------------------------------------------------------------------------


async def _statuses() -> list[str]:
    """Ang status ng bawat request, sa pagkakasunod ng pag-request."""
    async with get_session_factory()() as db:
        rows = (
            await db.scalars(select(SongRequest.status).order_by(SongRequest.requested_at))
        ).all()
    return list(rows)


async def test_playing_song_leaves_the_queue(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ang bug: lumalaki lang ang listahan buong gabi.

    Pagkatapos tumugtog, wala na dapat sa "Coming up" — may sariling now-playing
    panel na iyon, at ang dalawang beses lumitaw ang parehong kanta ang mismong
    ikinalilito.
    """
    connect_store()
    stub_spotify(monkeypatch)
    token = await _play_token(configured, operator_headers)

    assert (
        await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})
    ).status_code == 200

    page = await configured.get(f"/radio/{token}")
    assert TRACK.name in page.text

    # Umabot na ng playback ang kanta.
    stub_now_playing(monkeypatch, TRACK)
    poll = await configured.get(f"/api/radio/{token}/now-playing")
    assert poll.status_code == 200
    assert poll.json()["requests"] == []

    async with get_session_factory()() as db:
        record = await db.scalar(select(SongRequest))
        assert record is not None
        # Naka-mark, hindi dinelete: kailangan pa rin ito ng duplicate check.
        assert record.status == "played"

    cleared = await configured.get(f"/radio/{token}")
    assert TRACK.name not in cleared.text
    assert "Nothing waiting" in cleared.text


async def test_songs_before_the_playing_one_are_cleared_too(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nagsasarili ang paglilinis kapag walang bukas na page nang matagal.

    Kung ang tumutugtog ay ang pangatlo, ang una at pangalawa ay tapos na —
    kahit walang nakakita nang tumugtog ang mga iyon.
    """
    third = spotify.Track(
        uri="spotify:track:2takcwOaAZWiXQijPHIx7B",
        name="Tadhana",
        artist="Up Dharma Down",
        duration_ms=250_000,
        art_url="",
    )
    connect_store()

    async def fake_fetch(*, access_token: str, track_uri: str) -> spotify.Track:
        return {TRACK.uri: TRACK, OTHER_TRACK.uri: OTHER_TRACK, third.uri: third}[track_uri]

    async def fake_queue(*, access_token: str, track_uri: str) -> None:
        return None

    monkeypatch.setattr(spotify, "fetch_track", fake_fetch)
    monkeypatch.setattr(spotify, "add_to_queue", fake_queue)
    monkeypatch.setenv("BINGO_RADIO_REQUEST_COOLDOWN_SECONDS", "0")
    get_settings.cache_clear()

    token = await _play_token(configured, operator_headers)
    for uri in [TRACK.uri, OTHER_TRACK.uri, third.uri]:
        assert (
            await configured.post(f"/api/radio/{token}/request", json={"track_uri": uri})
        ).status_code == 200

    assert len((await configured.get(f"/api/radio/{token}/now-playing")).json()["requests"]) == 3

    # Nasa pangatlo na ang playback.
    stub_now_playing(monkeypatch, third)
    assert (await configured.get(f"/api/radio/{token}/now-playing")).json()["requests"] == []

    assert await _statuses() == ["played", "played", "played"]


async def test_host_own_track_leaves_the_queue_alone(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ang kanta ng host mismo ay hindi nagbabasehan ng anumang paglilinis.

    Hindi natin alam kung nasaan sa queue ng Spotify ang mga hiniling kapag ang
    tumutugtog ay galing sa playlist niya, kaya walang ginagalaw.
    """
    connect_store()
    stub_spotify(monkeypatch)
    token = await _play_token(configured, operator_headers)
    await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})

    host_track = spotify.Track(
        uri="spotify:track:0V3wPSX9ygBnCm8psDIegu",
        name="Something From The Host Playlist",
        artist="Nobody",
        duration_ms=200_000,
        art_url="",
    )
    stub_now_playing(monkeypatch, host_track)

    poll = await configured.get(f"/api/radio/{token}/now-playing")
    assert len(poll.json()["requests"]) == 1
    assert await _statuses() == ["queued"]


async def test_nothing_playing_leaves_the_queue_alone(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connect_store()
    stub_spotify(monkeypatch)
    token = await _play_token(configured, operator_headers)
    await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})

    stub_now_playing(monkeypatch, None)
    poll = await configured.get(f"/api/radio/{token}/now-playing")
    assert len(poll.json()["requests"]) == 1


async def test_skip_removes_the_current_song_even_if_nobody_polled(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ang kaso na mababaon kung hindi hahawakan ang skip nang tahasan.

    Kapag ni-skip agad bago pa may nakatingin sa kahit anong page, ang susunod
    na tutugtog ay puwedeng kanta ng host — walang matutugma doon, at maiiwan
    sa listahan ang ni-skip nang habambuhay.
    """
    skipped: list[bool] = []

    async def fake_skip(*, access_token: str) -> None:
        skipped.append(True)

    connect_store()
    stub_spotify(monkeypatch)
    monkeypatch.setattr(spotify, "skip_next", fake_skip)
    token = await _play_token(configured, operator_headers)
    await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})

    # Tumutugtog na ito, pero walang page na nag-poll — `queued` pa rin.
    stub_now_playing(monkeypatch, TRACK)
    assert await _statuses() == ["queued"]

    response = await configured.post("/api/radio/skip", headers=operator_headers)
    assert response.status_code == 200
    assert skipped == [True]
    assert await _statuses() == ["played"]


async def test_played_song_still_blocks_a_duplicate(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ang katapos-tapos lang na kanta ay hindi puwedeng i-request muli.

    Kung `queued` lang ang titingnan ng duplicate check, mababali ang buong
    punto ng window pagkatapos ng unang pagtugtog.
    """
    connect_store()
    stub_spotify(monkeypatch)
    ana = await _play_token(configured, operator_headers, "Ana")
    ben = await _play_token(configured, operator_headers, "Ben")

    await configured.post(f"/api/radio/{ana}/request", json={"track_uri": TRACK.uri})
    stub_now_playing(monkeypatch, TRACK)
    await configured.get(f"/api/radio/{ana}/now-playing")

    again = await configured.post(f"/api/radio/{ben}/request", json={"track_uri": TRACK.uri})
    assert again.status_code == 409
    assert "just played" in again.json()["detail"]


async def test_cooldown_is_not_reset_when_the_song_starts_playing(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ang pagtugtog ng kanta ay hindi bagong turn para sa nagpadala."""
    connect_store()
    stub_spotify(monkeypatch)
    token = await _play_token(configured, operator_headers)
    await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})

    stub_now_playing(monkeypatch, TRACK)
    await configured.get(f"/api/radio/{token}/now-playing")
    assert await _statuses() == ["played"]

    second = await configured.post(
        f"/api/radio/{token}/request", json={"track_uri": OTHER_TRACK.uri}
    )
    assert second.status_code == 409
    assert "everyone gets a turn" in second.json()["detail"]


async def test_queue_is_in_play_order(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Queue ito at hindi history: ang nasa itaas ang susunod na tutugtog."""
    connect_store()

    async def fake_fetch(*, access_token: str, track_uri: str) -> spotify.Track:
        return TRACK if track_uri == TRACK.uri else OTHER_TRACK

    async def fake_queue(*, access_token: str, track_uri: str) -> None:
        return None

    monkeypatch.setattr(spotify, "fetch_track", fake_fetch)
    monkeypatch.setattr(spotify, "add_to_queue", fake_queue)
    monkeypatch.setenv("BINGO_RADIO_REQUEST_COOLDOWN_SECONDS", "0")
    get_settings.cache_clear()

    token = await _play_token(configured, operator_headers)
    await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})
    await configured.post(f"/api/radio/{token}/request", json={"track_uri": OTHER_TRACK.uri})

    names = [
        item["track_name"]
        for item in (await configured.get(f"/api/radio/{token}/now-playing")).json()["requests"]
    ]
    assert names == [TRACK.name, OTHER_TRACK.name]


async def test_repeat_polls_do_not_requery(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ang bantay laban sa pag-query kada poll kada bisita.

    Sa isang party, dose-dosenang poll kada minuto ang darating. Iisa lang naman
    ang kasagutan hanggang magpalit ng kanta.
    """
    connect_store()
    stub_spotify(monkeypatch)
    token = await _play_token(configured, operator_headers)
    await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})

    stub_now_playing(monkeypatch, TRACK)
    assert radio.claim_reconcile(TRACK.uri) is True
    assert radio.claim_reconcile(TRACK.uri) is False
    assert radio.claim_reconcile(OTHER_TRACK.uri) is True


async def test_played_rows_are_never_deleted(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Append-only pa rin ang table. Ang `played` ay display state lang."""
    connect_store()
    stub_spotify(monkeypatch)
    token = await _play_token(configured, operator_headers)
    await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})

    stub_now_playing(monkeypatch, TRACK)
    await configured.get(f"/api/radio/{token}/now-playing")

    async with get_session_factory()() as db:
        rows = (await db.execute(select(SongRequest))).scalars().all()
        assert len(rows) == 1
        assert rows[0].track_name == TRACK.name
        assert rows[0].status == "played"


async def test_disconnect_clears_the_connection(
    configured: AsyncClient, operator_headers: dict[str, str]
) -> None:
    connect_store()
    assert radio.get_connection_store().connected

    response = await configured.post("/api/radio/disconnect", headers=operator_headers)
    assert response.status_code == 200
    assert not radio.get_connection_store().connected


async def test_connect_requires_a_refresh_token() -> None:
    """Walang refresh token, walang connection.

    Kung papayagan, mamamatay ang radio pagkatapos ng isang oras at mukhang
    random na bug iyon sa gitna ng party.
    """
    with pytest.raises(radio.RadioError):
        radio.get_connection_store().connect(
            spotify.TokenGrant(
                access_token="test-access-token",
                expires_in=3600,
                scope="",
                refresh_token=None,
            ),
            account_name="Party Host",
        )


async def test_player_row_is_untouched_by_radio(
    configured: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Walang bagong PII na naiipon dahil sa radio."""
    connect_store()
    stub_spotify(monkeypatch)
    token = await _play_token(configured, operator_headers, "Ana")
    await configured.post(f"/api/radio/{token}/request", json={"track_uri": TRACK.uri})

    async with get_session_factory()() as db:
        player = await db.scalar(select(Player).where(Player.play_token == token))
        assert player is not None
        assert player.given_name == "Ana"
        # Palayaw lang pa rin ang alam natin tungkol sa bisita.
        assert set(Player.__table__.columns.keys()) == {
            "id",
            "given_name",
            "play_token",
            "created_at",
        }
