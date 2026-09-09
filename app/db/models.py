"""SQLAlchemy models.

Design notes:
- Palayaw lang ang iniimbak tungkol sa player. Walang birth date, walang ID.
- Ang `AuditEvent` ay append-only. Walang code na nag-uupdate o nagde-delete ng
  rows dito.
- Ang nonce ay hindi kailanman naka-log nang buo, `nonce_hash` lang.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Dialect,
    ForeignKey,
    Index,
    Integer,
    String,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

PAIRING_PENDING = "pending"
PAIRING_CLAIMED = "claimed"
PAIRING_CANCELLED = "cancelled"

ROUND_OPEN = "open"
ROUND_DRAWING = "drawing"
ROUND_WON = "won"
ROUND_CLOSED = "closed"

CLAIM_VALID = "valid"
CLAIM_PATTERN_INCOMPLETE = "pattern_incomplete"
CLAIM_ROUND_NOT_DRAWING = "round_not_drawing"
CLAIM_LATE = "late"
CLAIM_ANNOUNCED = "announced"

# Sino ang naglalabas ng bola.
#   auto    - ang app ang bumubunot nang random
#   manual  - may pisikal na tambiolo, at ipinapasok ng host ang bawat bola
#   offline - wala talagang binibilang ang app; card dispenser lang ito
CALLER_AUTO = "auto"
CALLER_MANUAL = "manual"
CALLER_OFFLINE = "offline"
CALLER_MODES = (CALLER_AUTO, CALLER_MANUAL, CALLER_OFFLINE)

# Anong laro.
#   classic     - 75-ball pattern bingo; unahan sa pattern
#   elimination - parehong 5x5 card, pero labas ka kapag NALAHAT na ang 24 na
#                 numero mo. Ang huling may hindi pa natatawag na numero ang
#                 panalo.
GAME_CLASSIC = "classic"
GAME_ELIMINATION = "elimination"
GAME_TYPES = (GAME_CLASSIC, GAME_ELIMINATION)


def _new_id() -> str:
    return uuid.uuid4().hex


class UtcDateTime(TypeDecorator[datetime]):
    """Laging UTC-aware sa Python, laging UTC sa storage.

    Ang SQLite ay hindi nag-i-store ng tz offset, kaya kung walang normalization
    ay magkakahalo ang naive at aware na datetime at magiging silent bug ang
    expiry comparison. Dito iniiwasan iyon.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:  # noqa: ANN401
        return dialect.type_descriptor(DateTime(timezone=dialect.name != "sqlite"))

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime ang naipasa; gamitin ang aware UTC")
        as_utc = value.astimezone(UTC)
        return as_utc.replace(tzinfo=None) if dialect.name == "sqlite" else as_utc

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class GameRound(Base):
    """Isang laro: isang declared pattern at isang sequence ng bola.

    Ang pattern ay nakatakda sa paggawa pa ng round, bago ang unang draw.
    """

    __tablename__ = "game_round"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    join_code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    game_type: Mapped[str] = mapped_column(String(16), default=GAME_CLASSIC)
    # Ginagamit lang sa `classic`.
    pattern: Mapped[str] = mapped_column(String(32))
    caller_mode: Mapped[str] = mapped_column(String(16), default=CALLER_AUTO)
    status: Mapped[str] = mapped_column(String(16), default=ROUND_OPEN, index=True)
    label: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), default=None)
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), default=None)
    winning_draw_count: Mapped[int | None] = mapped_column(Integer, default=None)
    first_winner_card_id: Mapped[str | None] = mapped_column(String(32), default=None)

    draws: Mapped[list[Draw]] = relationship(back_populates="round", order_by="Draw.sequence_no")


class Draw(Base):
    """Isang nailabas na bola. Append-only.

    Ang dalawang unique constraint ang totoong panangga: hindi puwedeng
    lumabas nang dalawang beses ang parehong bola, at hindi puwedeng magkaroon
    ng dalawang bola sa parehong posisyon sa sequence.
    """

    __tablename__ = "draw"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    round_id: Mapped[str] = mapped_column(String(32), ForeignKey("game_round.id"), index=True)
    sequence_no: Mapped[int] = mapped_column(Integer)
    ball: Mapped[int] = mapped_column(Integer)
    drawn_at: Mapped[datetime] = mapped_column(UtcDateTime())

    round: Mapped[GameRound] = relationship(back_populates="draws")

    __table_args__ = (
        UniqueConstraint("round_id", "ball", name="uq_draw_round_ball"),
        UniqueConstraint("round_id", "sequence_no", name="uq_draw_round_sequence"),
    )


