"""Server-side card issuance.

Ang cards ay ginagawa lang dito, sa server, pagkatapos ng compliance check at
pagkatapos ma-consume ang pairing nonce. Ang client ay hindi kailanman
nagpapadala ng card data.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BingoCard, PairingSession, Player
from app.domain.cards import Card, generate_card
from app.domain.rng import Randomizer
from app.services.identity import utcnow

_MAX_SERIAL_ATTEMPTS = 5


class IssuanceError(Exception):
    """Hindi nakagawa ng cards."""


async def issue_cards(
    db: AsyncSession,
    *,
    pairing: PairingSession,
    player: Player,
    rng: Randomizer,
) -> list[Card]:
    """Gumawa at i-persist ng `pairing.card_count` na cards.

    Ang buong layout ay naka-store. Hindi ito ni-regenerate mula sa seed sa
    verification — ang stored row ang source of truth sa dispute.
    """
    issued: list[Card] = []
    now = utcnow()

    for _ in range(pairing.card_count):
        card = await _persist_unique(db, pairing=pairing, player=player, rng=rng, at=now)
        issued.append(card)

    return issued


async def _persist_unique(
    db: AsyncSession,
    *,
    pairing: PairingSession,
    player: Player,
    rng: Randomizer,
    at: object,
) -> Card:
    for _ in range(_MAX_SERIAL_ATTEMPTS):
        card = generate_card(rng)
        savepoint = await db.begin_nested()
        db.add(
            BingoCard(
                serial=card.serial,
                round_id=pairing.round_id,
                session_id=pairing.id,
                player_id=player.id,
                numbers=list(card.numbers),
                created_at=at,
            )
        )
        try:
            await savepoint.commit()
        except IntegrityError:
            # Serial collision sa loob ng session. Bumuo ng bago.
            await savepoint.rollback()
            continue
        return card

    raise IssuanceError("Could not find a unique serial after several attempts.")
