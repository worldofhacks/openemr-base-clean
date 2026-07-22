"""Durable encrypted delegated-job credentials (W2-D1/D9/D10; §3/§3a).

Background document jobs may outlive the interactive session, but they must retain the
uploading clinician's delegated identity.  This module persists only Fernet-authenticated
ciphertext plus non-secret binding metadata.  It never manufactures a service principal:
an expired access token is refreshed solely through the user's ``refresh_token`` grant.
"""

from __future__ import annotations

import json
import secrets
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import cast

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from app.auth.smart_client import (
    DelegatedRefreshExpired,
    SmartAuthError,
    SmartAuthUnavailable,
    TokenResponse,
)
from app.session.store import Session
from app.writeback.rest_client import DelegatedPrincipal


class JobCredentialError(RuntimeError):
    """Base error whose messages never contain token or ciphertext material."""


class JobCredentialUnavailable(JobCredentialError):
    """Credential storage, authenticated decryption, or refresh is unavailable."""


class JobCredentialBindingError(JobCredentialError):
    """The durable credential is not bound to the requested patient/principal."""


class JobCredentialAuthExpired(JobCredentialError):
    """Delegated refresh expired/revoked; clinician reauthorization is required."""


class JobCredentialNotFound(JobCredentialUnavailable):
    """No durable credential exists for the opaque reference."""


@dataclass(frozen=True)
class JobCredentialRecord:
    credential_ref: str
    session_id: str
    clinician_sub: str
    patient_id: str
    ciphertext: bytes = field(repr=False)
    access_expires_at: datetime
    refresh_expires_at: datetime | None
    revision: int
    created_ts: datetime
    updated_ts: datetime


class CredentialMaterial(BaseModel):
    """Decrypted material. SecretStr keeps accidental repr/error rendering masked."""

    model_config = ConfigDict(frozen=True)

    access_token: SecretStr
    refresh_token: SecretStr
    token_type: str
    scope: str
    patient_id: str
    clinician_sub: str
    access_expires_at: datetime
    refresh_expires_at: datetime | None = None


@dataclass(frozen=True)
class DelegatedSessionCredential:
    """Refresh-safe foreground token projection with no refresh secret exposed."""

    access_token: SecretStr = field(repr=False)
    token_type: str
    scope: str
    patient_id: str
    clinician_sub: str
    access_expires_at: datetime

    def as_token_response(self) -> TokenResponse:
        return TokenResponse(
            access_token=self.access_token,
            token_type=self.token_type,
            scope=self.scope,
            patient=self.patient_id,
            clinician_sub=self.clinician_sub,
        )


class CredentialCipher:
    """Small Fernet envelope; the key is retained only inside the crypto primitive."""

    def __init__(self, key: SecretStr | str) -> None:
        value = key.get_secret_value() if isinstance(key, SecretStr) else key
        try:
            self._fernet = Fernet(value.encode("ascii"))
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ValueError(
                "DOCUMENT_CREDENTIAL_KEY must be a valid Fernet key"
            ) from exc

    def encrypt_material(
        self,
        *,
        access_token: SecretStr,
        refresh_token: SecretStr,
        token_type: str,
        scope: str,
        patient_id: str,
        clinician_sub: str,
        access_expires_at: datetime,
        refresh_expires_at: datetime | None,
    ) -> bytes:
        payload = {
            "version": 1,
            "access_token": access_token.get_secret_value(),
            "refresh_token": refresh_token.get_secret_value(),
            "token_type": token_type,
            "scope": scope,
            "patient_id": patient_id,
            "clinician_sub": clinician_sub,
            "access_expires_at": _aware(access_expires_at).isoformat(),
            "refresh_expires_at": (
                _aware(refresh_expires_at).isoformat()
                if refresh_expires_at is not None
                else None
            ),
        }
        serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        return self._fernet.encrypt(serialized)

    def decrypt(self, ciphertext: bytes) -> CredentialMaterial:
        try:
            decoded = self._fernet.decrypt(ciphertext)
            payload = json.loads(decoded)
            if not isinstance(payload, dict) or payload.pop("version", None) != 1:
                raise ValueError("unsupported envelope")
            return CredentialMaterial.model_validate(payload)
        except (
            InvalidToken,
            ValueError,
            TypeError,
            UnicodeError,
            ValidationError,
        ) as exc:
            raise JobCredentialUnavailable(
                "delegated-job credential could not be authenticated"
            ) from exc

    def probe(self) -> bool:
        """Exercise authenticated encryption without using clinical or secret data."""
        probe = self._fernet.encrypt(b"credential-crypto-ready")
        return self._fernet.decrypt(probe) == b"credential-crypto-ready"


