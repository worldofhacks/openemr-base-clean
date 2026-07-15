"""Encrypted delegated-job credential contract (W2-D1/D9/D10; §3/§3a)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.auth.job_credentials import (
    CredentialCipher,
    InMemoryJobCredentialRepository,
    JobCredentialAuthExpired,
    JobCredentialBindingError,
    JobCredentialUnavailable,
    JobCredentialVault,
    PostgresJobCredentialRepository,
)
from app.auth.smart_client import (
    DelegatedRefreshExpired,
    SmartAuthUnavailable,
    SmartClient,
    TokenResponse,
)
from app.session.store import Session


NOW = datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc)
ACCESS = "synthetic-access-token-value"
REFRESH = "synthetic-refresh-token-value"


def _session(*, patient_id: str = "synthetic-patient") -> Session:
    return Session(
        session_id="synthetic-session",
        clinician_sub="Practitioner/synthetic-clinician",
        patient_id=patient_id,
        created_at=NOW,
        last_activity_at=NOW,
        token_expires_at=NOW + timedelta(hours=1),
        idle_timeout_s=1800,
        turn_cap=20,
    )


def _token(*, access: str = ACCESS, refresh: str | None = REFRESH) -> TokenResponse:
    return TokenResponse(
        access_token=SecretStr(access),
        token_type="Bearer",
        expires_in=3600,
        refresh_expires_in=7200,
        scope="openid offline_access user/Patient.read",
        patient="synthetic-patient",
        refresh_token=SecretStr(refresh) if refresh is not None else None,
        clinician_sub="Practitioner/synthetic-clinician",
    )


@pytest.mark.asyncio
async def test_vault_persists_only_authenticated_ciphertext_and_resolves_bound_principal() -> None:
    repository = InMemoryJobCredentialRepository()
    cipher = CredentialCipher(SecretStr(Fernet.generate_key().decode()))
    vault = JobCredentialVault(
        repository,
        cipher,
        refresh_access_token=_unexpected_refresh,
        now=lambda: NOW,
    )

    credential_ref = await vault.store(
        _session(), _token(), access_expires_at=NOW + timedelta(minutes=30)
    )
    stored = await repository.get(credential_ref)

    assert ACCESS.encode() not in stored.ciphertext
    assert REFRESH.encode() not in stored.ciphertext
    assert ACCESS not in repr(stored)
    assert REFRESH not in repr(stored)
    assert await vault.reference_for_session(_session()) == credential_ref
    principal = await vault.principal_for(
        credential_ref, expected_patient_id="synthetic-patient"
    )
    assert principal.patient_id == "synthetic-patient"
    assert principal.clinician_sub == "Practitioner/synthetic-clinician"
    assert principal.access_token.get_secret_value() == ACCESS
    assert await vault.probe() is True


@pytest.mark.asyncio
async def test_vault_refreshes_expired_access_and_rotates_ciphertext() -> None:
    repository = InMemoryJobCredentialRepository()
    cipher = CredentialCipher(Fernet.generate_key().decode())
    observed: list[str] = []

    async def refresh(token: SecretStr) -> TokenResponse:
        observed.append(token.get_secret_value())
        return _token(access="rotated-access", refresh=None)

    vault = JobCredentialVault(
        repository,
        cipher,
        refresh_access_token=refresh,
        now=lambda: NOW,
    )
    credential_ref = await vault.store(
        _session(), _token(), access_expires_at=NOW - timedelta(seconds=1)
    )
    before = await repository.get(credential_ref)

    principal = await vault.principal_for(
        credential_ref, expected_patient_id="synthetic-patient"
    )
    after = await repository.get(credential_ref)

    assert observed == [REFRESH]
    assert principal.access_token.get_secret_value() == "rotated-access"
    assert after.revision == before.revision + 1
    assert after.ciphertext != before.ciphertext
    assert b"rotated-access" not in after.ciphertext
    # A refresh response may omit refresh_token; the delegated refresh credential survives.
    assert cipher.decrypt(after.ciphertext).refresh_token.get_secret_value() == REFRESH


@pytest.mark.asyncio
async def test_vault_fails_closed_for_binding_mismatch_and_refresh_expiry() -> None:
    repository = InMemoryJobCredentialRepository()
    vault = JobCredentialVault(
        repository,
        CredentialCipher(Fernet.generate_key().decode()),
        refresh_access_token=_expired_refresh,
        now=lambda: NOW,
    )
    credential_ref = await vault.store(
        _session(), _token(), access_expires_at=NOW - timedelta(seconds=1)
    )

    with pytest.raises(JobCredentialBindingError):
        await vault.principal_for(credential_ref, expected_patient_id="other-patient")
    with pytest.raises(JobCredentialAuthExpired):
        await vault.principal_for(
            credential_ref, expected_patient_id="synthetic-patient"
        )


def test_cipher_rejects_tampering_without_disclosing_material() -> None:
    cipher = CredentialCipher(Fernet.generate_key().decode())
    encrypted = cipher.encrypt_material(
        access_token=SecretStr(ACCESS),
        refresh_token=SecretStr(REFRESH),
        token_type="Bearer",
        scope="offline_access",
        patient_id="synthetic-patient",
        clinician_sub="Practitioner/synthetic-clinician",
        access_expires_at=NOW,
        refresh_expires_at=None,
    )
    tampered = encrypted[:-1] + bytes([encrypted[-1] ^ 1])

    with pytest.raises(JobCredentialUnavailable) as caught:
        cipher.decrypt(tampered)
    assert ACCESS not in str(caught.value)
    assert REFRESH not in str(caught.value)


@pytest.mark.asyncio
async def test_smart_refresh_uses_only_refresh_token_grant_and_masks_failures() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        values = dict(item.split("=", 1) for item in request.content.decode().split("&"))
        seen.append(values)
        return httpx.Response(
            200,
            json={
                "access_token": "rotated-access",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = _smart_client(http)
        token = await client.refresh_token(refresh_token=SecretStr(REFRESH))

    assert token.access_token.get_secret_value() == "rotated-access"
    assert seen == [
        {
            "grant_type": "refresh_token",
            "refresh_token": REFRESH,
            "client_id": "synthetic-client",
            "client_secret": "synthetic-secret",
        }
    ]
    assert "client_credentials" not in repr(seen)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error"),
    ((400, DelegatedRefreshExpired), (401, DelegatedRefreshExpired), (503, SmartAuthUnavailable)),
)
async def test_smart_refresh_maps_expired_and_unavailable_without_response_body(
    status: int, error: type[Exception]
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(status, json={"error": "sensitive-synthetic"})
        )
    ) as http:
        with pytest.raises(error) as caught:
            await _smart_client(http).refresh_token(refresh_token=SecretStr(REFRESH))
    assert REFRESH not in str(caught.value)
    assert "sensitive-synthetic" not in str(caught.value)


@pytest.mark.asyncio
async def test_postgres_repository_writes_ciphertext_and_enforces_revision_cas() -> None:
    connection = _CredentialConnection()
    repository = PostgresJobCredentialRepository(lambda: _return(connection))
    cipher = CredentialCipher(Fernet.generate_key().decode())
    vault = JobCredentialVault(
        repository,
        cipher,
        refresh_access_token=_unexpected_refresh,
        now=lambda: NOW,
    )

    credential_ref = await vault.store(
        _session(), _token(), access_expires_at=NOW + timedelta(hours=1)
    )
    stored = await repository.get(credential_ref)

    assert stored.patient_id == "synthetic-patient"
    assert all(ACCESS not in repr(args) and REFRESH not in repr(args) for args in connection.args)
    assert connection.closed_count >= 2


async def _unexpected_refresh(_token: SecretStr) -> TokenResponse:
    raise AssertionError("refresh should not run")


async def _expired_refresh(_token: SecretStr) -> TokenResponse:
    raise DelegatedRefreshExpired("delegated refresh expired")


def _smart_client(http: httpx.AsyncClient) -> SmartClient:
    return SmartClient(
        client_id="synthetic-client",
        client_secret="synthetic-secret",
        authorize_endpoint="https://openemr.test/oauth/authorize",
        token_endpoint="https://openemr.test/oauth/token",
        fhir_base_url="https://openemr.test/apis/default/fhir",
        redirect_uri="https://agent.test/callback",
        http_client=http,
    )


async def _return(value):
    return value


class _CredentialConnection:
    def __init__(self) -> None:
        self.row: dict[str, object] | None = None
        self.args: list[tuple[object, ...]] = []
        self.closed_count = 0

    async def fetchrow(self, sql: str, *args: object):
        self.args.append(args)
        if "INSERT INTO agent_job_credentials" in sql:
            credential_ref, session_id, clinician_sub, patient_id, ciphertext = args[:5]
            self.row = {
                "credential_ref": credential_ref,
                "session_id": session_id,
                "clinician_sub": clinician_sub,
                "patient_id": patient_id,
                "ciphertext": ciphertext,
                "access_expires_at": args[5],
                "refresh_expires_at": args[6],
                "revision": 1,
                "created_ts": args[7],
                "updated_ts": args[7],
            }
            return self.row
        if "WHERE credential_ref=$1" in sql:
            return self.row if self.row and self.row["credential_ref"] == args[0] else None
        raise AssertionError(sql)

    async def close(self) -> None:
        self.closed_count += 1
