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
import binascii
import hashlib
import json
import secrets
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr

# Interactive exchange uses authorization_code. Background work may use only the
# corresponding delegated refresh_token grant; client_credentials is never accepted.
DELEGATED_GRANT_TYPE = "authorization_code"
DELEGATED_REFRESH_GRANT_TYPE = "refresh_token"


class SmartAuthError(Exception):
    """A SMART/OAuth exchange failed for a reason the caller cannot recover from."""


class CoPilotNotEnabledError(SmartAuthError):
    """The OAuth client is disabled/unrecognized (D14: user-scoped apps register
    disabled until an admin enables them). Surfaced explicitly, never as a hang (§6)."""


class DelegatedRefreshExpired(SmartAuthError):
    """The user's refresh token expired/revoked; a fresh SMART launch is required."""


class SmartAuthUnavailable(SmartAuthError):
    """The token endpoint could not complete a delegated refresh right now."""


def forbid_nondelegated_grant(grant_type: str) -> None:
    """Guardrail (F-S.5): refuse any grant other than delegated authorization_code."""
    if grant_type != DELEGATED_GRANT_TYPE:
        raise SmartAuthError(
            f"refusing non-delegated grant '{grant_type}': the agent must act as the "
            f"clinician via {DELEGATED_GRANT_TYPE} (F-S.5)"
        )


def _clinician_sub_from_id_token(id_token: str | None) -> str | None:
    """Decode the id_token JWT PAYLOAD (no signature check — it is our own freshly-exchanged
    token) and return the launching clinician's identity: `fhirUser` preferred, else `sub`.

    Defensive by design (fail-closed): any malformed JWT — wrong segment count, bad base64,
    non-JSON payload, non-object payload — yields None rather than raising, so a token exchange
    is never derailed by an unexpected id_token shape (§6)."""
    if not id_token or not isinstance(id_token, str):
        return None
    segments = id_token.split(".")
    if len(segments) < 2:
        return None
    seg = segments[1]
    try:
        padded = seg + "=" * (-len(seg) % 4)  # restore base64url padding
        raw = base64.urlsafe_b64decode(padded)
        payload = json.loads(raw)
    except (binascii.Error, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    fhir_user = payload.get("fhirUser")
    if isinstance(fhir_user, str) and fhir_user:
        return fhir_user
    sub = payload.get("sub")
    if isinstance(sub, str) and sub:
        return sub
    return None


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
    refresh_expires_in: int | None = None
    scope: str = ""
    patient: str | None = None  # SMART launch/patient context, when present
    refresh_token: SecretStr | None = None
    # The launching clinician's identity, decoded from the id_token (D9/D5 provider attribution):
    # fhirUser when present, else sub. None when the response carried no (decodable) id_token.
    clinician_sub: str | None = None

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

    async def refresh_token(self, *, refresh_token: SecretStr) -> TokenResponse:
        """Refresh the same clinician delegation; never substitute a service principal.

        The request has no path for ``client_credentials`` and failures deliberately omit
        both the refresh value and the token endpoint response body.
        """
        data = {
            "grant_type": DELEGATED_REFRESH_GRANT_TYPE,
            "refresh_token": refresh_token.get_secret_value(),
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        try:
            resp = await self._post_token(data, headers)
        except httpx.HTTPError as exc:
            raise SmartAuthUnavailable("delegated token refresh unavailable") from exc
        if resp.status_code in {400, 401}:
            raise DelegatedRefreshExpired(
                "delegated refresh expired; clinician reauthorization required"
            )
        if resp.status_code != 200:
            raise SmartAuthUnavailable(
                f"delegated token refresh unavailable (HTTP {resp.status_code})"
            )
        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001 - response bodies never cross this seam
            raise SmartAuthUnavailable(
                "delegated token refresh returned an invalid response"
            ) from exc
        if not isinstance(payload, dict) or "access_token" not in payload:
            raise SmartAuthUnavailable(
                "delegated token refresh returned an invalid response"
            )
        fields = {k: payload[k] for k in TokenResponse.model_fields if k in payload}
        fields["clinician_sub"] = _clinician_sub_from_id_token(payload.get("id_token"))
        return TokenResponse(**fields)

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
        fields = {k: payload[k] for k in TokenResponse.model_fields if k in payload}
        # id_token is NOT a TokenResponse field — decode it separately (D9/D5): the clinician's
        # identity is fhirUser (preferred) else sub, or None when there is no decodable id_token.
        fields["clinician_sub"] = _clinician_sub_from_id_token(payload.get("id_token"))
        return TokenResponse(**fields)