class JobCredentialRepository(ABC):
    @abstractmethod
    async def upsert(self, record: JobCredentialRecord) -> JobCredentialRecord: ...

    @abstractmethod
    async def get(self, credential_ref: str) -> JobCredentialRecord: ...

    @abstractmethod
    async def find_by_session(self, session_id: str) -> JobCredentialRecord | None: ...

    @abstractmethod
    async def rotate(
        self,
        credential_ref: str,
        *,
        expected_revision: int,
        ciphertext: bytes,
        access_expires_at: datetime,
        refresh_expires_at: datetime | None,
        updated_ts: datetime,
    ) -> JobCredentialRecord | None: ...

    @abstractmethod
    async def delete(self, credential_ref: str) -> None: ...

    @abstractmethod
    async def probe(self) -> bool: ...


class InMemoryJobCredentialRepository(JobCredentialRepository):
    """Contract-equivalent fake for tests; ciphertext remains opaque in repr."""

    def __init__(self) -> None:
        self._rows: dict[str, JobCredentialRecord] = {}
        self._by_session: dict[str, str] = {}

    async def upsert(self, record: JobCredentialRecord) -> JobCredentialRecord:
        existing_ref = self._by_session.get(record.session_id)
        if existing_ref is not None:
            existing = self._rows[existing_ref]
            if (
                existing.patient_id != record.patient_id
                or existing.clinician_sub != record.clinician_sub
            ):
                raise JobCredentialBindingError(
                    "session credential binding cannot be changed"
                )
            record = JobCredentialRecord(
                credential_ref=existing.credential_ref,
                session_id=existing.session_id,
                clinician_sub=existing.clinician_sub,
                patient_id=existing.patient_id,
                ciphertext=record.ciphertext,
                access_expires_at=record.access_expires_at,
                refresh_expires_at=record.refresh_expires_at,
                revision=existing.revision + 1,
                created_ts=existing.created_ts,
                updated_ts=record.updated_ts,
            )
        self._rows[record.credential_ref] = record
        self._by_session[record.session_id] = record.credential_ref
        return record

    async def get(self, credential_ref: str) -> JobCredentialRecord:
        try:
            return self._rows[credential_ref]
        except KeyError as exc:
            raise JobCredentialNotFound("delegated-job credential not found") from exc

    async def find_by_session(self, session_id: str) -> JobCredentialRecord | None:
        credential_ref = self._by_session.get(session_id)
        return self._rows.get(credential_ref) if credential_ref is not None else None

    async def rotate(
        self,
        credential_ref: str,
        *,
        expected_revision: int,
        ciphertext: bytes,
        access_expires_at: datetime,
        refresh_expires_at: datetime | None,
        updated_ts: datetime,
    ) -> JobCredentialRecord | None:
        current = await self.get(credential_ref)
        if current.revision != expected_revision:
            return None
        rotated = JobCredentialRecord(
            credential_ref=current.credential_ref,
            session_id=current.session_id,
            clinician_sub=current.clinician_sub,
            patient_id=current.patient_id,
            ciphertext=ciphertext,
            access_expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at,
            revision=current.revision + 1,
            created_ts=current.created_ts,
            updated_ts=updated_ts,
        )
        self._rows[credential_ref] = rotated
        return rotated

    async def delete(self, credential_ref: str) -> None:
        current = self._rows.pop(credential_ref, None)
        if current is not None:
            self._by_session.pop(current.session_id, None)

    async def probe(self) -> bool:
        return True


