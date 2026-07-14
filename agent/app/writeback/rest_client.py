"""Narrow delegated-token OpenEMR write client (W2-D1/D9/D10).

Only document creates and vital creates are exposed. There is intentionally no
update or delete method. Caller-supplied attribution is removed; the bearer token's
delegated principal is the sole transport identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from app.middleware.correlation import outbound_headers

_ATTRIBUTION_KEYS = frozenset({"user", "group", "author", "provider", "performer"})


def strip_caller_attribution(payload: Mapping[str, object]) -> dict[str, object]:
    """Make a clean payload without spoofable performer-like request fields."""

    return {
        key: value
        for key, value in payload.items()
        if key.casefold() not in _ATTRIBUTION_KEYS
    }


@dataclass(frozen=True)
class DelegatedPrincipal:
    clinician_sub: str
    patient_id: str
    access_token: SecretStr


class OpenEMRWriteError(Exception):
    def __init__(self, reason: str, *, ambiguous: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.ambiguous = ambiguous


class OpenEMRRestClient:
    """Append-only standard-REST transport under one bound delegated principal."""

    def __init__(
        self,
        *,
        base_url: str,
        principal: DelegatedPrincipal,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
    ) -> None:
        if urlsplit(base_url).scheme != "https":
            raise ValueError("OpenEMR write base URL must use https")
        self._base = base_url.rstrip("/")
        self._principal = principal
        self._http = http_client
        self._timeout = timeout

    def _authorize_patient(self, patient_id: str) -> None:
        if patient_id != self._principal.patient_id:
            raise OpenEMRWriteError("delegated principal is bound to a different patient")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._principal.access_token.get_secret_value()}",
            "Accept": "application/json",
            **outbound_headers(),
        }

    async def create_document(
        self,
        *,
        patient_id: str,
        category_path: str,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> str | None:
        self._authorize_patient(patient_id)
        response = await self._request(
            "POST",
            f"{self._base}/api/patient/{patient_id}/document",
            headers=self._headers(),
            data={"category": category_path},
            files={"file": (filename, content, content_type)},
        )
        # This fork returns JSON true and no id; discovery by marker/hash follows.
        body = response.json()
        if body is True:
            return None
        if isinstance(body, dict):
            value = body.get("id") or body.get("document_id")
            return str(value) if value else None
        return None

    async def create_vital(
        self,
        *,
        patient_id: str,
        encounter_id: str,
        payload: Mapping[str, object],
    ) -> str | None:
        self._authorize_patient(patient_id)
        clean = strip_caller_attribution(payload)
        response = await self._request(
            "POST",
            f"{self._base}/api/patient/{patient_id}/encounter/{encounter_id}/vital",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=clean,
        )
        body = response.json()
        if isinstance(body, dict):
            value = body.get("id") or body.get("vital_id")
            return str(value) if value else None
        return None

    async def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        try:
            if self._http is not None:
                response = await self._http.request(
                    method, url, timeout=self._timeout, **kwargs
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            raise OpenEMRWriteError("timeout after request dispatch", ambiguous=True) from exc
        except httpx.HTTPError as exc:
            raise OpenEMRWriteError(type(exc).__name__, ambiguous=True) from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise OpenEMRWriteError(f"HTTP {response.status_code}")
        return response
