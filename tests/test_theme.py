"""Ang tema at ang celebration popup.

Ang punto ng mga test na ito: gumagana ang app kahit wala pa ang mga litrato.
Ang mga file na `called.png` at `bingo.png` ay hindi naka-commit, kaya kung
naging kailangan sila ay masisira ang party.
"""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient

from app.config import Settings
from tests.conftest import TEST_API_KEY
from tests.test_rounds_api import join, make_round

STATIC = Path(__file__).resolve().parents[1] / "app" / "web" / "static"


def test_qr_expiry_defaults_to_five_minutes() -> None:
    """Ang 120s ay masyadong mabilis para sa bisitang kumakain pa.

    Ang class default ang tinitingnan at hindi ang buong settings, dahil ang
    `.env` ng makina ay puwedeng may sariling value.
    """
    assert Settings.model_fields["pairing_ttl_seconds"].default == 300


def test_theme_assets_exist() -> None:
    assert (STATIC / "theme.css").is_file()
    assert (STATIC / "dino.svg").is_file()
    assert (STATIC / "celebrate.js").is_file()


def test_photos_are_optional() -> None:
    """Ang litrato ay background-image na may kulay sa ilalim, hindi img tag."""
    css = (STATIC / "theme.css").read_text(encoding="utf-8")

    called = css.split("td.marked {", 1)[1].split("}", 1)[0]
    assert 'background-image: url("/static/img/called.png")' in called
    # Ang fallback na kulay ang dahilan kung bakit maayos pa rin kapag wala ang file.
    assert "background-color: var(--accent)" in called

    photo = css.split(".celebration-photo {", 1)[1].split("}", 1)[0]
    assert "/static/img/bingo.png" in photo
    assert "radial-gradient" in photo


def test_theme_is_removable_in_one_line() -> None:
    base = (
        Path(__file__).resolve().parents[1] / "app" / "web" / "templates" / "base.html"
    ).read_text(encoding="utf-8")
    assert base.count('href="/static/theme.css"') == 1
    # Nakasunod sa app.css para ito ang nananaig.
    assert base.index("app.css") < base.index("theme.css")


async def test_player_page_carries_the_celebration_popup(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    game = await make_round(client, operator_headers)
    token = await join(client, operator_headers, game["id"])

    page = await client.get(f"/play/{token}")
    assert page.status_code == 200
    assert 'id="celebration"' in page.text
    assert "/static/celebrate.js" in page.text
    # Nakatago hangga't walang nanalo.
    assert 'class="celebration" id="celebration" hidden' in page.text


async def test_caller_page_carries_the_celebration_popup(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    await client.post("/operator/session", data={"api_key": TEST_API_KEY})
    game = await make_round(client, operator_headers)

    page = await client.get(f"/caller/{game['id']}")
    assert page.status_code == 200
    assert 'id="celebration"' in page.text
    assert "showCelebration(" in page.text


async def test_elimination_player_page_carries_the_popup(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    created = await client.post(
        "/api/rounds",
        json={"game_type": "elimination", "label": "party"},
        headers=operator_headers,
    )
    assert created.status_code == 200, created.text
    game = created.json()
    token = await join(client, operator_headers, game["id"])

    page = await client.get(f"/play/{token}")
    assert page.status_code == 200
    assert 'id="celebration"' in page.text
    assert "WINNER!" in page.text
