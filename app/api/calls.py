"""Outbound calling API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.auth import get_calling_service, require_api_key
from app.models.call import CallInitiateRequest, CallRecord
from app.services.calling_service import (
    CallNotFoundError,
    CallingService,
    OptOutBlockedError,
)
from app.services.dograh_service import DograhError

router = APIRouter(prefix="/api/calls", tags=["Calling"])


class CallInitiateResponse(BaseModel):
    """Response returned after initiating an outbound call."""

    call: CallRecord


class CallListResponse(BaseModel):
    """Response returned when listing calls."""

    count: int
    calls: list[CallRecord]


@router.post(
    "/initiate",
    response_model=CallInitiateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_key)],
)
async def initiate_outbound_call(
    payload: CallInitiateRequest,
    service: CallingService = Depends(get_calling_service),
) -> CallInitiateResponse:
    """Initiate an outbound call via Dograh AI (using configured Vobiz telephony)."""
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


@router.get("", response_model=CallListResponse)
async def list_calls(
    service: CallingService = Depends(get_calling_service),
) -> CallListResponse:
    """List all recorded outbound calls."""
    calls = service.list_calls()
    return CallListResponse(count=len(calls), calls=calls)


@router.get("/{call_id}", response_model=CallRecord)
async def get_call(
    call_id: str,
    service: CallingService = Depends(get_calling_service),
) -> CallRecord:
    """Retrieve a single call record by call_id."""
    try:
        return service.get_call(call_id)
    except CallNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/{call_id}/sync",
    response_model=CallRecord,
    dependencies=[Depends(require_api_key)],
)
async def sync_call_status(
    call_id: str,
    service: CallingService = Depends(get_calling_service),
) -> CallRecord:
    """Manually sync the latest run status for a call from Dograh AI."""
    try:
        return await service.sync_call_from_dograh(call_id)
    except CallNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DograhError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc