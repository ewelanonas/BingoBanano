"""Maliliit na helper para sa oras at sa safe na pag-log."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def nonce_fingerprint(nonce: str) -> str:
    """Safe-to-log na representasyon ng nonce. Hindi reversible."""
    return hashlib.sha256(nonce.encode()).hexdigest()