class PostgresJobCredentialRepository(JobCredentialRepository):
    """Restricted durable store backed by migration 005."""

    def __init__(self, connect: Callable[[], Awaitable[object]]) -> None:
        self._connect = connect

    async def upsert(self, record: JobCredentialRecord) -> JobCredentialRecord:
        conn = await self._connection()
        try:
            row = await conn.fetchrow(  # type: ignore[attr-defined]
                """
                INSERT INTO agent_job_credentials
                    (credential_ref, session_id, clinician_sub, patient_id, ciphertext,
                     access_expires_at, refresh_expires_at, revision, created_ts, updated_ts)
                VALUES ($1,$2,$3,$4,$5,$6,$7,1,$8,$8)
                ON CONFLICT (session_id) DO UPDATE
                   SET ciphertext=EXCLUDED.ciphertext,
                       access_expires_at=EXCLUDED.access_expires_at,
                       refresh_expires_at=EXCLUDED.refresh_expires_at,
                       revision=agent_job_credentials.revision+1,
                       updated_ts=EXCLUDED.updated_ts
                 WHERE agent_job_credentials.clinician_sub=EXCLUDED.clinician_sub
                   AND agent_job_credentials.patient_id=EXCLUDED.patient_id
                RETURNING *
                """,
                record.credential_ref,
                record.session_id,
                record.clinician_sub,
                record.patient_id,
                record.ciphertext,
                record.access_expires_at,
                record.refresh_expires_at,
                record.updated_ts,
            )
            if row is None:
                raise JobCredentialBindingError(
                    "session credential binding cannot be changed"
                )
            return _record(row)
        except JobCredentialError:
            raise
        except Exception as exc:  # noqa: BLE001 - fail closed at the storage boundary
            raise JobCredentialUnavailable(
                "delegated-job credential store unavailable"
            ) from exc
        finally:
            await _close(conn)

    async def get(self, credential_ref: str) -> JobCredentialRecord:
        conn = await self._connection()
        try:
            row = await conn.fetchrow(  # type: ignore[attr-defined]
                "SELECT * FROM agent_job_credentials WHERE credential_ref=$1",
                credential_ref,
            )
            if row is None:
                raise JobCredentialNotFound("delegated-job credential not found")
            return _record(row)
        except JobCredentialError:
            raise
        except Exception as exc:  # noqa: BLE001 - fail closed at the storage boundary
            raise JobCredentialUnavailable(
                "delegated-job credential store unavailable"
            ) from exc
        finally:
            await _close(conn)

    async def find_by_session(self, session_id: str) -> JobCredentialRecord | None:
        conn = await self._connection()
        try:
            row = await conn.fetchrow(  # type: ignore[attr-defined]
                "SELECT * FROM agent_job_credentials WHERE session_id=$1",
                session_id,
            )
            return _record(row) if row is not None else None
        except JobCredentialError:
            raise
        except Exception as exc:  # noqa: BLE001 - fail closed at the storage boundary
            raise JobCredentialUnavailable(
                "delegated-job credential store unavailable"
            ) from exc
        finally:
            await _close(conn)

    async def rotate(
        self,
        credential_ref: str,
        *,
        expected_revision: int,
        ciphertext: bytes,
        access_expires_at: datetime,
        refresh_expires_at: datetime | None,
        updated_ts: datetime,
    ) -> JobCredentialRecord | None:
        conn = await self._connection()
        try:
            row = await conn.fetchrow(  # type: ignore[attr-defined]
                """
                UPDATE agent_job_credentials
                   SET ciphertext=$3, access_expires_at=$4, refresh_expires_at=$5,
                       revision=revision+1, updated_ts=$6
                 WHERE credential_ref=$1 AND revision=$2
                RETURNING *
                """,
                credential_ref,
                expected_revision,
                ciphertext,
                access_expires_at,
                refresh_expires_at,
                updated_ts,
            )
            return _record(row) if row is not None else None
        except JobCredentialError:
            raise
        except Exception as exc:  # noqa: BLE001 - fail closed at the storage boundary
            raise JobCredentialUnavailable(
                "delegated-job credential store unavailable"
            ) from exc
        finally:
            await _close(conn)

    async def delete(self, credential_ref: str) -> None:
        conn = await self._connection()
        try:
            await conn.execute(  # type: ignore[attr-defined]
                "DELETE FROM agent_job_credentials WHERE credential_ref=$1",
                credential_ref,
            )
        except JobCredentialError:
            raise
        except Exception as exc:  # noqa: BLE001 - fail closed at the storage boundary
            raise JobCredentialUnavailable(
                "delegated-job credential store unavailable"
            ) from exc
        finally:
            await _close(conn)

    async def probe(self) -> bool:
        conn = await self._connection()
        try:
            value = await conn.fetchval(  # type: ignore[attr-defined]
                "SELECT 1 FROM agent_job_credentials LIMIT 1"
            )
            return value in (None, 1)
        except JobCredentialError:
            raise
        except Exception as exc:  # noqa: BLE001 - fail closed at the storage boundary
            raise JobCredentialUnavailable(
                "delegated-job credential store unavailable"
            ) from exc
        finally:
            await _close(conn)

    async def _connection(self) -> object:
        try:
            return await self._connect()
        except Exception as exc:  # noqa: BLE001 - storage must fail closed
            raise JobCredentialUnavailable(
                "delegated-job credential store unavailable"
            ) from exc


RefreshAccessToken = Callable[[SecretStr], Awaitable[TokenResponse]]


