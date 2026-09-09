"""Pairing security at ang buong claim flow.

Ang mga test na ito ay tumutugma sa aktuwal na attack path: replay ng nonce,
expired nonce, at sabay-sabay na claim.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.db.models import AuditEvent, BingoCard, PairingSession, Player
from app.db.session import get_session_factory
from app.services.identity import nonce_fingerprint, utcnow

PLAYER_NAME = "Ana"


def claim_form(name: str = PLAYER_NAME) -> dict[str, str]:
    return {"given_name": name}


async def make_round(
    client: AsyncClient,
    headers: dict[str, str],
    pattern: str = "any_line",
) -> str:
    response = await client.post("/api/rounds", json={"pattern": pattern}, headers=headers)
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


async def create_pairing(
    client: AsyncClient,
    headers: dict[str, str],
    **payload: object,
) -> dict[str, object]:
    """Gumawa ng round kapag walang binigay, tapos mag-request ng QR para dito."""
    body = dict(payload)
    if "round_id" not in body:
        body["round_id"] = await make_round(client, headers)
    response = await client.post("/api/pairing", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def nonce_from(pairing: dict[str, object]) -> str:
    return str(pairing["pair_url"]).rsplit("/", 1)[-1]


async def test_create_pairing_requires_operator_auth(client: AsyncClient) -> None:
    response = await client.post("/api/pairing", json={"round_id": "x"})
    assert response.status_code == 401


async def test_create_pairing_rejects_wrong_api_key(client: AsyncClient) -> None:
    response = await client.post(
        "/api/pairing", json={"round_id": "x"}, headers={"X-API-Key": "wrong"}
    )
    assert response.status_code == 401


async def test_create_pairing_returns_qr_and_scannable_url(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    round_id = await make_round(client, operator_headers, "blackout")
    pairing = await create_pairing(client, operator_headers, round_id=round_id, card_count=3)

    assert pairing["qr_data_uri"].startswith("data:image/svg+xml")
    assert pairing["pair_url"].startswith("http://192.168.1.10:8000/pair/")
    assert pairing["card_count"] == 3
    assert pairing["round_id"] == round_id
    assert pairing["pattern"] == "blackout"
    # LAN IP kaya walang localhost warning, pero plain HTTP pa rin kaya may
    # transport warning.
    warnings = [str(item) for item in pairing["warnings"]]
    assert not any("localhost" in item for item in warnings)
    assert any("HTTPS" in item for item in warnings)
    # Ang nonce ay nasa QR lang, hindi sa ibang field.
    assert "nonce" not in pairing


async def test_status_endpoint_never_leaks_the_nonce(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    pairing = await create_pairing(client, operator_headers)
    response = await client.get(f"/api/pairing/{pairing['id']}", headers=operator_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["round_id"] == pairing["round_id"]
    assert nonce_from(pairing) not in response.text


async def test_card_count_above_maximum_is_rejected(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    round_id = await make_round(client, operator_headers)
    response = await client.post(
        "/api/pairing",
        json={"round_id": round_id, "card_count": 20},
        headers=operator_headers,
    )
    assert response.status_code == 422


async def test_landing_page_then_claim_issues_cards(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    pairing = await create_pairing(client, operator_headers, card_count=2)
    nonce = nonce_from(pairing)

    landing = await client.get(f"/pair/{nonce}")
    assert landing.status_code == 200
    assert "Generate my cards" in landing.text

    claimed = await client.post(f"/pair/{nonce}/claim", data=claim_form())
    assert claimed.status_code == 200
    assert "BB-" in claimed.text
    assert "FREE" in claimed.text
    assert "/play/" in claimed.text

    async with get_session_factory()() as db:
        cards = (await db.scalars(select(BingoCard))).all()
    assert len(cards) == 2
    assert len({card.serial for card in cards}) == 2
    for card in cards:
        assert len(card.numbers) == 25
        assert card.numbers[12] == 0


async def test_joining_only_needs_a_nickname(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    """Party game: walang birth date, walang age gate, palayaw lang."""
    pairing = await create_pairing(client, operator_headers)
    nonce = nonce_from(pairing)

    landing = await client.get(f"/pair/{nonce}")
    assert "birth" not in landing.text.lower()
    assert 'type="date"' not in landing.text

    claimed = await client.post(f"/pair/{nonce}/claim", data={"given_name": "  Tita Baby  "})
    assert claimed.status_code == 200

    async with get_session_factory()() as db:
        player = await db.scalar(select(Player))
    assert player is not None
    assert player.given_name == "Tita Baby"
    assert player.play_token


async def test_claim_without_a_nickname_is_rejected(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    pairing = await create_pairing(client, operator_headers)
    nonce = nonce_from(pairing)

    response = await client.post(f"/pair/{nonce}/claim", data={"given_name": "   "})
    assert response.status_code == 400

    async with get_session_factory()() as db:
        assert (await db.scalars(select(BingoCard))).all() == []
        pending = await db.scalar(select(PairingSession).where(PairingSession.nonce == nonce))
    # Hindi nasunog ang QR dahil sa isang denial.
    assert pending is not None
    assert pending.status == "pending"


async def test_nonce_cannot_be_replayed(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    pairing = await create_pairing(client, operator_headers)
    nonce = nonce_from(pairing)

    first = await client.post(f"/pair/{nonce}/claim", data=claim_form())
    assert first.status_code == 200

    second = await client.post(f"/pair/{nonce}/claim", data=claim_form())
    assert second.status_code == 410
    assert "already been used" in second.text or "expired" in second.text.lower()

    landing = await client.get(f"/pair/{nonce}")
    assert landing.status_code == 410


async def test_expired_nonce_is_refused(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    pairing = await create_pairing(client, operator_headers)
    nonce = nonce_from(pairing)

    async with get_session_factory()() as db:
        await db.execute(
            update(PairingSession)
            .where(PairingSession.nonce == nonce)
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        await db.commit()

    assert (await client.get(f"/pair/{nonce}")).status_code == 410
    claim = await client.post(f"/pair/{nonce}/claim", data=claim_form())
    assert claim.status_code == 410

    async with get_session_factory()() as db:
        assert (await db.scalars(select(BingoCard))).all() == []


async def test_concurrent_claims_only_one_wins(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    pairing = await create_pairing(client, operator_headers, card_count=2)
    nonce = nonce_from(pairing)

    responses = await asyncio.gather(
        client.post(f"/pair/{nonce}/claim", data=claim_form("Ana")),
        client.post(f"/pair/{nonce}/claim", data=claim_form("Ben")),
        return_exceptions=True,
    )

    statuses = [r.status_code for r in responses if not isinstance(r, BaseException)]
    assert statuses.count(200) == 1, f"dapat isa lang ang mananalo, nakuha {statuses}"

    async with get_session_factory()() as db:
        cards = (await db.scalars(select(BingoCard))).all()
    assert len(cards) == 2  # isang claim lang ang nakakuha ng cards


async def test_audit_trail_stores_hashed_nonce_only(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    pairing = await create_pairing(client, operator_headers)
    nonce = nonce_from(pairing)
    await client.post(f"/pair/{nonce}/claim", data=claim_form())

    async with get_session_factory()() as db:
        events = (await db.scalars(select(AuditEvent))).all()

    recorded = {event.event: event for event in events}
    assert "pairing.created" in recorded
    assert "pairing.claim" in recorded
    assert recorded["pairing.claim"].outcome == "ok"

    with_nonce = [event for event in events if event.nonce_hash is not None]
    assert with_nonce, "dapat may naka-record na pairing event"
    for event in with_nonce:
        assert event.nonce_hash != nonce
        assert event.nonce_hash == nonce_fingerprint(nonce)


async def test_localhost_base_url_raises_a_warning(
    client: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000")

    pairing = await create_pairing(client, operator_headers)
    warnings = pairing["warnings"]
    assert isinstance(warnings, list)
    assert any("localhost" in str(item) for item in warnings)
