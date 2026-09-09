"""Bingo patterns bilang 25-bit bitmask. Pure logic, walang I/O.

Bit `i` ay tumutugma sa cell index `i` (row-major). Ang FREE center (bit 12) ay
laging pre-marked, kaya kasama ito sa bawat pattern na dumadaan sa center.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.domain.cards import CELL_COUNT, FREE_INDEX, GRID_SIZE

FREE_MASK = 1 << FREE_INDEX
FULL_MASK = (1 << CELL_COUNT) - 1


def mask_from_cells(cells: Iterable[int]) -> int:
    mask = 0
    for cell in cells:
        if not 0 <= cell < CELL_COUNT:
            raise ValueError(f"cell index {cell} is outside 0-{CELL_COUNT - 1}")
        mask |= 1 << cell
    return mask


def _row(row: int) -> int:
    return mask_from_cells(row * GRID_SIZE + col for col in range(GRID_SIZE))


def _column(column: int) -> int:
    return mask_from_cells(column + row * GRID_SIZE for row in range(GRID_SIZE))


_DIAGONAL_DOWN = mask_from_cells(i * GRID_SIZE + i for i in range(GRID_SIZE))
_DIAGONAL_UP = mask_from_cells(i * GRID_SIZE + (GRID_SIZE - 1 - i) for i in range(GRID_SIZE))
_FOUR_CORNERS = mask_from_cells((0, 4, 20, 24))
_POSTAGE_STAMP = mask_from_cells((3, 4, 8, 9))
_LETTER_X = _DIAGONAL_DOWN | _DIAGONAL_UP
_CROSS = _row(2) | _column(2)
_KITE = mask_from_cells((0, 1, 2, 5, 6, 10, 18, 24))

PATTERNS: dict[str, int] = {
    "any_line": 0,  # special-cased sa `is_win`
    "row_1": _row(0),
    "row_2": _row(1),
    "row_3": _row(2),
    "row_4": _row(3),
    "row_5": _row(4),
    "column_b": _column(0),
    "column_i": _column(1),
    "column_n": _column(2),
    "column_g": _column(3),
    "column_o": _column(4),
    "diagonal_down": _DIAGONAL_DOWN,
    "diagonal_up": _DIAGONAL_UP,
    "four_corners": _FOUR_CORNERS,
    "postage_stamp": _POSTAGE_STAMP,
    "letter_x": _LETTER_X,
    "cross": _CROSS,
    "kite": _KITE,
    "blackout": FULL_MASK,
}

ANY_LINE_MASKS: tuple[int, ...] = (
    *(_row(row) for row in range(GRID_SIZE)),
    *(_column(column) for column in range(GRID_SIZE)),
    _DIAGONAL_DOWN,
    _DIAGONAL_UP,
)


def marked_mask(numbers: tuple[int, ...], drawn: Iterable[int]) -> int:
    """Ang mask ng na-cover na cells ng isang card sa ibinigay na draws.

    Ang FREE center ay laging marked.
    """
    drawn_set = set(drawn)
    mask = FREE_MASK
    for index, value in enumerate(numbers):
        if index != FREE_INDEX and value in drawn_set:
            mask |= 1 << index
    return mask


def is_win(marked: int, pattern: str) -> bool:
    """`True` kung na-cover ng `marked` ang buong declared pattern."""
    if pattern == "any_line":
        return any(marked & line == line for line in ANY_LINE_MASKS)
    try:
        required = PATTERNS[pattern]
    except KeyError as exc:
        raise ValueError(f"unknown pattern: {pattern}") from exc
    return marked & required == required


def cells_of(mask: int) -> list[int]:
    """Ang cell indices na naka-set sa isang mask."""
    return [cell for cell in range(CELL_COUNT) if mask >> cell & 1]


def pattern_cell_groups(pattern: str) -> list[list[int]]:
    """Ang mga cell group na puwedeng manalo sa isang pattern.

    Ginagamit ng client para mag-highlight. Presentation lang ito — ang server
    pa rin ang nagpapasya kung panalo.
    """
    if pattern == "any_line":
        return [cells_of(line) for line in ANY_LINE_MASKS]
    return [cells_of(PATTERNS[pattern])]


def missing_count(marked: int, pattern: str) -> int:
    """Ilang cell pa ang kailangan. Ginagamit sa 'isa na lang' na UX."""
    if pattern == "any_line":
        return min((line & ~marked).bit_count() for line in ANY_LINE_MASKS)
    required = PATTERNS[pattern]
    return (required & ~marked).bit_count()
