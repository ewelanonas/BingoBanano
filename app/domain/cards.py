"""75-ball bingo card generation. Pure logic, walang I/O.

Column ranges ayon sa PH standard: B 1-15, I 16-30, N 31-45, G 46-60, O 61-75.
Ang center cell (row 2, col 2) ay FREE at ini-store bilang 0.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.rng import Randomizer

GRID_SIZE = 5
CELL_COUNT = GRID_SIZE * GRID_SIZE
FREE_INDEX = 12
FREE_VALUE = 0
COLUMN_LETTERS = ("B", "I", "N", "G", "O")

# (inclusive_low, inclusive_high) kada column, index 0 = B.
COLUMN_RANGES: tuple[tuple[int, int], ...] = (
    (1, 15),
    (16, 30),
    (31, 45),
    (46, 60),
    (61, 75),
)

_SERIAL_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # walang I, L, O, U
_SERIAL_BODY_LENGTH = 10


class CardError(ValueError):
    """Invalid na card layout."""


@dataclass(frozen=True, slots=True)
class Card:
    """Isang naka-generate na bingo card.

    `numbers` ay row-major: index = row * 5 + column. Kaya ang column `c` ay
    nasa indices c, c+5, c+10, c+15, c+20.
    """

    serial: str
    numbers: tuple[int, ...]

    def rows(self) -> list[list[int]]:
        return [
            list(self.numbers[row * GRID_SIZE : (row + 1) * GRID_SIZE]) for row in range(GRID_SIZE)
        ]

    def column(self, index: int) -> list[int]:
        return [self.numbers[index + row * GRID_SIZE] for row in range(GRID_SIZE)]


def column_of(cell_index: int) -> int:
    """Ibalik ang column index (0 = B) ng isang cell index."""
    return cell_index % GRID_SIZE


def column_for_number(number: int) -> int:
    """Ibalik ang column index kung saan nabibilang ang isang ball number."""
    for index, (low, high) in enumerate(COLUMN_RANGES):
        if low <= number <= high:
            return index
    raise CardError(f"number {number} is outside 1-75")


def generate_numbers(rng: Randomizer) -> tuple[int, ...]:
    """Gumawa ng 25-cell layout. Ang FREE center ay 0."""
    cells: list[int] = [FREE_VALUE] * CELL_COUNT
    for column, (low, high) in enumerate(COLUMN_RANGES):
        target_rows = [row for row in range(GRID_SIZE) if column + row * GRID_SIZE != FREE_INDEX]
        picks = rng.sample(range(low, high + 1), len(target_rows))
        for row, value in zip(target_rows, picks, strict=True):
            cells[column + row * GRID_SIZE] = value
    return tuple(cells)


def new_serial(rng: Randomizer) -> str:
    """Human-readable na serial. Iniiwasan ang magkamukhang character."""
    body = "".join(rng.choice(_SERIAL_ALPHABET) for _ in range(_SERIAL_BODY_LENGTH))
    return f"BB-{body[:5]}-{body[5:]}"


def generate_card(rng: Randomizer) -> Card:
    card = Card(serial=new_serial(rng), numbers=generate_numbers(rng))
    validate(card.numbers)
    return card


def validate(numbers: tuple[int, ...]) -> None:
    """I-raise ang `CardError` kung hindi valid ang layout.

    Ginagamit ito bilang sanity gate sa issuance at sa verification ng claim.
    """
    if len(numbers) != CELL_COUNT:
        raise CardError(f"expected {CELL_COUNT} cells, got {len(numbers)}")
    if numbers[FREE_INDEX] != FREE_VALUE:
        raise CardError("the center cell must be FREE (0)")

    filled = [value for index, value in enumerate(numbers) if index != FREE_INDEX]
    if len(set(filled)) != len(filled):
        raise CardError("duplicate number on the card")
    if FREE_VALUE in filled:
        raise CardError("blank cell outside the center")

    for index, value in enumerate(numbers):
        if index == FREE_INDEX:
            continue
        low, high = COLUMN_RANGES[column_of(index)]
        if not low <= value <= high:
            letter = COLUMN_LETTERS[column_of(index)]
            raise CardError(f"{value} is outside the range of column {letter} ({low}-{high})")
