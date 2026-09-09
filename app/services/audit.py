"""Append-only audit trail."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent
from app.services.identity import nonce_fingerprint, utcnow

OUTCOME_OK = "ok"
OUTCOME_DENIED = "denied"
OUTCOME_ERROR = "error"


async def record(
    db: AsyncSession,
    *,
    event: str,
    outcome: str,
    nonce: str | None = None,
    player_id: str | None = None,
    detail: dict[str, str | int] | None = None,
) -> None:
    """Isulat ang isang audit row.

    Ang `nonce` ay hashed bago i-store — hindi kailanman naka-log nang buo.
    Huwag maglagay ng PII sa `detail`.
    """
    db.add(
        AuditEvent(
            at=utcnow(),
            event=event,
            outcome=outcome,
            nonce_hash=nonce_fingerprint(nonce) if nonce else None,
            player_id=player_id,
            detail=detail or {},
        )
    )
