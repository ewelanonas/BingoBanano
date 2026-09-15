"""Typed settings. Walang secret na naka-hardcode dito.

Basahin mula sa environment o `.env`. Ang mga secret ay walang default value —
kung wala, hindi mag-i-start ang app (fail closed) kaysa tumakbo nang bukas.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MissingSecretError(RuntimeError):
    """Kulang ang required secret sa configuration."""


class RadioNotConfiguredError(RuntimeError):
    """Walang Spotify credentials, kaya patay ang Banano Radio.

    Hiwalay ito sa `MissingSecretError` nang sadya. Ang bingo ay tumatakbo nang
    walang Spotify — kung isasama natin ito sa `require_secrets()`, ang bawat
    party na walang Spotify ay hindi na makakapagsimula ng app.
    """


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="BINGO_",
        extra="ignore",
    )

    # Ang base URL na naka-encode sa QR. HUWAG gamitin ang localhost dito kung
    # ang phone ang mag-scan: ang phone ang magiging localhost at mabibigo.
    # Gamitin ang LAN IP ng dev machine, halimbawa http://192.168.1.10:8000
    public_base_url: str = "http://127.0.0.1:8000"

    database_url: str = "sqlite+aiosqlite:///./bingobanano.db"

    # Password ng host. Walang default — tingnan ang `.env.example`.
    operator_api_key: str = ""

    # Limang minuto: sapat para makapag-scan ang bisitang kumakain o nag-uusap
    # pa. Ang 120 ay masyadong mabilis sa totoong party.
    pairing_ttl_seconds: int = Field(default=300, ge=30, le=900)
    default_card_count: int = Field(default=2, ge=1, le=24)
    max_card_count: int = Field(default=12, ge=1, le=24)
    pairing_rate_limit_per_minute: int = Field(default=20, ge=1, le=600)
    # Hiwalay at mas maluwag: normal na pindutin ang BINGO nang paulit-ulit
    # habang tumatakbo ang laro, hindi iyon abuso.
    bingo_rate_limit_per_minute: int = Field(default=120, ge=1, le=1200)
    # Masikip sadya. Isang beses lang naman pumapasok ang host, at kapag naka-
    # tunnel ang server ay abot ito ng internet — dito papasok ang brute force.
    login_rate_limit_per_minute: int = Field(default=10, ge=1, le=120)
    # Ang pag-decode ng litrato ay ang pinakamabigat na trabaho sa app na kayang
    # simulan ng isang request. Dalawang file lang naman ang ina-upload, kaya
    # sapat na ang 12 kada minuto para sa pagpapalit-palit ng crop.
    photo_upload_rate_limit_per_minute: int = Field(default=12, ge=1, le=120)

    # --- Banano Radio (Spotify) ---
    # Galing sa developer.spotify.com dashboard ng host. Walang default: kapag
    # blangko, patay lang ang Radio at tumatakbo pa rin ang bingo.
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    # KAILANGANG loopback address. Bawal na ang `localhost` sa Spotify, at bawal
    # ang plain HTTP maliban sa loopback — kaya `127.0.0.1` talaga ang nakasulat
    # dito at hindi ang tunnel URL. Ang host lang naman ang pumipindot ng
    # Connect, at nasa makina niya ang browser na iyon.
    spotify_redirect_uri: str = "http://127.0.0.1:8000/operator/radio/callback"
    # Maluwag: normal na mag-type-type ang bisita habang naghahanap ng kanta.
    radio_search_rate_limit_per_minute: int = Field(default=30, ge=1, le=600)
    # Ang radio join ay gumagawa ng Player row at bukas sa kahit sino na may QR.
    # Masikip sadya: isang beses lang naman sumasali ang bisita, at kapag
    # naka-tunnel ang server ay abot ito ng internet.
    radio_join_rate_limit_per_minute: int = Field(default=10, ge=1, le=120)
    radio_request_rate_limit_per_minute: int = Field(default=10, ge=1, le=120)
    # Ang totoong panangga sa "ako lang ang DJ" ay ito, hindi ang rate limit.
    # Dalawang minuto kada bisita: kasya pa rin ang lahat sa isang party.
    radio_request_cooldown_seconds: int = Field(default=120, ge=0, le=3600)
    # Walang 20-minutong ambient track sa kaarawan.
    radio_max_track_seconds: int = Field(default=600, ge=30, le=3600)
    # Kung kalalabas lang ng kantang ito, hindi na puwedeng i-request muli.
    radio_duplicate_window_minutes: int = Field(default=60, ge=0, le=720)
    # Isang upstream call kada ganitong dami ng segundo, gaano man karaming
    # bisita ang nakatingin sa now-playing. Dito namamatay ang rate limit ng
    # Spotify kapag 20 phone ang nag-poll nang sabay.
    radio_now_playing_cache_seconds: int = Field(default=5, ge=1, le=60)

    def radio_configured(self) -> bool:
        return bool(self.spotify_client_id and self.spotify_client_secret)

    def require_radio(self) -> None:
        if not self.radio_configured():
            raise RadioNotConfiguredError(
                "BINGO_SPOTIFY_CLIENT_ID and BINGO_SPOTIFY_CLIENT_SECRET are not set, "
                "so Banano Radio is off. Create an app at developer.spotify.com, add "
                f"{self.spotify_redirect_uri} as a Redirect URI, then put the two "
                "values in .env."
            )

    def require_secrets(self) -> None:
        if len(self.operator_api_key) < 32:
            raise MissingSecretError(
                "BINGO_OPERATOR_API_KEY is missing or shorter than 32 characters. "
                "Copy .env.example to .env and generate a value with: "
                'python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