class Claim(Base):
    """Isang pagpindot ng BINGO, valid man o hindi. Append-only.

    Ini-record kahit tinanggihan — ang pattern ng maling claim ay kapaki-pakinabang
    kapag may reklamo o kapag may bug sa marking sa client.
    """

    __tablename__ = "claim"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    round_id: Mapped[str] = mapped_column(String(32), ForeignKey("game_round.id"), index=True)
    card_id: Mapped[str] = mapped_column(String(32), ForeignKey("bingo_card.id"))
    player_id: Mapped[str] = mapped_column(String(32), ForeignKey("player.id"))
    pattern: Mapped[str] = mapped_column(String(32))
    verified: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(String(32))
    draw_count: Mapped[int] = mapped_column(Integer)
    missing_cells: Mapped[int] = mapped_column(Integer)
    is_first_winner: Mapped[bool] = mapped_column(Boolean, default=False)
    claimed_at: Mapped[datetime] = mapped_column(UtcDateTime())

    # Sa cards-only mode ay walang binibilang ang server, kaya ang host ang
    # nagpapasya. Ito ang sinasabi ng phone na minarkahan ng bisita — hindi
    # katotohanan, kundi ang inaangkin niya, at ipinapakita sa host para may
    # matingnan siya. Hindi ito ginagamit sa anumang verification.
    reported_marks: Mapped[list[int]] = mapped_column(JSON, default=list)

    __table_args__ = (
        UniqueConstraint("round_id", "card_id", "draw_count", name="uq_claim_card_draw"),
    )


class PairingSession(Base):
    """Isang QR na ipinapakita sa website, hinihintay na ma-scan.

    Ang `nonce` ang credential. Single-use at may expiry.
    """

    __tablename__ = "pairing_session"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    nonce: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    round_id: Mapped[str] = mapped_column(String(32), ForeignKey("game_round.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=PAIRING_PENDING, index=True)
    card_count: Mapped[int] = mapped_column(Integer)
    kiosk_label: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime())
    claimed_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), default=None)
    claimed_by_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("player.id"), default=None
    )

    cards: Mapped[list[BingoCard]] = relationship(back_populates="session")

    __table_args__ = (Index("ix_pairing_status_expiry", "status", "expires_at"),)


class Player(Base):
    """Isang bisita sa party. Palayaw lang ang hinihingi.

    Walang birth date at walang identity hash: party game ito, hindi
    regulated na gaming. Ang kailangan lang ay pangalan sa scoreboard.
    """

    __tablename__ = "player"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    given_name: Mapped[str] = mapped_column(String(64))
    # Capability token para sa live board ng player: `/play/{play_token}`.
    # Hindi guessable, at ito lang ang nagbubukas ng cards niya.
    play_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())


class BingoCard(Base):
    """Ang naka-persist na layout. Ito ang source of truth sa dispute.

    Hindi kailanman ni-regenerate mula sa seed sa oras ng verification.
    """

    __tablename__ = "bingo_card"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    serial: Mapped[str] = mapped_column(String(24), index=True)
    round_id: Mapped[str] = mapped_column(String(32), ForeignKey("game_round.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(32), ForeignKey("pairing_session.id"))
    player_id: Mapped[str] = mapped_column(String(32), ForeignKey("player.id"), index=True)
    numbers: Mapped[list[int]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime())

    # Ginagamit lang sa elimination: kailan nakumpleto ang lahat ng numero ng
    # card, kaya labas na ang may hawak nito.
    eliminated_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), default=None)
    eliminated_at_draw: Mapped[int | None] = mapped_column(Integer, default=None)
    is_winner: Mapped[bool] = mapped_column(Boolean, default=False)

    session: Mapped[PairingSession] = relationship(back_populates="cards")

    __table_args__ = (
        UniqueConstraint("session_id", "serial", name="uq_card_session_serial"),
        Index("ix_card_round_eliminated", "round_id", "eliminated_at"),
    )


class AuditEvent(Base):
    """Append-only trail. Dito titingin kapag may nagtanong kung anong nangyari."""

    __tablename__ = "audit_event"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)
    event: Mapped[str] = mapped_column(String(48), index=True)
    outcome: Mapped[str] = mapped_column(String(16))
    nonce_hash: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    player_id: Mapped[str | None] = mapped_column(String(32), default=None)
    detail: Mapped[dict[str, str | int]] = mapped_column(JSON, default=dict)
