"""Draw logic: walang replacement, uniform, at tama ang letra."""

from __future__ import annotations

import pytest

from app.domain.draws import (
    ALL_BALLS,
    TOTAL_BALLS,
    DrawError,
    PoolExhaustedError,
    balls_by_letter,
    format_call,
    letter_for,
    pick_next,
    remaining_balls,
)
from app.domain.rng import seeded_randomizer, system_randomizer


def test_a_full_game_draws_every_ball_exactly_once() -> None:
    rng = system_randomizer()
    drawn: list[int] = []
    for _ in range(TOTAL_BALLS):
        drawn.append(pick_next(drawn, rng))

    assert len(drawn) == TOTAL_BALLS
    assert set(drawn) == ALL_BALLS
    assert len(set(drawn)) == len(drawn)


def test_pool_exhaustion_is_explicit() -> None:
    rng = system_randomizer()
    drawn = sorted(ALL_BALLS)
    with pytest.raises(PoolExhaustedError):
        pick_next(drawn, rng)


def test_remaining_shrinks_by_one_each_draw() -> None:
    rng = seeded_randomizer(11)
    drawn: list[int] = []
    for expected in range(TOTAL_BALLS, 0, -1):
        assert len(remaining_balls(drawn)) == expected
        drawn.append(pick_next(drawn, rng))
    assert remaining_balls(drawn) == []


def test_duplicate_in_history_is_rejected() -> None:
    with pytest.raises(DrawError, match="duplicate"):
        pick_next([7, 7], system_randomizer())


def test_out_of_range_history_is_rejected() -> None:
    with pytest.raises(DrawError, match="outside 1-75"):
        remaining_balls([0, 5])


def test_letters_follow_the_column_ranges() -> None:
    assert letter_for(1) == "B"
    assert letter_for(15) == "B"
    assert letter_for(16) == "I"
    assert letter_for(31) == "N"
    assert letter_for(46) == "G"
    assert letter_for(75) == "O"


def test_format_call_matches_what_the_caller_says() -> None:
    assert format_call(7) == "B-7"
    assert format_call(60) == "G-60"


def test_invalid_ball_is_rejected() -> None:
    with pytest.raises(DrawError):
        letter_for(76)
    with pytest.raises(DrawError):
        letter_for(0)


def test_board_grouping_sorts_within_each_column() -> None:
    grouped = balls_by_letter([75, 3, 61, 1, 30])
    assert grouped["B"] == [1, 3]
    assert grouped["I"] == [30]
    assert grouped["N"] == []
    assert grouped["O"] == [61, 75]


def test_draw_distribution_covers_the_whole_pool() -> None:
    """Sanity check: sa maraming isahang draw, lahat ng bola ay lumalabas."""
    rng = system_randomizer()
    seen = {pick_next([], rng) for _ in range(4000)}
    assert seen == ALL_BALLS
