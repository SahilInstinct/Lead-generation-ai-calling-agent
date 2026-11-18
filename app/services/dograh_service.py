"""Client for interacting with the self-hosted Dograh AI REST API."""

from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


class DograhError(Exception):
    """Raised when Dograh API configuration is missing or a request fails."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class DograhCallTriggerResult:
    """Structured result returned when Dograh initiates an outbound call."""

    status: str
    workflow_run_id: str
    workflow_run_name: str | None = None
    raw_response: dict[str, Any] | None = None


class DograhService:
    """Async REST client for Dograh AI (outbound call triggers, run status, health)."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.base_url = settings.dograh_api_base_url.rstrip("/")
        self._transport = transport

    def _build_headers(self) -> dict[str, str]:
        if not self.settings.dograh_api_key:
            raise DograhError(
                "DOGRAH_API_KEY is not configured. Generate an API key in Dograh under Developers -> API Keys.",
                status_code=500,
            )
        return {
            "X-API-Key": self.settings.dograh_api_key,
            "Content-Type": "application/json",
        }

    def _client(self, timeout: float = 15.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            transport=self._transport,
        )

    async def check_health(self) -> dict[str, Any]:
        """Check Dograh backend reachability via GET /api/v1/health."""
        if self.settings.mock_providers:
            return {"reachable": True, "mode": "mock", "status": "mocked"}

        try:
            async with self._client(timeout=5.0) as client:
                response = await client.get("/api/v1/health")
                return {
                    "reachable": response.status_code == 200,
                    "status_code": response.status_code,
                    "body": response.json()
                    if response.headers.get("content-type", "").startswith("application/json")
                    else response.text[:200],
                }
        except httpx.HTTPError as exc:
            logger.warning("Dograh health check failed: %s", exc)
            return {"reachable": False, "error": str(exc)}

    async def list_workflows(self) -> list[dict[str, Any]]:
        """List available Dograh workflows (voice agents) via GET /api/v1/workflow/fetch."""
        headers = self._build_headers()
        try:
            async with self._client(timeout=10.0) as client:
                response = await client.get("/api/v1/workflow/fetch", headers=headers)
                if response.status_code >= 400:
                    raise DograhError(
                        f"Dograh workflow fetch failed ({response.status_code}): {response.text}",
                        status_code=response.status_code,
                    )
                data = response.json()
                return data if isinstance(data, list) else data.get("workflows", [])
        except httpx.HTTPError as exc:
            raise DograhError(f"Failed to connect to Dograh API: {exc}") from exc

    async def trigger_outbound_call(
        self,
        phone_number: str,
        initial_context: dict[str, Any],
        workflow_uuid: str | None = None,
    ) -> DograhCallTriggerResult:
        """Trigger an outbound call in Dograh (which dials via configured Vobiz telephony)."""
        target_uuid = workflow_uuid or self.settings.dograh_workflow_uuid
        if not target_uuid:
            raise DograhError(
                "DOGRAH_WORKFLOW_UUID is not configured. Set your ApkaPG agent UUID in .env.",
                status_code=500,
            )

        headers = self._build_headers()
        path = (
            f"/api/v1/public/agent/test/workflow/{target_uuid}"
            if self.settings.dograh_use_draft_test
            else f"/api/v1/public/agent/workflow/{target_uuid}"
        )

        payload: dict[str, Any] = {
            "phone_number": phone_number,
            "initial_context": initial_context,
        }
        if self.settings.dograh_telephony_config_id is not None:
            payload["telephony_configuration_id"] = self.settings.dograh_telephony_config_id

        try:
            async with self._client(timeout=20.0) as client:
                response = await client.post(path, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise DograhError(f"Failed to reach Dograh API at {self.base_url}: {exc}") from exc

        if response.status_code >= 400:
            raise DograhError(
                f"Dograh rejected outbound call ({response.status_code}): {response.text}",
                status_code=response.status_code,
            )

        data = response.json()
        run_id = data.get("workflow_run_id")
        if run_id is None:
            raise DograhError(
                f"Dograh response missing workflow_run_id: {data}",
                status_code=502,
            )

        return DograhCallTriggerResult(
            status=str(data.get("status", "initiated")),
            workflow_run_id=str(run_id),
            workflow_run_name=data.get("workflow_run_name"),
            raw_response=data,
        )

    async def get_workflow_run(
        self,
        workflow_id: int,
        run_id: str,
    ) -> dict[str, Any]:
        """Fetch details of a specific run via GET /api/v1/workflow/{workflow_id}/runs/{run_id}."""
        headers = self._build_headers()
        path = f"/api/v1/workflow/{workflow_id}/runs/{run_id}"
        try:
            async with self._client(timeout=10.0) as client:
                response = await client.get(path, headers=headers)
                if response.status_code >= 400:
                    raise DograhError(
                        f"Dograh run fetch failed ({response.status_code}): {response.text}",
                        status_code=response.status_code,
                    )
                return response.json()
        except httpx.HTTPError as exc:
            raise DograhError(f"Failed to fetch run from Dograh: {exc}") from exc