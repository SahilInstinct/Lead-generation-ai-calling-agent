"""FastAPI application entry point for ApkaPG AI Outbound Calling & Lead Collection."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.calls import router as calls_router
from app.api.leads import router as leads_router
from app.api.ui import router as ui_router
from app.api.webhooks import router as webhooks_router
from app.config import Settings, get_settings
from app.services.dograh_service import DograhService
from app.services.vobiz_service import VobizService
from app.utils.logger import get_logger, setup_logging

logger = get_logger(__name__)


def _check_directory_writable(path) -> bool:
    """Verify that a storage directory exists and is writable."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        return os.access(path, os.W_OK)
    except OSError:
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize logging and verify local storage directories on startup."""
    settings: Settings = get_settings()
    setup_logging(level=settings.log_level, secrets=settings.get_secret_values())
    settings.ensure_data_dirs()

    logger.info(
        "Starting %s (env=%s, mock_providers=%s, vobiz_mode=%s)",
        settings.app_name,
        settings.app_env,
        settings.mock_providers,
        settings.vobiz_mode,
    )
    yield
    logger.info("Shutting down %s", settings.app_name)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    settings = get_settings()

    application = FastAPI(
        title=settings.app_name,
        description=(
            "Backend API for initiating AI outbound calls to PG owners via Dograh AI "
            "and Vobiz, collecting structured lead data locally in JSON and CSV."
        ),
        version="0.4.0",
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def request_logging_middleware(request: Request, call_next):
        start_time = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "%s %s -> %d (%.2f ms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    @application.get("/health", tags=["System"])
    async def health_check() -> JSONResponse:
        """Verify service health and local storage directory readiness."""
        current_settings = get_settings()
        storage_checks: dict[str, bool] = {
            "leads_dir": _check_directory_writable(current_settings.leads_dir),
            "calls_dir": _check_directory_writable(current_settings.calls_dir),
            "transcripts_dir": _check_directory_writable(
                current_settings.transcripts_dir
            ),
        }
        all_healthy = all(storage_checks.values())

        payload: dict[str, Any] = {
            "status": "healthy" if all_healthy else "degraded",
            "service": current_settings.app_name,
            "environment": current_settings.app_env,
            "mock_providers": current_settings.mock_providers,
            "storage": storage_checks,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        status_code = 200 if all_healthy else 503
        return JSONResponse(status_code=status_code, content=payload)

    @application.get("/api/system/status", tags=["System"])
    async def system_status() -> dict[str, Any]:
        """Report integration readiness for Dograh AI and Vobiz."""
        current_settings = get_settings()
        dograh_service = DograhService(current_settings)
        vobiz_service = VobizService(current_settings)
        dograh_health = await dograh_service.check_health()

        return {
            "mock_providers": current_settings.mock_providers,
            "dograh": {
                "base_url": current_settings.dograh_api_base_url,
                "api_key_configured": bool(current_settings.dograh_api_key),
                "workflow_uuid_configured": bool(current_settings.dograh_workflow_uuid),
                "use_draft_test": current_settings.dograh_use_draft_test,
                "health": dograh_health,
            },
            "vobiz": vobiz_service.get_status_summary(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # Register webhooks_router before calls_router so /api/calls/webhook
    # never matches /api/calls/{call_id} on GET requests.
    application.include_router(ui_router)
    application.include_router(webhooks_router)
    application.include_router(calls_router)
    application.include_router(leads_router)

    return application


app = create_app()