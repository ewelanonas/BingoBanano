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
