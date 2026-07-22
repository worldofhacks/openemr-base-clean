"""W2 launches use and attest the replacement delegated client (W2-D1/D9; §3)."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import SecretStr

from app.auth.job_credentials import DelegatedSessionCredential
from app.auth.scopes import ScopeCoverageError, W2_REQUESTED_SCOPES
from app.auth.smart_client import TokenResponse
from app.service import AgentServices
from app.session.store import InMemorySessionStore, SessionExpiredError


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
        self.last_create: dict[str, object] | None = None

    async def create(self, **kwargs):
        self.created += 1
        self.last_create = kwargs
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


def test_week2_launch_destination_is_bound_only_in_server_side_oauth_state() -> None:
    services = _services(enabled=True)

    services.begin_launch(destination="week2")

    assert len(services._pkce) == 1
    pending = next(iter(services._pkce.values()))
    assert getattr(pending, "destination", None) == "week2"
    assert getattr(pending, "verifier", None)


def test_week2_launch_fails_closed_when_document_runtime_is_disabled() -> None:
    services = _services(enabled=False)

    with pytest.raises(RuntimeError, match="document runtime is disabled"):
        services.begin_launch(destination="week2")

    assert services._pkce == {}


@pytest.mark.asyncio
async def test_abandoned_oauth_state_expires_before_callback_exchange() -> None:
    services = _services(enabled=True)
    now = 1_000.0
    services._launch_clock = lambda: now
    services.begin_launch(destination="week2")
    state = next(iter(services._pkce))

    now += 301.0
    with pytest.raises(ValueError, match="unknown or replayed OAuth state"):
        await services.complete_callback_with_destination(
            code="code-synthetic", state=state
        )

    assert state not in services._pkce


def test_pending_oauth_state_store_evicts_oldest_entry_at_hard_cap() -> None:
    services = _services(enabled=True)
    services._pkce = {f"old-{index}": "legacy-verifier" for index in range(256)}

    services.begin_launch(destination="week2")

    assert len(services._pkce) == 256
    assert "old-0" not in services._pkce


def test_launch_rate_limit_bounds_one_serving_instance() -> None:
    from app.service import LaunchRateLimited

    services = _services(enabled=True)
    services._launch_clock = lambda: 1_000.0
    for _ in range(60):
        services.begin_launch(destination="week2")

    with pytest.raises(LaunchRateLimited):
        services.begin_launch(destination="week2")


@pytest.mark.asyncio
async def test_callback_consumes_the_server_bound_week2_destination_once() -> None:
    token = TokenResponse(
        access_token="synthetic-token",
        scope=" ".join(sorted(W2_REQUESTED_SCOPES)),
        patient="patient-synthetic",
        clinician_sub="Practitioner/synthetic",
    )
    services = _services(enabled=True, token=token)
    services._token_deadline = lambda: datetime.now(timezone.utc) + timedelta(hours=1)
    services.begin_launch(destination="week2")
    state = next(iter(services._pkce))

    _session, destination = await services.complete_callback_with_destination(
        code="code-synthetic", state=state
    )

    assert destination == "week2"
    with pytest.raises(ValueError, match="unknown or replayed OAuth state"):
        await services.complete_callback_with_destination(
            code="code-synthetic", state=state
        )


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
async def test_callback_persists_optional_smart_encounter_with_patient_pin() -> None:
    token = TokenResponse(
        access_token="synthetic-token",
        scope=" ".join(sorted(W2_REQUESTED_SCOPES)),
        patient="patient-synthetic",
        encounter="encounter-synthetic",
        clinician_sub="Practitioner/synthetic",
    )
    services = _services(enabled=True, token=token)
    services._pkce["state-synthetic"] = "verifier-synthetic"
    services._token_deadline = lambda: datetime.now(timezone.utc) + timedelta(hours=1)

    await services.complete_callback(code="code-synthetic", state="state-synthetic")

    assert services.sessions.last_create is not None
    assert services.sessions.last_create["patient_id"] == "patient-synthetic"
    assert services.sessions.last_create["encounter_id"] == "encounter-synthetic"


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
async def test_callback_rejects_bearer_and_response_non_api_scope_disagreement() -> (
    None
):
    token = TokenResponse(
        access_token=_jwt_with_scopes(set(W2_REQUESTED_SCOPES)),
        scope=" ".join(
            sorted(W2_REQUESTED_SCOPES - {"api:oemr", "user/MedicationRequest.read"})
        ),
        patient="patient-synthetic",
    )
    services = _services(enabled=True, token=token)
    services._pkce["state-synthetic"] = "verifier-synthetic"

    with pytest.raises(ScopeCoverageError, match="bearer scope attestation"):
        await services.complete_callback(code="code-synthetic", state="state-synthetic")

    assert services.sessions.created == 0


@pytest.mark.asyncio
async def test_expired_w2_access_token_rotates_behind_the_same_session_id() -> None:
    now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    sessions = InMemorySessionStore(now=lambda: now, idle_timeout_s=1800)
    session = await sessions.create(
        clinician_sub="Practitioner/synthetic",
        patient_id="patient-synthetic",
        token_expires_at=now + timedelta(seconds=1),
    )
    now += timedelta(minutes=2)

    class Vault:
        calls = 0

        async def credential_for_session(self, resolved):
            self.calls += 1
            assert resolved.session_id == session.session_id
            return DelegatedSessionCredential(
                access_token=SecretStr("rotated-synthetic-token"),
                token_type="Bearer",
                scope=" ".join(sorted(W2_REQUESTED_SCOPES)),
                patient_id="patient-synthetic",
                clinician_sub="Practitioner/synthetic",
                access_expires_at=now + timedelta(hours=1),
            )

    vault = Vault()
    services = object.__new__(AgentServices)
    services.sessions = sessions
    services.document_runtime = type("Runtime", (), {"credential_vault": vault})()
    services._tokens = {
        session.session_id: TokenResponse(access_token="expired-synthetic-token")
    }
    services._token_deadline = lambda: now + timedelta(hours=1)

    resolved = await services.resolve_session(session.session_id)

    assert resolved.session_id == session.session_id
    assert resolved.patient_id == "patient-synthetic"
    assert vault.calls == 1
    assert (
        services._tokens[session.session_id].access_token.get_secret_value()
        == "rotated-synthetic-token"
    )
    assert await sessions.get(session.session_id) is resolved


@pytest.mark.asyncio
async def test_w2_session_rehydrates_foreground_token_after_process_restart() -> None:
    now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    sessions = InMemorySessionStore(now=lambda: now)
    session = await sessions.create(
        clinician_sub="Practitioner/synthetic",
        patient_id="patient-synthetic",
        token_expires_at=now + timedelta(minutes=30),
    )

    class Vault:
        async def credential_for_session(self, _resolved):
            return DelegatedSessionCredential(
                access_token=SecretStr("rehydrated-synthetic-token"),
                token_type="Bearer",
                scope=" ".join(sorted(W2_REQUESTED_SCOPES)),
                patient_id="patient-synthetic",
                clinician_sub="Practitioner/synthetic",
                access_expires_at=now + timedelta(minutes=30),
            )

    services = object.__new__(AgentServices)
    services.sessions = sessions
    services.document_runtime = type("Runtime", (), {"credential_vault": Vault()})()
    services._tokens = {}
    services._token_deadline = lambda: now + timedelta(hours=1)

    resolved = await services.resolve_session(session.session_id)

    assert resolved.session_id == session.session_id
    assert (
        services._tokens[session.session_id].access_token.get_secret_value()
        == "rehydrated-synthetic-token"
    )


@pytest.mark.asyncio
async def test_w2_refresh_never_reopens_an_idle_session() -> None:
    now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    sessions = InMemorySessionStore(now=lambda: now, idle_timeout_s=60)
    session = await sessions.create(
        clinician_sub="Practitioner/synthetic",
        patient_id="patient-synthetic",
        token_expires_at=now + timedelta(seconds=1),
    )
    now += timedelta(minutes=2)

    class Vault:
        async def credential_for_session(self, _resolved):
            raise AssertionError("an idle session must not reach the credential vault")

    services = object.__new__(AgentServices)
    services.sessions = sessions
    services.document_runtime = type("Runtime", (), {"credential_vault": Vault()})()
    services._tokens = {}

    with pytest.raises(SessionExpiredError):
        await services.resolve_session(session.session_id)
