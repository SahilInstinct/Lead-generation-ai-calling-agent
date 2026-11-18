"""Built-in operator dashboard routes (localhost-only actions without exposing API keys)."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

from app.api.auth import get_calling_service
from app.api.calls import CallInitiateResponse
from app.models.call import CallInitiateRequest, CallRecord
from app.services.calling_service import (
    CallNotFoundError,
    CallingService,
    OptOutBlockedError,
)
from app.services.dograh_service import DograhError

router = APIRouter(tags=["UI"], include_in_schema=False)

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def require_local_dashboard_origin(request: Request) -> None:
    """Allow dashboard-triggered calls only from localhost; remote callers must use /api/calls/* with X-API-Key."""
    client_host = request.client.host if request.client else ""
    if client_host not in _LOCAL_HOSTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dashboard actions are restricted to localhost. Use /api/calls/* with X-API-Key.",
        )


@router.get("/")
async def operator_dashboard() -> FileResponse:
    """Serve the built-in ApkaPG calling & lead collection dashboard."""
    return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")


@router.post(
    "/ui/calls/initiate",
    response_model=CallInitiateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_local_dashboard_origin)],
)
async def ui_initiate_call(
    payload: CallInitiateRequest,
    service: CallingService = Depends(get_calling_service),
) -> CallInitiateResponse:
    """Initiate an outbound call from the local dashboard without client-side API keys."""
    try:
        record = await service.initiate_call(payload)
        return CallInitiateResponse(call=record)
    except OptOutBlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except DograhError as exc:
        upstream_status = exc.status_code if exc.status_code in (400, 401, 403, 404, 422) else 502
        raise HTTPException(
            status_code=upstream_status,
            detail=str(exc),
        ) from exc


@router.post(
    "/ui/calls/{call_id}/sync",
    response_model=CallRecord,
    dependencies=[Depends(require_local_dashboard_origin)],
)
async def ui_sync_call(
    call_id: str,
    service: CallingService = Depends(get_calling_service),
) -> CallRecord:
    """Sync call status from Dograh directly from the local dashboard."""
    try:
        return await service.sync_call_from_dograh(call_id)
    except CallNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DograhError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc