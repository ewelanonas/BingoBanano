"""Kailan ang dalawang Spotify track ay iisang kanta.

Pure logic, walang I/O. Nasa `app/domain/` para madali i-unit test at para
saklaw ito ng `mypy --strict`.

## Bakit hindi sapat ang track URI

Ang catalog ng Spotify ay may hiwalay na track ID kada paglabas ng kanta. Ang
isang awit ay puwedeng may URI sa single, iba sa album, iba sa deluxe edition,
iba sa remaster, at iba pa sa greatest-hits compilation. Sa search results ay
magkatabi ang mga ito at mukhang pareho.

Kaya ang paghahambing ng URI lang ay nagpapapasok ng paulit-ulit na kanta: pili
lang ang bisita ng ibang linya sa parehong listahan at nakalusot na. Ang
pinaghahambing dito ay pamagat at artist, pagkatapos alisin ang mga sabit na
nagkakaiba lang sa paglabas.

Sinasadya ang pagiging mahigpit: mas mabuti nang matanggihan ang live version ng
kantang katapos-tapos lang kaysa tumugtog ang parehong awit nang dalawang beses
sa isang party.
"""

from __future__ import annotations

import re
import unicodedata

# "Kailan - Remastered 2011", "Harana - Live at Araneta". Ang lahat pagkatapos ng
# unang " - " ay tungkol sa paglabas at hindi sa kanta.
_DASH_SUFFIX = re.compile(r"\s+-\s+.*$")

# Sabit sa dulo: "(Remastered)", "[Live]", "(feat. Someone)". Sa dulo lang
# tinatanggal — may mga pamagat na nagsisimula sa panaklong, gaya ng
# "(Sittin' On) The Dock of the Bay", at hindi puwedeng masira iyon.
_TRAILING_GROUP = re.compile(r"\s*[(\[][^()\[\]]*[)\]]\s*$")

# "Smokey Mountain feat. Someone", "Artist ft. Other". Ang featured artist ay
# lumilitaw sa ilang paglabas lang.
_FEATURING = re.compile(r"\s+(feat\.?|ft\.?|featuring|with)\s+.*$", re.IGNORECASE)

# Tinatanggal nang walang kapalit, at hindi pinapalitan ng espasyo: ang
# "Don't Stop" at "Dont Stop" ay iisang kanta, kaya hindi puwedeng maging
# "don t stop" ang isa.
_DROPPED = re.compile(r"['\u2018\u2019\u02bc\"\u201c\u201d.]+")

# Ang natitirang hindi letra at hindi numero ay nagiging espasyo. Dito nasasama
# ang en dash, ampersand, at doble-espasyo.
_NOISE = re.compile(r"[^0-9a-z]+")


def _fold(text: str) -> str:
    """Ibaba, alisin ang accent, at pagsamahin ang mga pagkakaiba sa bantas."""
    stripped = unicodedata.normalize("NFKD", text)
    without_accents = "".join(ch for ch in stripped if not unicodedata.combining(ch))
    tightened = _DROPPED.sub("", without_accents.casefold())
    return _NOISE.sub(" ", tightened).strip()


def normalize_title(name: str) -> str:
    title = _DASH_SUFFIX.sub("", name.strip())
    # Paulit-ulit: puwedeng may dalawang sabit, "(Live) (Remastered)".
    while True:
        shorter = _TRAILING_GROUP.sub("", title)
        if shorter == title:
            break
        title = shorter

    folded = _fold(title)
    # Kung nawala ang lahat — pamagat na panaklong lang ang buo, halimbawa —
    # ang buong pangalan ang gamitin. Ang blangkong susi ay tutugma sa lahat,
    # at iyon ay tahimik na magbabara sa buong party.
    return folded or _fold(name) or name.strip().casefold()


def normalize_artist(artist: str) -> str:
    """Ang unang artist lang.

    Ang search results ay pinagsasama ang mga artist gamit ang ", ", kaya ang
    isang kanta ay puwedeng "Smokey Mountain" sa isang paglabas at
    "Smokey Mountain, Jolina Magdangal" sa isa pa. Ang unang pangalan ang
    matatag.
    """
    first = _FEATURING.sub("", artist.split(",", 1)[0])
    return _fold(first) or _fold(artist) or artist.strip().casefold()


def track_key(name: str, artist: str) -> str:
    """Susi para sa "parehong kanta", kahit magkaibang track ID.

    >>> track_key("Kailan - Remastered 2011", "Smokey Mountain")
    'kailan|smokey mountain'
    >>> track_key("Kailan (Live)", "Smokey Mountain, Jolina Magdangal")
    'kailan|smokey mountain'
    """
    return f"{normalize_title(name)}|{normalize_artist(artist)}"
