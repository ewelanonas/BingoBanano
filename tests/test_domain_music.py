"""Kailan ang dalawang Spotify track ay iisang kanta.

Pure functions ito, kaya walang client at walang database dito.
"""

from __future__ import annotations

import pytest

from app.domain.music import normalize_artist, normalize_title, track_key

SAME_SONG = [
    # Ang paulit-ulit na kanta sa totoong party ay galing dito: magkatabi sa
    # search results ang album version at ang remaster, at ibang track ID ang
    # bawat isa.
    ("Kailan", "Smokey Mountain"),
    ("Kailan - Remastered 2011", "Smokey Mountain"),
    ("Kailan (Live)", "Smokey Mountain"),
    ("Kailan (Remastered) (Bonus Track)", "Smokey Mountain"),
    ("KAILAN", "smokey mountain"),
    # Ang artist list ay pinagsasama ng ", " at nagbabago kada paglabas.
    ("Kailan", "Smokey Mountain, Jolina Magdangal"),
    ("Kailan - Live at the Araneta", "Smokey Mountain feat. Someone"),
]


@pytest.mark.parametrize(("name", "artist"), SAME_SONG)
def test_all_pressings_share_one_key(name: str, artist: str) -> None:
    assert track_key(name, artist) == track_key("Kailan", "Smokey Mountain")


def test_different_songs_do_not_collide() -> None:
    keys = {
        track_key("Kailan", "Smokey Mountain"),
        track_key("Harana", "Parokya ni Edgar"),
        # Parehong pamagat, ibang artist: cover ito at hindi parehong recording.
        track_key("Kailan", "Some Cover Band"),
    }
    assert len(keys) == 3


def test_accents_and_punctuation_fold_together() -> None:
    assert track_key("Cafe", "Artist") == track_key("Café", "Artist")
    assert track_key("Don't Stop", "Artist") == track_key("Dont  Stop", "Artist")


def test_leading_parenthesis_is_kept() -> None:
    """Sa dulo lang tinatanggal ang panaklong.

    May mga pamagat na nagsisimula doon, at hindi puwedeng masira ang mga iyon.
    """
    assert normalize_title("(Sittin' On) The Dock of the Bay") == ("sittin on the dock of the bay")


def test_a_fully_parenthesised_title_never_becomes_empty() -> None:
    """Ang blangkong susi ay tutugma sa lahat at tahimik na magbabara sa party."""
    assert normalize_title("(Live)") != ""
    assert normalize_title("(((")
    assert normalize_artist("???")


def test_key_has_both_halves() -> None:
    assert track_key("Harana", "Parokya ni Edgar") == "harana|parokya ni edgar"
