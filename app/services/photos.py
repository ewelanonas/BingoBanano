"""Ang dalawang litrato ng party: pag-validate, pag-normalize, at pag-store.

Bakit may upload page at hindi na lang basta kinokopya ang file sa folder: ang
litrato ay nasa phone ng host, hindi sa dev machine. Mas madaling i-upload mula
sa phone kaysa i-transfer pa.

Ang ini-upload ay hindi kailanman isinusulat nang buo. Dinedecode ito ni Pillow,
ini-crop, at ini-re-encode bilang JPEG. Tatlong bagay ang nakukuha natin dito:

1. Kung hindi ito litrato, hindi ito madedecode, kaya hindi ito makakalusot.
   Ang `Content-Type` na sinasabi ng browser ay hindi pinagkakatiwalaan — kahit
   sino puwedeng magsabi ng `image/png` sa kahit anong bytes.
2. Nawawala ang EXIF, kasama ang GPS coordinates ng bahay. Litrato ito ng bata
   sa loob ng bahay; hindi kailangang isama ang lokasyon sa isang page na
   maaaring maabot ng internet kapag naka-tunnel.
3. Lumiliit ang file. Ang bisita ay nasa mobile data at mid-range Android.

Walang I/O sa `app/domain/` kaya dito ito nakalagay at hindi doon.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from PIL import Image, ImageOps, UnidentifiedImageError

PhotoSlot = Literal["photo1", "photo2"]

# Whitelist, hindi sanitizing. Ang slot ay pumapasok sa filename, kaya ang tanging
# safe na paraan ay tuwirang paghahambing sa listahan ng pinapayagan.
SLOTS: Final[tuple[PhotoSlot, ...]] = ("photo1", "photo2")

IMG_DIR: Path = Path(__file__).resolve().parent.parent / "web" / "static" / "img"

# Ang litrato mula sa modernong phone ay mga 3-5 MB. Maluwag ang 12 MB, pero may
# hangganan pa rin: ang buong body ay nasa memory bago pa madecode.
MAX_UPLOAD_BYTES: Final = 12 * 1024 * 1024
# Decompression bomb guard. Maliit ang naka-compress na file, pero ang naka-
# decode na pixel buffer ay kayang punuin ang RAM. Ini-check ito mula sa header,
# bago ang aktuwal na decode.
MAX_SOURCE_PIXELS: Final = 50_000_000

# Bilog at maliit lang ang lalabas nito sa screen, kaya 640px ang sapat. JPEG at
# hindi PNG: litrato ito, at ang PNG ng litrato ay 8-10x na mas mabigat.
OUTPUT_EDGE: Final = 640
JPEG_QUALITY: Final = 82

# Bahagyang mataas sa gitna. Sa litrato ng tao ay nasa itaas na kalahati ang mukha,
# kaya ang tuwirang center crop ay madalas na nauuwi sa dibdib.
CROP_CENTERING: Final = (0.5, 0.4)


class PhotoRejected(Exception):
    """Base para sa upload na hindi natanggap. Ang mensahe ay para sa host."""


class NotAnImage(PhotoRejected):
    """Hindi madecode ang bytes bilang litrato."""


class PhotoTooLarge(PhotoRejected):
    """Lumampas sa byte o pixel na limitasyon."""


@dataclass(frozen=True, slots=True)
class PhotoInfo:
    slot: str
    url: str
    byte_size: int
    # Mtime bilang cache buster. Kapag walang ganito, ang browser ay maaaring
    # ipakita pa rin ang lumang litrato pagkatapos mag-upload ng panibago.
    version: int


def photo_path(slot: PhotoSlot) -> Path:
    if slot not in SLOTS:  # pragma: no cover - hinuhuli na ito ng route validation
        raise ValueError(f"unknown photo slot: {slot!r}")
    return IMG_DIR / f"{slot}.jpg"


def photo_info(slot: PhotoSlot) -> PhotoInfo | None:
    """`None` kung wala pang naka-upload sa slot na ito."""
    path = photo_path(slot)
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return PhotoInfo(
        slot=slot,
        url=f"/static/img/{slot}.jpg",
        byte_size=stat.st_size,
        version=int(stat.st_mtime),
    )


def saved_photos() -> list[tuple[PhotoSlot, PhotoInfo | None]]:
    return [(slot, photo_info(slot)) for slot in SLOTS]


def store_photo(slot: PhotoSlot, raw: bytes) -> PhotoInfo:
    """I-validate, i-normalize, at isulat ang litrato. Fail closed sa pagdududa."""
    path = photo_path(slot)

    if not raw:
        raise NotAnImage("No file was picked.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise PhotoTooLarge(
            f"That file is {len(raw) // (1024 * 1024)} MB. "
            f"Keep it under {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )

    try:
        # Lazy ito: header lang ang nababasa, kaya may sukat na tayo bago pa
        # maglaan ng memory para sa mga pixel.
        source = Image.open(io.BytesIO(raw))
        if source.width * source.height > MAX_SOURCE_PIXELS:
            raise PhotoTooLarge("That photo has too many pixels. Try a smaller copy.")
        # Ang EXIF orientation ay ina-apply bago ito matanggal, kung hindi ay
        # mababaligtad ang litratong galing sa phone.
        upright = ImageOps.exif_transpose(source) or source
        square = ImageOps.fit(
            upright.convert("RGB"),
            (OUTPUT_EDGE, OUTPUT_EDGE),
            method=Image.Resampling.LANCZOS,
            centering=CROP_CENTERING,
        )
        buffer = io.BytesIO()
        square.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    except PhotoRejected:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise NotAnImage("That file is not a photo the app can read.") from exc
    except (OSError, ValueError) as exc:
        # Truncated o sirang file. Isang mensahe lang ang sinasabi sa host —
        # walang internal na detalye ng Pillow.
        raise NotAnImage("That photo could not be read. Try another one.") from exc

    IMG_DIR.mkdir(parents=True, exist_ok=True)
    # Isulat sa tabi, pagkatapos ay palitan nang isang hakbang. Kung hindi,
    # may sandaling kalahating file ang naihahain sa isang bisitang nanalo.
    scratch = path.with_suffix(".jpg.part")
    scratch.write_bytes(buffer.getvalue())
    os.replace(scratch, path)

    info = photo_info(slot)
    assert info is not None  # kasusulat lang
    return info


def delete_photo(slot: PhotoSlot) -> bool:
    """`False` kung wala nang aalisin."""
    try:
        photo_path(slot).unlink()
    except FileNotFoundError:
        return False
    return True