class JobCredentialVault:
    """Binding enforcement, encrypted persistence, and delegated refresh rotation."""

    def __init__(
        self,
        repository: JobCredentialRepository,
        cipher: CredentialCipher,
        refresh_access_token: RefreshAccessToken,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        refresh_skew_seconds: int = 60,
    ) -> None:
        if refresh_skew_seconds < 0:
            raise ValueError("refresh_skew_seconds cannot be negative")
        self._repository = repository
        self._cipher = cipher
        self._refresh_access_token = refresh_access_token
        self._now = now
        self._refresh_skew = timedelta(seconds=refresh_skew_seconds)

    async def store(
        self,
        session: Session,
        token: TokenResponse,
        *,
        access_expires_at: datetime,
    ) -> str:
        self._assert_token_binding(session, token)
        if token.refresh_token is None:
            raise JobCredentialUnavailable(
                "delegated refresh token is required for background work"
            )
        now = _aware(self._now())
        refresh_expires_at = (
            now + timedelta(seconds=token.refresh_expires_in)
            if token.refresh_expires_in is not None
            else None
        )
        ciphertext = self._cipher.encrypt_material(
            access_token=token.access_token,
            refresh_token=token.refresh_token,
            token_type=token.token_type,
            scope=token.scope,
            patient_id=session.patient_id,
            clinician_sub=session.clinician_sub,
            access_expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at,
        )
        record = JobCredentialRecord(
            credential_ref=f"credential:{secrets.token_urlsafe(24)}",
            session_id=session.session_id,
            clinician_sub=session.clinician_sub,
            patient_id=session.patient_id,
            ciphertext=ciphertext,
            access_expires_at=_aware(access_expires_at),
            refresh_expires_at=refresh_expires_at,
            revision=1,
            created_ts=now,
            updated_ts=now,
        )
        return (await self._repository.upsert(record)).credential_ref

    async def reference_for_session(self, session: Session) -> str:
        record = await self._repository.find_by_session(session.session_id)
        if record is None:
            raise JobCredentialNotFound("delegated-job credential not found")
        self._assert_record_binding(record, session.patient_id, session.clinician_sub)
        return record.credential_ref

    async def principal_for(
        self, credential_ref: str, *, expected_patient_id: str
    ) -> DelegatedPrincipal:
        record = await self._repository.get(credential_ref)
        record, material = await self._active_material(
            record, expected_patient_id=expected_patient_id
        )
        return self._principal(record, material)

    async def credential_for_session(
        self, session: Session
    ) -> DelegatedSessionCredential:
        """Hydrate or refresh a foreground session from the encrypted vault.

        The opaque session id, clinician, and patient must all match the durable
        record. Only an access-token projection leaves this boundary; the delegated
        refresh token remains encrypted inside the vault.
        """

        record = await self._repository.find_by_session(session.session_id)
        if record is None:
            raise JobCredentialNotFound("delegated-job credential not found")
        self._assert_record_binding(record, session.patient_id, session.clinician_sub)
        record, material = await self._active_material(
            record, expected_patient_id=session.patient_id
        )
        return DelegatedSessionCredential(
            access_token=material.access_token,
            token_type=material.token_type,
            scope=material.scope,
            patient_id=record.patient_id,
            clinician_sub=record.clinician_sub,
            access_expires_at=material.access_expires_at,
        )

    async def _active_material(
        self, record: JobCredentialRecord, *, expected_patient_id: str
    ) -> tuple[JobCredentialRecord, CredentialMaterial]:
        self._assert_record_binding(record, expected_patient_id)
        material = self._decrypt_bound(record)
        now = _aware(self._now())
        if material.access_expires_at > now + self._refresh_skew:
            return record, material

        if (
            material.refresh_expires_at is not None
            and material.refresh_expires_at <= now
        ):
            raise JobCredentialAuthExpired(
                "delegated refresh expired; clinician reauthorization required"
            )
        refreshed = await self._refresh(material.refresh_token)
        self._assert_refreshed_binding(record, refreshed)
        if refreshed.expires_in is None or refreshed.expires_in <= 0:
            raise JobCredentialUnavailable(
                "delegated refresh returned no usable access-token lifetime"
            )
        refresh_token = refreshed.refresh_token or material.refresh_token
        refresh_expires_at = (
            now + timedelta(seconds=refreshed.refresh_expires_in)
            if refreshed.refresh_expires_in is not None
            else material.refresh_expires_at
        )
        access_expires_at = now + timedelta(seconds=refreshed.expires_in)
        ciphertext = self._cipher.encrypt_material(
            access_token=refreshed.access_token,
            refresh_token=refresh_token,
            token_type=refreshed.token_type or material.token_type,
            scope=refreshed.scope or material.scope,
            patient_id=record.patient_id,
            clinician_sub=record.clinician_sub,
            access_expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at,
        )
        rotated = await self._repository.rotate(
            record.credential_ref,
            expected_revision=record.revision,
            ciphertext=ciphertext,
            access_expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at,
            updated_ts=now,
        )
        if rotated is None:
            # Another worker rotated first. Use only the authenticated winning value.
            rotated = await self._repository.get(record.credential_ref)
        self._assert_record_binding(rotated, expected_patient_id)
        return rotated, self._decrypt_bound(rotated)

    async def delete(self, credential_ref: str) -> None:
        await self._repository.delete(credential_ref)

    async def probe(self) -> bool:
        return self._cipher.probe() and await self._repository.probe()

    async def _refresh(self, refresh_token: SecretStr) -> TokenResponse:
        try:
            return await self._refresh_access_token(refresh_token)
        except DelegatedRefreshExpired as exc:
            raise JobCredentialAuthExpired(
                "delegated refresh expired; clinician reauthorization required"
            ) from exc
        except SmartAuthUnavailable as exc:
            raise JobCredentialUnavailable(
                "delegated token refresh temporarily unavailable"
            ) from exc
        except SmartAuthError as exc:
            raise JobCredentialAuthExpired(
                "delegated refresh failed; clinician reauthorization required"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - boundary fails closed, never logs values
            raise JobCredentialUnavailable(
                "delegated token refresh temporarily unavailable"
            ) from exc

    def _decrypt_bound(self, record: JobCredentialRecord) -> CredentialMaterial:
        material = self._cipher.decrypt(record.ciphertext)
        if (
            material.patient_id != record.patient_id
            or material.clinician_sub != record.clinician_sub
            or material.access_expires_at != record.access_expires_at
            or material.refresh_expires_at != record.refresh_expires_at
        ):
            raise JobCredentialBindingError(
                "delegated-job credential binding metadata does not match"
            )
        return material

    @staticmethod
    def _assert_token_binding(session: Session, token: TokenResponse) -> None:
        if token.patient is not None and token.patient != session.patient_id:
            raise JobCredentialBindingError(
                "delegated token is bound to a different patient"
            )
        if (
            token.clinician_sub is not None
            and token.clinician_sub != session.clinician_sub
        ):
            raise JobCredentialBindingError(
                "delegated token is bound to a different clinician"
            )

    @staticmethod
    def _assert_record_binding(
        record: JobCredentialRecord,
        patient_id: str,
        clinician_sub: str | None = None,
    ) -> None:
        if record.patient_id != patient_id or (
            clinician_sub is not None and record.clinician_sub != clinician_sub
        ):
            raise JobCredentialBindingError(
                "delegated-job credential is bound to a different principal"
            )

    @staticmethod
    def _assert_refreshed_binding(
        record: JobCredentialRecord, token: TokenResponse
    ) -> None:
        if token.patient is not None and token.patient != record.patient_id:
            raise JobCredentialBindingError(
                "refreshed token is bound to a different patient"
            )
        if (
            token.clinician_sub is not None
            and token.clinician_sub != record.clinician_sub
        ):
            raise JobCredentialBindingError(
                "refreshed token is bound to a different clinician"
            )

    @staticmethod
    def _principal(
        record: JobCredentialRecord, material: CredentialMaterial
    ) -> DelegatedPrincipal:
        return DelegatedPrincipal(
            clinician_sub=record.clinician_sub,
            patient_id=record.patient_id,
            access_token=material.access_token,
        )


def _record(row: object) -> JobCredentialRecord:
    values = dict(cast(Mapping[str, object], row))
    ciphertext = values["ciphertext"]
    return JobCredentialRecord(
        credential_ref=str(values["credential_ref"]),
        session_id=str(values["session_id"]),
        clinician_sub=str(values["clinician_sub"]),
        patient_id=str(values["patient_id"]),
        ciphertext=(
            ciphertext.tobytes()  # asyncpg may return a memoryview for BYTEA
            if isinstance(ciphertext, memoryview)
            else bytes(cast(bytes, ciphertext))
        ),
        access_expires_at=_aware(cast(datetime, values["access_expires_at"])),
        refresh_expires_at=(
            _aware(cast(datetime, values["refresh_expires_at"]))
            if values.get("refresh_expires_at") is not None
            else None
        ),
        revision=int(str(values["revision"])),
        created_ts=_aware(cast(datetime, values["created_ts"])),
        updated_ts=_aware(cast(datetime, values["updated_ts"])),
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("credential timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


async def _close(conn: object) -> None:
    close = getattr(conn, "close", None)
    if close is None:
        return
    result = close()
    if hasattr(result, "__await__"):
        await result
