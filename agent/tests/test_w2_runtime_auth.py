"""W2 launches use and attest the replacement delegated client (W2-D1/D9; §3)."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.auth.scopes import ScopeCoverageError, W2_REQUESTED_SCOPES
from app.auth.smart_client import TokenResponse
from app.service import AgentServices


class _Smart:
    def __init__(self, token: TokenResponse | None = None) -> None:
        self.scope: str | None = None
        self.token = token

    def build_authorize_url(self, *, scope: str, **_kwargs) -> str:
        self.scope = scope
        return "https://openemr.test/authorize"

    async def exchange_code(self, **_kwargs) -> TokenResponse:
        assert self.token is not None
        return self.token


class _Sessions:
    def __init__(self) -> None:
        self.created = 0

    async def create(self, **_kwargs):
        self.created += 1
        return type("Session", (), {"session_id": "session-synthetic"})()


def _services(*, enabled: bool, token: TokenResponse | None = None) -> AgentServices:
    services = object.__new__(AgentServices)
    services.settings = type(
        "Settings",
        (),
        {
            "w2_document_runtime_enabled": enabled,
            "token_lifetime_seconds": 3600,
        },
    )()
    services.smart = _Smart(token)
    services.sessions = _Sessions()
    services._pkce = {}
    services._tokens = {}
    return services


def _jwt_with_scopes(scopes: set[str]) -> str:
    def encode(value: object) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{encode({'alg': 'RS256'})}.{encode({'scopes': sorted(scopes)})}.signature"


def test_enabled_document_runtime_launches_with_exact_w2_manifest() -> None:
    services = _services(enabled=True)

    services.begin_launch()

    assert set((services.smart.scope or "").split()) == W2_REQUESTED_SCOPES


@pytest.mark.asyncio
async def test_callback_rejects_partial_w2_grant_before_session_creation() -> None:
    token = TokenResponse(
        access_token="synthetic-token",
        scope=" ".join(sorted(W2_REQUESTED_SCOPES - {"user/vital.crus"})),
        patient="patient-synthetic",
    )
    services = _services(enabled=True, token=token)
    services._pkce["state-synthetic"] = "verifier-synthetic"

    with pytest.raises(ScopeCoverageError, match="user/vital.crus"):
        await services.complete_callback(code="code-synthetic", state="state-synthetic")

    assert services.sessions.created == 0


@pytest.mark.asyncio
async def test_callback_rejects_unexpected_w2_grant_before_session_creation() -> None:
    token = TokenResponse(
        access_token="synthetic-token",
        scope=" ".join(sorted(W2_REQUESTED_SCOPES | {"user/Observation.write"})),
        patient="patient-synthetic",
    )
    services = _services(enabled=True, token=token)
    services._pkce["state-synthetic"] = "verifier-synthetic"

    with pytest.raises(ScopeCoverageError, match="Unexpected.*Observation.write"):
        await services.complete_callback(code="code-synthetic", state="state-synthetic")

    assert services.sessions.created == 0


@pytest.mark.asyncio
async def test_callback_accepts_complete_w2_grant() -> None:
    token = TokenResponse(
        access_token="synthetic-token",
        scope=" ".join(sorted(W2_REQUESTED_SCOPES)),
        patient="patient-synthetic",
        clinician_sub="Practitioner/synthetic",
    )
    services = _services(enabled=True, token=token)
    services._pkce["state-synthetic"] = "verifier-synthetic"
    # Keep the time helper executable on the object-new service.
    services._token_deadline = lambda: datetime.now(timezone.utc) + timedelta(hours=1)

    await services.complete_callback(code="code-synthetic", state="state-synthetic")

    assert services.sessions.created == 1
    assert set(services._tokens["session-synthetic"].scopes) == W2_REQUESTED_SCOPES


@pytest.mark.asyncio
async def test_callback_attests_api_scope_from_openemr_bearer_claim() -> None:
    # OpenEMR deliberately omits api:* scopes from the token response's display field,
    # while retaining them in the bearer JWT that its APIs actually authorize.
    token = TokenResponse(
        access_token=_jwt_with_scopes(set(W2_REQUESTED_SCOPES)),
        scope=" ".join(sorted(W2_REQUESTED_SCOPES - {"api:oemr"})),
        patient="patient-synthetic",
        clinician_sub="Practitioner/synthetic",
    )
    services = _services(enabled=True, token=token)
    services._pkce["state-synthetic"] = "verifier-synthetic"
    services._token_deadline = lambda: datetime.now(timezone.utc) + timedelta(hours=1)

    await services.complete_callback(code="code-synthetic", state="state-synthetic")

    assert services.sessions.created == 1
    assert set(services._tokens["session-synthetic"].scopes) == W2_REQUESTED_SCOPES


@pytest.mark.asyncio
async def test_callback_rejects_api_scope_missing_from_openemr_bearer_claim() -> None:
    granted = set(W2_REQUESTED_SCOPES - {"api:oemr"})
    token = TokenResponse(
        access_token=_jwt_with_scopes(granted),
        scope=" ".join(sorted(granted)),
        patient="patient-synthetic",
    )
    services = _services(enabled=True, token=token)
    services._pkce["state-synthetic"] = "verifier-synthetic"

    with pytest.raises(ScopeCoverageError, match="api:oemr"):
        await services.complete_callback(code="code-synthetic", state="state-synthetic")

    assert services.sessions.created == 0


@pytest.mark.asyncio
async def test_callback_rejects_malformed_bearer_scope_attestation() -> None:
    token = TokenResponse(
        access_token="header.not-base64.signature",
        scope=" ".join(sorted(W2_REQUESTED_SCOPES)),
        patient="patient-synthetic",
    )
    services = _services(enabled=True, token=token)
    services._pkce["state-synthetic"] = "verifier-synthetic"

    with pytest.raises(ScopeCoverageError, match="bearer scope attestation"):
        await services.complete_callback(code="code-synthetic", state="state-synthetic")

    assert services.sessions.created == 0


@pytest.mark.asyncio
async def test_callback_rejects_bearer_and_response_non_api_scope_disagreement() -> None:
    token = TokenResponse(
        access_token=_jwt_with_scopes(set(W2_REQUESTED_SCOPES)),
        scope=" ".join(
            sorted(
                W2_REQUESTED_SCOPES
                - {"api:oemr", "user/MedicationRequest.read"}
            )
        ),
        patient="patient-synthetic",
    )
    services = _services(enabled=True, token=token)
    services._pkce["state-synthetic"] = "verifier-synthetic"

    with pytest.raises(ScopeCoverageError, match="bearer scope attestation"):
        await services.complete_callback(code="code-synthetic", state="state-synthetic")

    assert services.sessions.created == 0
