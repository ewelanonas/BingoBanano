"""Ang upload ng litrato ng party.

Ang folder na tinutumbok ng service ay nasa loob ng repo, kaya ini-redirect ito
sa `tmp_path` sa lahat ng test dito. Kung hindi, ang test suite ay magsusulat ng
tunay na file sa `app/web/static/img/`.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from httpx import AsyncClient
from PIL import Image

from app.services import photos
from tests.conftest import TEST_API_KEY


@pytest.fixture(autouse=True)
def scratch_img_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "img"
    target.mkdir()
    monkeypatch.setattr(photos, "IMG_DIR", target)
    return target


def image_bytes(width: int = 1200, height: int = 900, fmt: str = "PNG") -> bytes:
    image = Image.new("RGB", (width, height), (210, 70, 40))
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


async def sign_in(client: AsyncClient) -> str:
    """Mag-login at ibalik ang CSRF token mula sa page markup."""
    await client.post("/operator/session", data={"api_key": TEST_API_KEY})
    page = await client.get("/operator/photos")
    return str(page.text.split('const CSRF_TOKEN = "', 1)[1].split('"', 1)[0])


async def upload(
    client: AsyncClient,
    headers: dict[str, str],
    slot: str,
    raw: bytes,
    filename: str = "eli.png",
) -> object:
    return await client.post(
        f"/api/photos/{slot}",
        headers=headers,
        files={"photo": (filename, raw, "image/png")},
    )


# --- Access -----------------------------------------------------------------


async def test_the_photo_page_needs_the_host_signed_in(client: AsyncClient) -> None:
    response = await client.get("/operator/photos")
    assert response.status_code == 303
    assert response.headers["location"] == "/operator"


async def test_uploading_without_authorization_is_refused(
    client: AsyncClient, scratch_img_dir: Path
) -> None:
    response = await upload(client, {}, "photo1", image_bytes())
    assert response.status_code == 401
    assert not (scratch_img_dir / "photo1.jpg").exists()


async def test_a_cookie_session_without_csrf_cannot_upload(client: AsyncClient) -> None:
    await client.post("/operator/session", data={"api_key": TEST_API_KEY})

    response = await upload(client, {}, "photo1", image_bytes())
    assert response.status_code == 403


async def test_a_cookie_session_with_csrf_can_upload(client: AsyncClient) -> None:
    token = await sign_in(client)

    response = await upload(client, {"X-CSRF-Token": token}, "photo1", image_bytes())
    assert response.status_code == 200


# --- Ang page ---------------------------------------------------------------


async def test_the_page_offers_both_slots_when_nothing_is_uploaded(client: AsyncClient) -> None:
    await sign_in(client)

    page = await client.get("/operator/photos")
    assert page.status_code == 200
    assert 'data-slot="photo1"' in page.text
    assert 'data-slot="photo2"' in page.text
    assert page.text.count('<p class="photo-empty">') == 2


async def test_the_page_shows_a_saved_photo_with_a_cache_buster(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    await sign_in(client)
    await upload(client, operator_headers, "photo2", image_bytes())

    page = await client.get("/operator/photos")
    # Kung walang version sa URL, ang lumang litrato ang ipapakita ng browser
    # pagkatapos mag-upload ng panibago.
    assert "/static/img/photo2.jpg?v=" in page.text
    assert page.text.count('<p class="photo-empty">') == 1


async def test_the_lobby_links_to_the_photo_page(client: AsyncClient) -> None:
    await client.post("/operator/session", data={"api_key": TEST_API_KEY})

    lobby = await client.get("/kiosk")
    assert 'href="/operator/photos"' in lobby.text


# --- Ang aktuwal na pag-store ----------------------------------------------


async def test_an_upload_is_re_encoded_as_a_square_jpeg(
    client: AsyncClient, operator_headers: dict[str, str], scratch_img_dir: Path
) -> None:
    response = await upload(client, operator_headers, "photo1", image_bytes(1200, 900))
    assert response.status_code == 200

    saved = scratch_img_dir / "photo1.jpg"
    assert saved.exists()
    with Image.open(saved) as stored:
        # Hindi PNG kahit PNG ang ipinadala: litrato ito, at ang PNG ay walong
        # beses na mas mabigat sa parehong hitsura.
        assert stored.format == "JPEG"
        assert stored.size == (photos.OUTPUT_EDGE, photos.OUTPUT_EDGE)

    body = response.json()
    assert body["url"] == "/static/img/photo1.jpg"
    assert body["byte_size"] == saved.stat().st_size


async def test_the_saved_file_stays_small_enough_for_mobile_data(
    client: AsyncClient, operator_headers: dict[str, str], scratch_img_dir: Path
) -> None:
    await upload(client, operator_headers, "photo1", image_bytes(3000, 2000))

    # Bawat bisita ay naglo-load nito sa mobile data.
    assert (scratch_img_dir / "photo1.jpg").stat().st_size < 200 * 1024


async def test_camera_metadata_is_stripped(
    client: AsyncClient, operator_headers: dict[str, str], scratch_img_dir: Path
) -> None:
    source = Image.new("RGB", (400, 300), (30, 120, 90))
    exif = source.getexif()
    exif[274] = 3  # Orientation: 180 degrees
    exif[0x9286] = "shot at home"  # UserComment
    buffer = io.BytesIO()
    source.save(buffer, format="JPEG", exif=exif)

    await upload(client, operator_headers, "photo1", buffer.getvalue(), filename="eli.jpg")

    with Image.open(scratch_img_dir / "photo1.jpg") as stored:
        # Litrato ito ng bata sa loob ng bahay. Ang GPS at ang iba pang camera
        # info ay wala nang lugar sa isang page na maaaring maabot ng internet.
        assert dict(stored.getexif()) == {}


async def test_the_two_slots_are_independent(
    client: AsyncClient, operator_headers: dict[str, str], scratch_img_dir: Path
) -> None:
    await upload(client, operator_headers, "photo1", image_bytes())
    assert (scratch_img_dir / "photo1.jpg").exists()
    assert not (scratch_img_dir / "photo2.jpg").exists()

    await upload(client, operator_headers, "photo2", image_bytes())
    assert (scratch_img_dir / "photo2.jpg").exists()


async def test_uploading_again_replaces_the_photo(
    client: AsyncClient, operator_headers: dict[str, str], scratch_img_dir: Path
) -> None:
    await upload(client, operator_headers, "photo1", image_bytes(400, 400))
    first = (scratch_img_dir / "photo1.jpg").read_bytes()

    noisy = Image.new("RGB", (400, 400), (5, 5, 200))
    buffer = io.BytesIO()
    noisy.save(buffer, format="PNG")
    await upload(client, operator_headers, "photo1", buffer.getvalue())

    assert (scratch_img_dir / "photo1.jpg").read_bytes() != first
    # Walang natirang kalahating file sa tabi.
    assert not (scratch_img_dir / "photo1.jpg.part").exists()


# --- Pagtanggi --------------------------------------------------------------


async def test_a_file_that_is_not_a_photo_is_refused(
    client: AsyncClient, operator_headers: dict[str, str], scratch_img_dir: Path
) -> None:
    # Sinasabi ng request na `image/png` ito. Hindi pinagkakatiwalaan iyon —
    # ang decode ang nagpapasya.
    response = await upload(client, operator_headers, "photo1", b"#!/bin/sh\nrm -rf /\n")

    assert response.status_code == 400
    assert not (scratch_img_dir / "photo1.jpg").exists()


async def test_an_empty_pick_is_refused(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    response = await upload(client, operator_headers, "photo1", b"")
    assert response.status_code == 400


async def test_a_truncated_photo_is_refused(
    client: AsyncClient, operator_headers: dict[str, str], scratch_img_dir: Path
) -> None:
    half = image_bytes(1200, 900)[:600]

    response = await upload(client, operator_headers, "photo1", half)
    assert response.status_code == 400
    assert not (scratch_img_dir / "photo1.jpg").exists()


async def test_an_oversized_upload_is_refused(
    client: AsyncClient, operator_headers: dict[str, str], scratch_img_dir: Path
) -> None:
    response = await upload(
        client,
        operator_headers,
        "photo1",
        b"\x00" * (photos.MAX_UPLOAD_BYTES + 1),
    )

    assert response.status_code == 413
    assert not (scratch_img_dir / "photo1.jpg").exists()


async def test_a_pixel_bomb_is_refused_before_it_is_decoded(
    client: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Maliit ang naka-compress na file, malaki ang naka-decode na buffer. Ang
    # sukat ay mula sa header, kaya hindi kailangang i-decode para tumanggi.
    monkeypatch.setattr(photos, "MAX_SOURCE_PIXELS", 1000)

    response = await upload(client, operator_headers, "photo1", image_bytes(200, 200))
    assert response.status_code == 413


async def test_an_unknown_slot_is_refused(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    # Ang slot ay pumapasok sa filename, kaya whitelist at hindi sanitizing.
    # Ang hindi kilalang slot ay 422; ang may slash ay hindi na tumatama sa route.
    for slot in ("photo3", "..%2F..%2Fapp%2Fmain", "README", "photo1.jpg"):
        response = await client.post(
            f"/api/photos/{slot}",
            headers=operator_headers,
            files={"photo": ("x.png", image_bytes(), "image/png")},
        )
        assert response.status_code in (404, 422), slot


async def test_uploads_are_rate_limited(
    client: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.security import get_limiter
    from app.config import get_settings

    get_limiter().reset()
    monkeypatch.setenv("BINGO_PHOTO_UPLOAD_RATE_LIMIT_PER_MINUTE", "2")
    get_settings.cache_clear()

    for _ in range(2):
        allowed = await upload(client, operator_headers, "photo1", image_bytes(80, 80))
        assert allowed.status_code == 200

    blocked = await upload(client, operator_headers, "photo1", image_bytes(80, 80))
    assert blocked.status_code == 429

    get_settings.cache_clear()


# --- Pagtanggal -------------------------------------------------------------


async def test_removing_a_photo_leaves_the_fallback_behind(
    client: AsyncClient, operator_headers: dict[str, str], scratch_img_dir: Path
) -> None:
    await upload(client, operator_headers, "photo1", image_bytes())

    removed = await client.delete("/api/photos/photo1", headers=operator_headers)
    assert removed.status_code == 200
    assert removed.json() == {"slot": "photo1", "removed": True}
    assert not (scratch_img_dir / "photo1.jpg").exists()

    again = await client.delete("/api/photos/photo1", headers=operator_headers)
    assert again.json()["removed"] is False


async def test_removing_needs_authorization(
    client: AsyncClient, operator_headers: dict[str, str], scratch_img_dir: Path
) -> None:
    await upload(client, operator_headers, "photo1", image_bytes())

    response = await client.delete("/api/photos/photo1")
    assert response.status_code == 401
    assert (scratch_img_dir / "photo1.jpg").exists()


# --- Ang front-end at ang server ay dapat magkasundo sa filename ------------


def test_the_popup_and_the_cards_ask_for_the_files_the_server_writes() -> None:
    static = Path(__file__).resolve().parent.parent / "app" / "web" / "static"
    script = (static / "celebrate.js").read_text(encoding="utf-8")
    theme = (static / "theme.css").read_text(encoding="utf-8")

    for slot in photos.SLOTS:
        assert f"/static/img/{slot}.jpg" in script, slot

    # Ang natawag na numero ay laging `photo1`: dalawampu't apat na cell na
    # nagpapalit-palit ng mukha ay hindi mababasa.
    assert '/static/img/photo1.jpg"' in theme
    assert "photo2" not in theme
