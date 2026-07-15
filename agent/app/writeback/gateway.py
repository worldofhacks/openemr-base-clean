"""Typed seam between writeback backends and live OpenEMR HTTP operations.

The concrete backends in this package own reconciliation semantics.  A production
gateway owns authentication and the actual standard-REST/FHIR requests; tests inject a
gateway double and never open a socket (W2-D1/D9/D10; §2/§3/§5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence


@dataclass(frozen=True)
class CategoryRecord:
    """One path-to-category/ACL result from the provisioned category gateway."""

    path: str
    category_id: str
    writable: bool


@dataclass(frozen=True)
class DocumentRecord:
    """Minimal standard-API document-list projection used for reconciliation."""

    remote_id: str
    filename: str


@dataclass(frozen=True)
class VitalRecord:
    """Minimal standard-API vital-list projection used for reconciliation.

    ``payload_hash`` is the gateway's canonical hash of the persisted clinical payload;
    the shorter copy in ``note`` is only a discoverability marker and is never accepted
    as the full fingerprint.
    """

    remote_id: str
    note: str
    payload_hash: str


@dataclass(frozen=True)
class VitalReadback:
    """Joined standard-vital and field-specific FHIR Observation readback."""

    remote_id: str
    note: str
    standard_payload_hash: str
    fhir_payload_hash: str | None


class OpenEMRDocumentGateway(Protocol):
    """Live operations required by :class:`OpenEMRDocumentBackend`."""

    async def resolve_document_categories(
        self, path: str
    ) -> Sequence[CategoryRecord]: ...

    async def list_documents(
        self, *, patient_id: str, category_path: str
    ) -> Sequence[DocumentRecord]: ...

    async def read_document_bytes(
        self, *, patient_id: str, remote_id: str
    ) -> bytes | None: ...

    async def create_document(
        self,
        *,
        patient_id: str,
        category_path: str,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> str | None: ...


class OpenEMRVitalGateway(Protocol):
    """Live operations required by :class:`OpenEMRVitalBackend`."""

    async def list_vitals(
        self, *, patient_id: str, encounter_id: str
    ) -> Sequence[VitalRecord]: ...

    async def read_vital(
        self, *, patient_id: str, encounter_id: str, remote_id: str
    ) -> VitalReadback | None: ...

    async def create_vital(
        self,
        *,
        patient_id: str,
        encounter_id: str,
        payload: Mapping[str, object],
    ) -> str | None: ...
