"""Pag-abot sa labas ng LAN: tunnel, forwarded headers, at login hardening.

Kapag naka-tunnel, ang laro ay abot ng internet. Ang mga test na ito ang
nagbabantay sa mga bagay na nagiging mahalaga doon.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.api.security import CSRF_HEADER, OPERATOR_COOKIE, get_limiter
from tests.conftest import TEST_API_KEY

HTTPS_BASE = "https://testserver"


async def make_round(client: AsyncClient, headers: dict[str, str], base: str = "") -> str:
    response = await client.post(
        f"{base}/api/rounds", json={"pattern": "any_line"}, headers=headers
    )
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


# --- Brute force sa host login -------------------------------------------


async def test_login_is_rate_limited(client: AsyncClient) -> None:
    """Ang /operator ay abot ng internet kapag naka-tunnel, kaya may limit."""
    from app.config import get_settings

    limit = get_settings().login_rate_limit_per_minute
    get_limiter().reset()

    for _ in range(limit):
        attempt = await client.post("/operator/session", data={"api_key": "guess"})
        assert attempt.status_code == 303

    blocked = await client.post("/operator/session", data={"api_key": "guess"})
    assert blocked.status_code == 429


async def test_rate_limited_login_blocks_even_the_correct_key(client: AsyncClient) -> None:
    """Ang limit ay bago pa ang pag-check ng key, kaya hindi ito oracle."""
    from app.config import get_settings

    limit = get_settings().login_rate_limit_per_minute
    get_limiter().reset()

    for _ in range(limit):
        await client.post("/operator/session", data={"api_key": "guess"})

    correct = await client.post("/operator/session", data={"api_key": TEST_API_KEY})
    assert correct.status_code == 429
    assert OPERATOR_COOKIE not in correct.cookies


async def test_a_fresh_client_ip_gets_its_own_budget(client: AsyncClient) -> None:
    get_limiter().reset()
    response = await client.post("/operator/session", data={"api_key": TEST_API_KEY})
    assert response.status_code == 303


# --- Cookie sa likod ng tunnel -------------------------------------------


async def test_cookie_is_not_secure_over_plain_http(client: AsyncClient) -> None:
    get_limiter().reset()
    response = await client.post("/operator/session", data={"api_key": TEST_API_KEY})
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    # Kung `Secure` ito sa plain HTTP, hindi na maipapadala ng browser ang cookie
    # at hindi makakapasok ang host sa LAN setup.
    assert "Secure" not in set_cookie


async def test_cookie_is_secure_when_the_tunnel_reports_https(client: AsyncClient) -> None:
    """Ginagaya ang ginagawa ng uvicorn --proxy-headers sa likod ng tunnel."""
    get_limiter().reset()
    # Ang ASGI scope scheme ay galing sa request URL, kaya absolute https URL
    # ang gamit dito. Ganito rin ang nakikita ng app kapag inayos na ni uvicorn
    # ang scheme mula sa X-Forwarded-Proto.
    response = await client.post(
        HTTPS_BASE + "/operator/session",
        data={"api_key": TEST_API_KEY},
    )
    set_cookie = response.headers["set-cookie"]
    assert "Secure" in set_cookie
    assert "HttpOnly" in set_cookie


async def test_the_whole_host_flow_works_over_https(client: AsyncClient) -> None:
    get_limiter().reset()
    await client.post(HTTPS_BASE + "/operator/session", data={"api_key": TEST_API_KEY})
    kiosk = await client.get(HTTPS_BASE + "/kiosk")
    assert kiosk.status_code == 200

    token = kiosk.text.split('const CSRF_TOKEN = "', 1)[1].split('"', 1)[0]
    # Buong flow sa HTTPS: ang Secure cookie ay hindi ipapadala sa plain HTTP,
    # kaya kung mahalo ang scheme ay 401 ang aabutin — tamang behaviour iyon.
    round_id = await make_round(client, {CSRF_HEADER: token}, base=HTTPS_BASE)

    pairing = await client.post(
        HTTPS_BASE + "/api/pairing",
        json={"round_id": round_id},
        headers={CSRF_HEADER: token},
    )
    assert pairing.status_code == 200


# --- Warnings sa base URL -------------------------------------------------


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://localhost:8000", "only works on this machine"),
        ("http://127.0.0.1:8000", "only works on this machine"),
        ("http://192.168.1.10:8000", "same Wi-Fi"),
        ("http://10.0.0.5:8000", "same Wi-Fi"),
        ("http://172.16.4.4:8000", "same Wi-Fi"),
        ("http://bingo.example.com", "plain"),
    ],
)
async def test_base_url_warnings(
    client: AsyncClient,
    operator_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
    expected: str,
) -> None:
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "public_base_url", base_url)
    round_id = await make_round(client, operator_headers)
    pairing = await client.post(
        "/api/pairing", json={"round_id": round_id}, headers=operator_headers
    )
    warnings = " ".join(str(item) for item in pairing.json()["warnings"])
    assert expected in warnings, warnings


async def test_https_tunnel_url_raises_no_warning(
    client: AsyncClient, operator_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ito ang tamang setup para sa bisitang nasa mobile data."""
    from app.config import get_settings

    monkeypatch.setattr(
        get_settings(), "public_base_url", "https://sunny-banana-42.trycloudflare.com"
    )
    round_id = await make_round(client, operator_headers)
    pairing = await client.post(
        "/api/pairing", json={"round_id": round_id}, headers=operator_headers
    )
    body = pairing.json()
    assert body["warnings"] == []
    assert body["pair_url"].startswith("https://sunny-banana-42.trycloudflare.com/pair/")
