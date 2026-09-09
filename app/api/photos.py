"""Ang upload page para sa dalawang litrato ng party.

Host-only ito. Nagsusulat ito sa `app/web/static/img/`, kaya kung bukas ito sa
kahit sino ay puwedeng magpalit ng litrato ang isang bisita sa gitna ng party —
o mag-upload ng kung anong hindi bagay sa kaarawan ng bata.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.deps import templates
from app.api.security import (
    OPERATOR_COOKIE,
    enforce_rate_limit,
    get_session_store,
    require_operator,
)
from app.config import get_settings
from app.services import photos

router = APIRouter()


@router.get("/operator/photos", response_class=HTMLResponse, include_in_schema=False)
async def photos_page(request: Request) -> Response:
    session = get_session_store().get(request.cookies.get(OPERATOR_COOKIE))
    if session is None:
        return RedirectResponse("/operator", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(
        request,
        "operator_photos.html",
        {
            "csrf_token": session.csrf_token,
            "photos": photos.saved_photos(),
            "max_mb": photos.MAX_UPLOAD_BYTES // (1024 * 1024),
            "output_edge": photos.OUTPUT_EDGE,
        },
    )


@router.post(
    "/api/photos/{slot}",
    dependencies=[Depends(require_operator)],
    include_in_schema=False,
)
async def upload_photo(
    request: Request,
    slot: photos.PhotoSlot,
    photo: Annotated[UploadFile, File()],
) -> dict[str, object]:
    settings = get_settings()
    enforce_rate_limit(
        request,
        bucket="photo_upload",
        limit=settings.photo_upload_rate_limit_per_minute,
    )

    # Hangganan sa pagbasa mismo. Kung hihintayin pa nating matapos ang buong
    # body bago i-check ang sukat, ang malaking file na ang nagpasya para sa atin.
    raw = await photo.read(photos.MAX_UPLOAD_BYTES + 1)
    # Ang decode at ang pagsulat ay blocking, at hindi ito ipinapasa sa thread.
    # Sinasadya: isang host lang ang tumatawag dito, isang beses bago magsimula
    # ang party, at mga daang millisecond lang ito. Kung magiging maramihan ang
    # upload, dito ang unang ilalagay na `run_in_threadpool`.
    try:
        info = photos.store_photo(slot, raw)
    except photos.PhotoTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc
    except photos.NotAnImage as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return {
        "slot": info.slot,
        "url": info.url,
        "byte_size": info.byte_size,
        "version": info.version,
    }


@router.delete(
    "/api/photos/{slot}",
    dependencies=[Depends(require_operator)],
    include_in_schema=False,
)
async def remove_photo(slot: photos.PhotoSlot) -> dict[str, object]:
    return {"slot": slot, "removed": photos.delete_photo(slot)}
