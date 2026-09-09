"""Draw logic para sa 75-ball bingo. Pure, walang I/O.

Ang draw ay walang replacement: bawat bola ay isang beses lang sa isang round.
Ang pagpili ay uniform sa mga natitirang bola.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.domain.cards import COLUMN_LETTERS, column_for_number
from app.domain.rng import Randomizer

BALL_MIN = 1
BALL_MAX = 75
ALL_BALLS: frozenset[int] = frozenset(range(BALL_MIN, BALL_MAX + 1))
TOTAL_BALLS = len(ALL_BALLS)


class DrawError(ValueError):
    """Hindi valid na draw state."""


class PoolExhaustedError(DrawError):
    """Wala nang natitirang bola sa round."""


def validate_ball(ball: int) -> int:
    if ball not in ALL_BALLS:
        raise DrawError(f"ball {ball} is outside {BALL_MIN}-{BALL_MAX}")
    return ball


def remaining_balls(drawn: Iterable[int]) -> list[int]:
    """Ang mga bola na hindi pa nalalabas, sorted para deterministic ang order."""
    drawn_set = set(drawn)
    unknown = drawn_set - ALL_BALLS
    if unknown:
        raise DrawError(f"drawn balls outside 1-75: {sorted(unknown)}")
    return sorted(ALL_BALLS - drawn_set)


def pick_next(drawn: Sequence[int], rng: Randomizer) -> int:
    """Pumili ng susunod na bola nang uniform mula sa natitira.

    Nagre-raise ng `PoolExhaustedError` kapag kumpleto na ang 75.
    """
    if len(set(drawn)) != len(drawn):
        raise DrawError("duplicate in the drawn sequence")
    pool = remaining_balls(drawn)
    if not pool:
        raise PoolExhaustedError("all 75 balls have been drawn")
    return rng.sample(pool, 1)[0]


def letter_for(ball: int) -> str:
    """Ang B-I-N-G-O na letra ng isang bola."""
    return COLUMN_LETTERS[column_for_number(validate_ball(ball))]


def format_call(ball: int) -> str:
    """Ang sinasabi ng caller, halimbawa `B-7`."""
    return f"{letter_for(ball)}-{ball}"


def balls_by_letter(drawn: Iterable[int]) -> dict[str, list[int]]:
    """Ang drawn balls na naka-group kada column, para sa caller board."""
    grouped: dict[str, list[int]] = {letter: [] for letter in COLUMN_LETTERS}
    for ball in drawn:
        grouped[letter_for(ball)].append(ball)
    for balls in grouped.values():
        balls.sort()
    return grouped
