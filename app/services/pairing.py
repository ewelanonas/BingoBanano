"""Nonce lifecycle ng QR pairing.

Ang nonce ay credential: 256-bit entropy, short-lived, at single-use. Ang
pag-consume ay isang atomic conditional UPDATE, kaya kahit sabay-sabay ang mga
claim ay isa lang ang mananalo.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import PAIRING_CLAIMED, PAIRING_PENDING, PairingSession
from app.domain.rng import new_nonce
from app.services.identity import utcnow


class PairingUnavailableError(Exception):
    """Ang nonce ay hindi na claimable: expired, nagamit na, o wala."""


async def create_pairing(
    db: AsyncSession,
    settings: Settings,
    *,
    round_id: str,
    card_count: int,
    kiosk_label: str,
) -> PairingSession:
    now = utcnow()
    pairing = PairingSession(
        nonce=new_nonce(),
        round_id=round_id,
        status=PAIRING_PENDING,
        card_count=card_count,
        kiosk_label=kiosk_label,
        created_at=now,
        expires_at=now + timedelta(seconds=settings.pairing_ttl_seconds),
    )
    db.add(pairing)
    await db.flush()
    return pairing


async def peek(db: AsyncSession, nonce: str) -> PairingSession | None:
    """Basahin ang pairing para sa landing page. Read-only, walang side effect."""
    return await db.scalar(select(PairingSession).where(PairingSession.nonce == nonce))


async def get_by_id(db: AsyncSession, pairing_id: str) -> PairingSession | None:
    return await db.scalar(select(PairingSession).where(PairingSession.id == pairing_id))


def is_claimable(pairing: PairingSession | None) -> bool:
    if pairing is None:
        return False
    return pairing.status == PAIRING_PENDING and pairing.expires_at > utcnow()


async def consume(db: AsyncSession, *, nonce: str, player_id: str) -> PairingSession:
    """Atomic single-use consume ng nonce.

    Ang guard ay nasa WHERE clause, hindi sa Python — kaya walang
    check-then-act race. Kung 0 rows ang naapektuhan, hindi claimable.
    """
    now = utcnow()
    result = await db.execute(
        update(PairingSession)
        .where(
            PairingSession.nonce == nonce,
            PairingSession.status == PAIRING_PENDING,
            PairingSession.expires_at > now,
        )
        .values(status=PAIRING_CLAIMED, claimed_at=now, claimed_by_id=player_id)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise PairingUnavailableError("The QR is expired, already used, or unknown.")

    pairing = await db.scalar(select(PairingSession).where(PairingSession.nonce == nonce))
    if pairing is None:  # pragma: no cover - hindi mangyayari pagkatapos ng UPDATE
        raise PairingUnavailableError("The pairing vanished after being consumed.")
    return pairing
