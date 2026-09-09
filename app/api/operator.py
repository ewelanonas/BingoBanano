"""Operator-facing routes: login at ang kiosk na nagpapakita ng QR.

Ang API key ay hindi kailanman naipapadala sa browser JavaScript. Pinapalitan
ito ng HttpOnly session cookie at isang CSRF token na nasa page markup.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.deps import templates
from app.api.security import (
    OPERATOR_COOKIE,
    get_session_store,
    verify_api_key,
)
from app.config import get_settings
from app.domain.patterns import PATTERNS

router = APIRouter()


@router.get("/", include_in_schema=False)
async def root(request: Request) -> RedirectResponse:
    store = get_session_store()
    if store.get(request.cookies.get(OPERATOR_COOKIE)) is not None:
        return RedirectResponse("/kiosk", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse("/operator", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/operator", response_class=HTMLResponse, include_in_schema=False)
async def operator_login(request: Request, error: str | None = None) -> Response:
    return templates.TemplateResponse(
        request,
        "operator_login.html",
        {"error": error},
    )


@router.post("/operator/session", include_in_schema=False)
async def operator_session(
    request: Request,
    api_key: Annotated[str, Form()],
) -> RedirectResponse:
    settings = get_settings()
    settings.require_secrets()

    if not verify_api_key(api_key, settings):
        # Walang detalye sa error message — hindi sinasabi kung bakit mali.
        return RedirectResponse(
            "/operator?error=invalid",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    session = get_session_store().create()
    response = RedirectResponse("/kiosk", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        OPERATOR_COOKIE,
        session.token,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )
    return response


@router.post("/operator/logout", include_in_schema=False)
async def operator_logout(request: Request) -> RedirectResponse:
    get_session_store().revoke(request.cookies.get(OPERATOR_COOKIE))
    response = RedirectResponse("/operator", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(OPERATOR_COOKIE, path="/")
    return response


@router.get("/kiosk", response_class=HTMLResponse, include_in_schema=False)
async def kiosk(request: Request) -> Response:
    session = get_session_store().get(request.cookies.get(OPERATOR_COOKIE))
    if session is None:
        return RedirectResponse("/operator", status_code=status.HTTP_303_SEE_OTHER)

    settings = get_settings()
    try:
        settings.require_secrets()
    except Exception as exc:  # noqa: BLE001 - ipinapakita ang setup na kulang
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return templates.TemplateResponse(
        request,
        "kiosk.html",
        {
            "csrf_token": session.csrf_token,
            "patterns": sorted(PATTERNS),
            "default_card_count": settings.default_card_count,
            "max_card_count": settings.max_card_count,
            "public_base_url": settings.public_base_url,
        },
    )
