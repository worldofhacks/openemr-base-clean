"""Concrete OpenEMR document backend for the shared intent machine.

The adapter is configured for exactly one canonical document path.  Discovery uses the
standard document list for marker candidates, then hashes bytes re-read through the
gateway's DocumentReference-to-Binary operation (W2-D1/D9/D10; §2/§3/§5).
"""

from __future__ import annotations

import hashlib
import hmac

from app.writeback.gateway import OpenEMRDocumentGateway
from app.writeback.intents import RemoteMatch
from app.writeback.preflight import CategoryMismatch, CategoryResolution
from app.writeback.transports import DocumentWritePayload


class OpenEMRDocumentBackend:
    """Adapt injected live gateway operations to ``DocumentBackend``."""

    def __init__(self, gateway: OpenEMRDocumentGateway, *, category_path: str) -> None:
        self._gateway = gateway
        self._category_path = category_path

    def _require_fixed_path(self, path: str) -> None:
        if (
            path != self._category_path
            or not path.startswith("/")
            or ".." in path.split("/")
        ):
            raise CategoryMismatch("document category path is not the configured path")

    async def resolve_category(self, path: str) -> CategoryResolution:
        self._require_fixed_path(path)
        records = await self._gateway.resolve_document_categories(path)
        exact = [record for record in records if record.path == path]
        if len(exact) != 1:
            raise CategoryMismatch("category path resolution was missing or ambiguous")
        record = exact[0]
        return CategoryResolution(
            path=record.path,
            category_id=record.category_id,
            writable=record.writable,
        )

    async def find_documents(
        self, *, patient_id: str, marker: str, payload_hash: str
    ) -> list[RemoteMatch]:
        records = await self._gateway.list_documents(
            patient_id=patient_id, category_path=self._category_path
        )
        matches: list[RemoteMatch] = []
        marker_prefix = f"{marker}-"
        for record in records:
            if not record.filename.startswith(marker_prefix):
                continue
            content = await self._gateway.read_document_bytes(
                patient_id=patient_id, remote_id=record.remote_id
            )
            if content is None:
                continue
            actual_hash = hashlib.sha256(content).hexdigest()
            if hmac.compare_digest(actual_hash, payload_hash):
                matches.append(RemoteMatch(record.remote_id, actual_hash))
        return matches

    async def create_document(
        self,
        *,
        patient_id: str,
        category_path: str,
        marker: str,
        payload: DocumentWritePayload,
    ) -> str | None:
        self._require_fixed_path(category_path)
        marker_prefix = f"{marker}-"
        filename = payload.filename
        if not filename.startswith(marker_prefix):
            filename = f"{marker_prefix}{filename}"
        return await self._gateway.create_document(
            patient_id=patient_id,
            category_path=self._category_path,
            filename=filename,
            content_type=payload.content_type,
            content=payload.content,
        )

    async def verify_document(
        self, *, patient_id: str, remote_id: str, payload_hash: str
    ) -> bool:
        content = await self._gateway.read_document_bytes(
            patient_id=patient_id, remote_id=remote_id
        )
        if content is None:
            return False
        actual_hash = hashlib.sha256(content).hexdigest()
        return hmac.compare_digest(actual_hash, payload_hash)
