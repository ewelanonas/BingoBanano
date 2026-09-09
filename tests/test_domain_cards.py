"""Card generation invariants. Ito ang unang titingnan sa fairness audit."""

from __future__ import annotations

import pytest

from app.domain.cards import (
    CELL_COUNT,
    COLUMN_LETTERS,
    COLUMN_RANGES,
    FREE_INDEX,
    CardError,
    column_for_number,
    generate_card,
    generate_numbers,
    validate,
)
from app.domain.rng import seeded_randomizer, system_randomizer

ITERATIONS = 400


def test_layout_has_25_cells_and_free_center() -> None:
    rng = system_randomizer()
    for _ in range(ITERATIONS):
        numbers = generate_numbers(rng)
        assert len(numbers) == CELL_COUNT
        assert numbers[FREE_INDEX] == 0


def test_every_number_is_inside_its_column_range() -> None:
    rng = system_randomizer()
    for _ in range(ITERATIONS):
        numbers = generate_numbers(rng)
        for index, value in enumerate(numbers):
            if index == FREE_INDEX:
                continue
            low, high = COLUMN_RANGES[index % 5]
            assert low <= value <= high, f"column {COLUMN_LETTERS[index % 5]} labas sa range"


def test_no_duplicate_numbers_on_a_card() -> None:
    rng = system_randomizer()
    for _ in range(ITERATIONS):
        numbers = generate_numbers(rng)
        filled = [v for i, v in enumerate(numbers) if i != FREE_INDEX]
        assert len(set(filled)) == len(filled)
        assert 0 not in filled


def test_serials_are_unique_across_many_issuances() -> None:
    rng = system_randomizer()
    serials = {generate_card(rng).serial for _ in range(2000)}
    assert len(serials) == 2000


def test_serial_format_is_readable() -> None:
    card = generate_card(system_randomizer())
    assert card.serial.startswith("BB-")
    assert len(card.serial) == len("BB-XXXXX-XXXXX")
    assert not set(card.serial) & set("ILOU")


def test_rows_and_columns_agree_with_flat_layout() -> None:
    card = generate_card(seeded_randomizer(7))
    rows = card.rows()
    assert len(rows) == 5
    assert [row[0] for row in rows] == card.column(0)
    for value in card.column(2):
        if value:
            assert column_for_number(value) == 2


def test_validate_rejects_out_of_range_number() -> None:
    numbers = list(generate_numbers(system_randomizer()))
    # Isang value na wala sa card para range error ang mag-trigger, hindi duplicate.
    intruder = next(n for n in range(16, 31) if n not in numbers)
    numbers[0] = intruder  # column B, dapat 1-15 lang
    with pytest.raises(CardError, match="column B"):
        validate(tuple(numbers))


def test_validate_rejects_duplicate() -> None:
    numbers = list(generate_numbers(system_randomizer()))
    numbers[5] = numbers[0]  # pareho ng column B
    with pytest.raises(CardError, match="duplicate"):
        validate(tuple(numbers))


def test_validate_rejects_filled_center() -> None:
    numbers = list(generate_numbers(system_randomizer()))
    numbers[FREE_INDEX] = 33
    with pytest.raises(CardError, match="FREE"):
        validate(tuple(numbers))


def test_column_for_number_rejects_out_of_range() -> None:
    with pytest.raises(CardError):
        column_for_number(76)
