"""Randomness boundary for BingoBanano.

Lahat ng random na may kinalaman sa cards, draws, o pera ay dumadaan dito.
Ang `secrets.SystemRandom` lang ang production source. Ang `random` module ay
hindi kailanman ginagamit sa gaming logic.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from typing import Protocol


class Randomizer(Protocol):
    """Ang maliit na surface na kailangan ng domain layer."""

    def sample(self, population: Sequence[int], k: int) -> list[int]:
        """Pumili ng `k` na magkakaibang item mula sa `population`."""
        ...

    def choice(self, seq: Sequence[str]) -> str:
        """Pumili ng isang item mula sa `seq`."""
        ...


def system_randomizer() -> Randomizer:
    """Ang production randomizer. Nakabase sa OS CSPRNG."""
    return secrets.SystemRandom()


def seeded_randomizer(seed: int) -> Randomizer:
    """Deterministic randomizer para sa tests lamang.

    Huwag itong i-wire sa anumang runtime path. Nakalagay dito para hindi
    kailangang gumamit ng `random` module sa test files.
    """
    import random as _random  # noqa: S311 - tests only, hindi gaming path

    return _random.Random(seed)  # noqa: S311


def new_nonce() -> str:
    """One-time pairing nonce. Ito ang credential na naka-encode sa QR."""
    return secrets.token_urlsafe(32)


def new_play_token() -> str:
    """Capability token ng live board ng player."""
    return secrets.token_urlsafe(24)


_JOIN_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"  # walang malabong character


def new_join_code(length: int = 6) -> str:
    """Maikling code na nababasa nang malakas ng caller sa mga bisita."""
    return "".join(secrets.choice(_JOIN_CODE_ALPHABET) for _ in range(length))
