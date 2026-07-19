"""Injectable source/artifact/vital transports for the shared intent machine.

The closed payload DTOs live beside the ``WriteTransport`` protocol in
``app.writeback.intents`` and are re-exported here for their historical import
path (AF-P1-03).
"""

from __future__ import annotations

from typing import Protocol

from app.schemas.writeback import WriteIntent
from app.writeback.intents import (
    AmbiguousCommitError,
    DocumentWritePayload,
    RemoteMatch,
    VitalWritePayload,
    WritePayload,
)
from app.writeback.preflight import (
    CategoryExpectation,
    CategoryResolution,
    verify_category_path,
)
from app.writeback.rest_client import OpenEMRWriteError, strip_caller_attribution

__all__ = [
    "DocumentBackend",
    "DocumentIntentTransport",
    "DocumentWritePayload",
    "ExtractionArtifactTransport",
    "SourceDocumentTransport",
    "VitalBackend",
    "VitalIntentTransport",
    "VitalWritePayload",
    "WritePayload",
]


class DocumentBackend(Protocol):
    async def resolve_category(self, path: str) -> CategoryResolution: ...

    async def find_documents(
        self, *, patient_id: str, marker: str, payload_hash: str
    ) -> list[RemoteMatch]: ...

    async def create_document(
        self,
        *,
        patient_id: str,
        category_path: str,
        marker: str,
        payload: DocumentWritePayload,
    ) -> str | None: ...

    async def verify_document(
        self, *, patient_id: str, remote_id: str, payload_hash: str
    ) -> bool: ...


class VitalBackend(Protocol):
    async def find_vitals(
        self, *, patient_id: str, marker: str, payload_hash: str
    ) -> list[RemoteMatch]: ...

    async def create_vital(
        self,
        *,
        patient_id: str,
        marker: str,
        payload: VitalWritePayload,
    ) -> str | None: ...

    async def verify_vital(
        self, *, patient_id: str, remote_id: str, payload_hash: str
    ) -> bool: ...


class DocumentIntentTransport:
    """Shared document transport; instantiate separately for source and artifact paths."""

    def __init__(
        self, backend: DocumentBackend, *, category: CategoryExpectation
    ) -> None:
        self._backend = backend
        self._category = category

    async def discover(self, intent: WriteIntent) -> list[RemoteMatch]:
        return await self._backend.find_documents(
            patient_id=intent.patient_id,
            marker=intent.correlation_marker,
            payload_hash=intent.payload_hash,
        )

    async def post(self, intent: WriteIntent, payload: WritePayload) -> str | None:
        if not isinstance(payload, DocumentWritePayload):
            raise TypeError("document intent requires DocumentWritePayload")
        resolved = await self._backend.resolve_category(self._category.path)
        category_path = verify_category_path(self._category, resolved)
        try:
            return await self._backend.create_document(
                patient_id=intent.patient_id,
                category_path=category_path,
                marker=intent.correlation_marker,
                payload=payload,
            )
        except OpenEMRWriteError as exc:
            if exc.ambiguous:
                raise AmbiguousCommitError(exc.reason) from exc
            raise

    async def verify(
        self, intent: WriteIntent, match: RemoteMatch, payload_hash: str
    ) -> bool:
        return await self._backend.verify_document(
            patient_id=intent.patient_id,
            remote_id=match.remote_id,
            payload_hash=payload_hash,
        )


class SourceDocumentTransport(DocumentIntentTransport):
    """Named source-document seam for B2 wiring."""


class ExtractionArtifactTransport(DocumentIntentTransport):
    """Named grounded-artifact seam for B2/B3 wiring."""


class VitalIntentTransport:
    """One append-only vital-field intent under the delegated token."""

    def __init__(self, backend: VitalBackend) -> None:
        self._backend = backend

    async def discover(self, intent: WriteIntent) -> list[RemoteMatch]:
        return await self._backend.find_vitals(
            patient_id=intent.patient_id,
            marker=intent.correlation_marker,
            payload_hash=intent.payload_hash,
        )

    async def post(self, intent: WriteIntent, payload: WritePayload) -> str | None:
        if not isinstance(payload, VitalWritePayload):
            raise TypeError("vital intent requires VitalWritePayload")
        clean = strip_caller_attribution(payload.values)
        try:
            return await self._backend.create_vital(
                patient_id=intent.patient_id,
                marker=intent.correlation_marker,
                payload=VitalWritePayload(payload.encounter_id, clean),
            )
        except OpenEMRWriteError as exc:
            if exc.ambiguous:
                raise AmbiguousCommitError(exc.reason) from exc
            raise

    async def verify(
        self, intent: WriteIntent, match: RemoteMatch, payload_hash: str
    ) -> bool:
        return await self._backend.verify_vital(
            patient_id=intent.patient_id,
            remote_id=match.remote_id,
            payload_hash=payload_hash,
        )
