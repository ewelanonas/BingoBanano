"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import history, operator, pairing, play, rounds
from app.api.deps import STATIC_DIR
from app.config import MissingSecretError, get_settings
from app.db.session import create_schema, dispose_engine

logger = logging.getLogger("bingobanano")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # Fail closed sa startup: mas mabuting hindi mag-start kaysa tumakbo nang
    # walang operator key o walang identity pepper.
    settings.require_secrets()
    await create_schema()
    logger.info("BingoBanano ready. QR base URL: %s", settings.public_base_url)
    try:
        yield
    finally:
        await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="BingoBanano",
        version="0.1.0",
        summary="75-ball bingo card issuance na may QR device pairing",
        lifespan=lifespan,
    )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(operator.router)
    app.include_router(rounds.router)
    app.include_router(pairing.router)
    app.include_router(play.router)
    app.include_router(history.router)

    @app.exception_handler(MissingSecretError)
    async def _missing_secret(request: Request, exc: MissingSecretError) -> JSONResponse:
        logger.error("configuration incomplete: %s", exc)
        # Walang internal na detalye sa user-facing response.
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "The server configuration is incomplete."},
        )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
