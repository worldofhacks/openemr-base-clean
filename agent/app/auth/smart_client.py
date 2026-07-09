"""SMART-on-FHIR authorization_code + PKCE(S256) client (ARCHITECTURE.md §4, §5a, D2, D9).

The agent is an external OAuth2/SMART client of OpenEMR (D2). It acts strictly *as*
the launching clinician using a delegated `authorization_code` + PKCE(S256) token
(D9) — it NEVER negotiates `client_credentials` (which would attribute access to the
synthetic `oe-system` user, F-S.5) and NEVER sends OpenEMR's same-session
`APICSRFTOKEN` local-API shortcut (F-S.3). This module owns only the token exchange
and the authorize-URL construction; the interactive browser login/consent is driven
by the test harness (Selenium), never by this runtime code.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

# The ONLY grant type the agent is permitted to use (F-S.5 / D9).
DELEGATED_GRANT_TYPE = "authorization_code"


class SmartAuthError(Exception):
    """A SMART/OAuth exchange failed for a reason the caller cannot recover from."""


class CoPilotNotEnabledError(SmartAuthError):
    """The OAuth client is disabled/unrecognized (D14: user-scoped apps register
    disabled until an admin enables them). Surfaced explicitly, never as a hang (§6)."""


def forbid_nondelegated_grant(grant_type: str) -> None:
    """Guardrail (F-S.5): refuse any grant other than delegated authorization_code."""
    if grant_type != DELEGATED_GRANT_TYPE:
        raise SmartAuthError(
            f"refusing non-delegated grant '{grant_type}': the agent must act as the "
            f"clinician via {DELEGATED_GRANT_TYPE} (F-S.5)"
        )


def generate_pkce() -> tuple[str, str, str]:
    """Return (code_verifier, code_challenge, method) for PKCE S256 (RFC 7636)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge, "S256"


class TokenResponse(BaseModel):
    """A delegated access token + its launch context. The token itself never leaks
    via repr (SecretStr)."""

    model_config = ConfigDict(frozen=True)

    access_token: SecretStr
    token_type: str = "Bearer"
    expires_in: int | None = None
    scope: str = ""
    patient: str | None = None  # SMART launch/patient context, when present
    refresh_token: SecretStr | None = None

    @property
    def scopes(self) -> list[str]:
        return self.scope.split()

    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"{self.token_type} {self.access_token.get_secret_value()}"}


class SmartClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        authorize_endpoint: str,
        token_endpoint: str,
        fhir_base_url: str,
        redirect_uri: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._authorize_endpoint = authorize_endpoint
        self._token_endpoint = token_endpoint
        self._fhir_base_url = str(fhir_base_url).rstrip("/")
        self._redirect_uri = redirect_uri
        self._http = http_client  # injected in tests; created per-call otherwise

    def build_authorize_url(
        self, *, state: str, code_challenge: str, scope: str, launch: str | None = None
    ) -> str:
        """Build the SMART authorization request URL (browser redirect target)."""
        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "scope": scope,
            "state": state,
            "aud": self._fhir_base_url,  # SMART requires aud = FHIR base
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if launch is not None:
            params["launch"] = launch
            # EHR launch requires the `launch` scope to receive patient context.
            if "launch" not in scope.split():
                params["scope"] = f"launch {scope}"
        return f"{self._authorize_endpoint}?{urlencode(params)}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> TokenResponse:
        """Exchange an authorization code for a delegated access token (PKCE-completed).
        Confidential client: client_secret via client_secret_post. Never client_credentials
        (F-S.5); never APICSRFTOKEN (F-S.3 — a bearer exchange, no local-API header)."""
        forbid_nondelegated_grant(DELEGATED_GRANT_TYPE)
        data = {
            "grant_type": DELEGATED_GRANT_TYPE,
            "code": code,
            "redirect_uri": self._redirect_uri,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "code_verifier": code_verifier,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        resp = await self._post_token(data, headers)
        return self._parse_token_response(resp)

    async def _post_token(self, data: dict, headers: dict) -> httpx.Response:
        if self._http is not None:
            return await self._http.post(self._token_endpoint, data=data, headers=headers)
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.post(self._token_endpoint, data=data, headers=headers)

    def _parse_token_response(self, resp: httpx.Response) -> TokenResponse:
        if resp.status_code == 401:
            raise CoPilotNotEnabledError(
                "OAuth client rejected (invalid_client) — the SMART app is likely "
                "disabled; enable it in Administration → API Clients (D14)"
            )
        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise SmartAuthError(f"token endpoint returned non-JSON (HTTP {resp.status_code})") from exc
        if resp.status_code != 200 or "access_token" not in payload:
            # Never surface the raw error to a user; describe the failed operation (§ error handling).
            raise SmartAuthError(f"token exchange failed (HTTP {resp.status_code})")
        return TokenResponse(**{k: payload[k] for k in TokenResponse.model_fields if k in payload})
