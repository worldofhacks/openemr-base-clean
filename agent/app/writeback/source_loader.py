"""Persisted OpenEMR source-document loader (W2-D1/D9/D10; §3/§5)."""

from __future__ import annotations

import hashlib
import hmac
from typing import Protocol, Sequence

from app.ingestion.repository import DocumentRecord
from app.writeback.gateway import DocumentRecord as RemoteDocumentRecord


class SourceDocumentGateway(Protocol):
    async def list_documents(
        self, *, patient_id: str, category_path: str
    ) -> Sequence[RemoteDocumentRecord]: ...

    async def read_document_bytes(
        self, *, patient_id: str, remote_id: str
    ) -> bytes | None: ...


class SourceDocumentUnavailable(RuntimeError):
    """The permanent source marker/hash did not identify exactly one document."""


class OpenEMRSourceLoader:
    """Resolve the source leg by its exact marker and SHA-256 fingerprint."""

    def __init__(self, gateway: SourceDocumentGateway, *, category_path: str) -> None:
        if not category_path.startswith("/") or ".." in category_path.split("/"):
            raise ValueError("source category must be a canonical absolute path")
        self._gateway = gateway
        self._category_path = category_path

    async def fetch(self, record: DocumentRecord) -> bytes:
        marker_prefix = f"document:{record.document_id}:source:v1-"
        documents = await self._gateway.list_documents(
            patient_id=record.patient_id, category_path=self._category_path
        )
        matches: list[bytes] = []
        for document in documents:
            if not document.filename.startswith(marker_prefix):
                continue
            content = await self._gateway.read_document_bytes(
                patient_id=record.patient_id, remote_id=document.remote_id
            )
            if content is None:
                continue
            actual = hashlib.sha256(content).hexdigest()
            if hmac.compare_digest(actual, record.content_hash):
                matches.append(content)
        if len(matches) != 1:
            raise SourceDocumentUnavailable(record.document_id)
        return matches[0]
