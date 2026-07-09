"""Thin async FHIR client (ARCHITECTURE.md §2, D9, D10-rev, F-S.9).

Reads only, with the clinician's delegated Bearer token. Pins https:// and rejects a
downgrade (F-S.9 — TLS is edge-only on the deployment). Attaches the correlation id as
`X-Copilot-Request-Id` on every outbound call so the request threads into the agent's
own trace (D10-rev). A call failure/timeout raises `FhirCallError`; the tool layer
catches it and turns it into a FAILED ToolResult (never a silent omission, §6/F3).
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from app.middleware.correlation import outbound_headers


class FhirCallError(Exception):
    """A FHIR read failed (network, timeout, or non-2xx). Carries a short reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class FhirClient:
    def __init__(
        self,
        *,
        base_url: str,
        access_token: str,
        http_client: httpx.AsyncClient | None = None,
        per_call_timeout: float = 8.0,
    ) -> None:
        if urlsplit(base_url).scheme != "https":
            raise ValueError("FHIR base URL must be https:// (F-S.9: no plaintext downgrade)")
        self._base = base_url.rstrip("/")
        self._token = access_token
        self._http = http_client
        self._timeout = per_call_timeout

    async def search(self, resource_type: str, params: dict) -> dict:
        """GET {base}/{ResourceType}?{params} → the FHIR Bundle (dict)."""
        url = f"{self._base}/{resource_type}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/fhir+json",
            **outbound_headers(),  # X-Copilot-Request-Id (D10-rev)
        }
        try:
            resp = await self._get(url, params, headers)
        except httpx.TimeoutException as exc:
            raise FhirCallError("timeout") from exc
        except httpx.HTTPError as exc:
            raise FhirCallError(type(exc).__name__) from exc
        if resp.status_code != 200:
            raise FhirCallError(f"HTTP {resp.status_code}")
        try:
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            raise FhirCallError("non-JSON response") from exc

    async def _get(self, url: str, params: dict, headers: dict) -> httpx.Response:
        if self._http is not None:
            return await self._http.get(url, params=params, headers=headers, timeout=self._timeout)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.get(url, params=params, headers=headers)
