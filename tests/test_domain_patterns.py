"""Pattern bitmask at win detection."""

from __future__ import annotations

import pytest

from app.domain.cards import FREE_INDEX, generate_card
from app.domain.patterns import (
    FREE_MASK,
    FULL_MASK,
    PATTERNS,
    is_win,
    marked_mask,
    missing_count,
)
from app.domain.rng import seeded_randomizer


def test_free_center_is_always_marked() -> None:
    card = generate_card(seeded_randomizer(1))
    assert marked_mask(card.numbers, []) == FREE_MASK


def test_middle_row_win_needs_only_four_numbers() -> None:
    card = generate_card(seeded_randomizer(2))
    middle_row = [v for i, v in enumerate(card.numbers) if 10 <= i <= 14 and i != FREE_INDEX]
    assert len(middle_row) == 4

    marked = marked_mask(card.numbers, middle_row)
    assert is_win(marked, "row_3")
    assert is_win(marked, "any_line")
    assert not is_win(marked, "blackout")


def test_column_win_ignores_other_columns() -> None:
    card = generate_card(seeded_randomizer(3))
    marked = marked_mask(card.numbers, card.column(0))
    assert is_win(marked, "column_b")
    assert not is_win(marked, "column_i")


def test_blackout_needs_every_number() -> None:
    card = generate_card(seeded_randomizer(4))
    drawn = [v for v in card.numbers if v]
    assert marked_mask(card.numbers, drawn) == FULL_MASK
    assert is_win(marked_mask(card.numbers, drawn), "blackout")
    assert not is_win(marked_mask(card.numbers, drawn[:-1]), "blackout")


def test_four_corners() -> None:
    card = generate_card(seeded_randomizer(5))
    corners = [card.numbers[i] for i in (0, 4, 20, 24)]
    marked = marked_mask(card.numbers, corners)
    assert is_win(marked, "four_corners")


def test_missing_count_tracks_isa_na_lang() -> None:
    card = generate_card(seeded_randomizer(6))
    middle_row = [v for i, v in enumerate(card.numbers) if 10 <= i <= 14 and i != FREE_INDEX]
    assert missing_count(marked_mask(card.numbers, middle_row[:3]), "row_3") == 1
    assert missing_count(marked_mask(card.numbers, middle_row), "row_3") == 0
    assert missing_count(marked_mask(card.numbers, middle_row[:3]), "any_line") == 1


def test_unknown_pattern_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown pattern"):
        is_win(FREE_MASK, "letter_z")


def test_all_declared_patterns_fit_the_grid() -> None:
    for name, mask in PATTERNS.items():
        assert mask & ~FULL_MASK == 0, f"{name} ay lumalabas sa 25 cells"
