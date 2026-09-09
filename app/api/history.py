"""Game history: mga nakaraang round at ang naging resulta."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import templates
from app.api.security import OPERATOR_COOKIE, get_session_store
from app.db.session import get_db
from app.services import history

router = APIRouter()


def _require_host(request: Request) -> RedirectResponse | None:
    """Ang history ay para sa host. Ang bisita ay may sariling board."""
    if get_session_store().get(request.cookies.get(OPERATOR_COOKIE)) is None:
        return RedirectResponse("/operator", status_code=status.HTTP_303_SEE_OTHER)
    return None


@router.get("/history", response_class=HTMLResponse, include_in_schema=False)
async def history_list(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    redirect = _require_host(request)
    if redirect is not None:
        return redirect

    rounds = await history.recent_rounds(db)
    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "rounds": rounds,
            "outcome_line": history.outcome_line,
        },
    )


@router.get("/history/{round_id}", response_class=HTMLResponse, include_in_schema=False)
async def history_detail(
    request: Request,
    round_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    redirect = _require_host(request)
    if redirect is not None:
        return redirect

    detail = await history.round_detail(db, round_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That round does not exist."
        )

    return templates.TemplateResponse(
        request,
        "history_detail.html",
        {
            "detail": detail,
            "outcome_line": history.outcome_line,
            "reason_label": history.reason_label,
        },
    )
