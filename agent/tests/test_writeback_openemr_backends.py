"""Concrete OpenEMR backend-adapter tests (W2-D1/D9/D10; §2/§3/§5)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import pytest

from app.writeback.gateway import (
    CategoryRecord,
    DocumentRecord,
    VitalReadback,
    VitalRecord,
)
from app.writeback.intents import RemoteMatch
from app.writeback.preflight import CategoryMismatch, CategoryResolution
from app.writeback.rest_client import OpenEMRWriteError
from app.writeback.transports import DocumentWritePayload, VitalWritePayload


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass
class FakeOpenEMRGateway:
    categories: list[CategoryRecord] = field(default_factory=list)
    documents: list[DocumentRecord] = field(default_factory=list)
    document_bytes: dict[str, bytes | None] = field(default_factory=dict)
    vitals: list[VitalRecord] = field(default_factory=list)
    vital_readbacks: dict[str, VitalReadback | None] = field(default_factory=dict)
    created_document_id: str | None = "doc-created"
    created_vital_id: str | None = "vital-created"
    category_requests: list[str] = field(default_factory=list)
    document_list_requests: list[tuple[str, str]] = field(default_factory=list)
    document_read_requests: list[tuple[str, str]] = field(default_factory=list)
    document_creates: list[dict[str, object]] = field(default_factory=list)
    vital_list_requests: list[tuple[str, str]] = field(default_factory=list)
    vital_read_requests: list[tuple[str, str, str]] = field(default_factory=list)
    vital_creates: list[dict[str, object]] = field(default_factory=list)

    async def resolve_document_categories(self, path: str):
        self.category_requests.append(path)
        return list(self.categories)

    async def list_documents(self, *, patient_id: str, category_path: str):
        self.document_list_requests.append((patient_id, category_path))
        return list(self.documents)

    async def read_document_bytes(self, *, patient_id: str, remote_id: str):
        self.document_read_requests.append((patient_id, remote_id))
        return self.document_bytes.get(remote_id)

    async def create_document(
        self,
        *,
        patient_id: str,
        category_path: str,
        filename: str,
        content_type: str,
        content: bytes,
    ):
        self.document_creates.append(
            {
                "patient_id": patient_id,
                "category_path": category_path,
                "filename": filename,
                "content_type": content_type,
                "content": content,
            }
        )
        return self.created_document_id

    async def list_vitals(self, *, patient_id: str, encounter_id: str):
        self.vital_list_requests.append((patient_id, encounter_id))
        return list(self.vitals)

    async def read_vital(self, *, patient_id: str, encounter_id: str, remote_id: str):
        self.vital_read_requests.append((patient_id, encounter_id, remote_id))
        return self.vital_readbacks.get(remote_id)

    async def create_vital(
        self, *, patient_id: str, encounter_id: str, payload: dict[str, object]
    ):
        self.vital_creates.append(
            {
                "patient_id": patient_id,
                "encounter_id": encounter_id,
                "payload": payload,
            }
        )
        return self.created_vital_id


@pytest.mark.asyncio
async def test_document_backend_adapts_one_exact_category_resolution():
    from app.writeback.documents_api import OpenEMRDocumentBackend

    gateway = FakeOpenEMRGateway(
        categories=[
            CategoryRecord(path="/AI-Extractions", category_id="27", writable=True)
        ]
    )
    backend = OpenEMRDocumentBackend(gateway, category_path="/AI-Extractions")

    resolved = await backend.resolve_category("/AI-Extractions")

    assert resolved == CategoryResolution(
        path="/AI-Extractions", category_id="27", writable=True
    )
    assert gateway.category_requests == ["/AI-Extractions"]


@pytest.mark.asyncio
async def test_document_backend_rejects_ambiguous_category_resolution():
    from app.writeback.documents_api import OpenEMRDocumentBackend

    gateway = FakeOpenEMRGateway(
        categories=[
            CategoryRecord("/AI-Extractions", "27", True),
            CategoryRecord("/AI-Extractions", "28", True),
        ]
    )
    backend = OpenEMRDocumentBackend(gateway, category_path="/AI-Extractions")

    with pytest.raises(CategoryMismatch):
        await backend.resolve_category("/AI-Extractions")


@pytest.mark.asyncio
async def test_document_find_requires_marker_boundary_and_sha256_payload_match():
    from app.writeback.documents_api import OpenEMRDocumentBackend

    expected = b"synthetic grounded artifact"
    marker = "document:synthetic-1:artifact:v1"
    gateway = FakeOpenEMRGateway(
        documents=[
            DocumentRecord("doc-match", f"{marker}-artifact.json"),
            DocumentRecord("doc-wrong-payload", f"{marker}-other.json"),
            DocumentRecord("doc-wrong-marker", "document:synthetic-2:artifact:v1.json"),
            DocumentRecord("doc-marker-collision", f"{marker}extra-artifact.json"),
        ],
        document_bytes={
            "doc-match": expected,
            "doc-wrong-payload": b"different bytes",
            "doc-wrong-marker": expected,
            "doc-marker-collision": expected,
        },
    )
    backend = OpenEMRDocumentBackend(gateway, category_path="/AI-Extractions")

    matches = await backend.find_documents(
        patient_id="patient-synthetic-a",
        marker=marker,
        payload_hash=_sha256(expected),
    )

    assert matches == [RemoteMatch("doc-match", _sha256(expected))]
    assert gateway.document_list_requests == [
        ("patient-synthetic-a", "/AI-Extractions")
    ]
    assert gateway.document_read_requests == [
        ("patient-synthetic-a", "doc-match"),
        ("patient-synthetic-a", "doc-wrong-payload"),
    ]


@pytest.mark.asyncio
async def test_document_create_uses_fixed_path_and_makes_marker_discoverable():
    from app.writeback.documents_api import OpenEMRDocumentBackend

    gateway = FakeOpenEMRGateway()
    backend = OpenEMRDocumentBackend(gateway, category_path="/AI-Source-Documents")
    payload = DocumentWritePayload(
        filename="synthetic.pdf",
        content_type="application/pdf",
        content=b"%PDF-synthetic",
    )

    remote_id = await backend.create_document(
        patient_id="patient-synthetic-a",
        category_path="/AI-Source-Documents",
        marker="document:synthetic-1:source:v1",
        payload=payload,
    )

    assert remote_id == "doc-created"
    assert gateway.document_creates == [
        {
            "patient_id": "patient-synthetic-a",
            "category_path": "/AI-Source-Documents",
            "filename": "document:synthetic-1:source:v1-synthetic.pdf",
            "content_type": "application/pdf",
            "content": b"%PDF-synthetic",
        }
    ]

    with pytest.raises(CategoryMismatch):
        await backend.create_document(
            patient_id="patient-synthetic-a",
            category_path="/Other",
            marker="document:synthetic-1:source:v1",
            payload=payload,
        )
    assert len(gateway.document_creates) == 1


@pytest.mark.asyncio
async def test_document_verify_hashes_fhir_binary_readback_bytes():
    from app.writeback.documents_api import OpenEMRDocumentBackend

    expected = b"synthetic source document"
    gateway = FakeOpenEMRGateway(document_bytes={"doc-1": expected})
    backend = OpenEMRDocumentBackend(gateway, category_path="/AI-Source-Documents")

    assert await backend.verify_document(
        patient_id="patient-synthetic-a",
        remote_id="doc-1",
        payload_hash=_sha256(expected),
    )
    assert not await backend.verify_document(
        patient_id="patient-synthetic-a",
        remote_id="doc-1",
        payload_hash=_sha256(b"other"),
    )


def _note(marker: str, payload_hash: str) -> str:
    return f"copilot-intent:{marker};payload:{payload_hash[:12]}"


@pytest.mark.asyncio
async def test_vital_find_uses_fixed_encounter_marker_and_full_payload_hash():
    from app.writeback.vitals_api import OpenEMRVitalBackend

    marker = "corr-synthetic-1"
    payload_hash = "a" * 64
    gateway = FakeOpenEMRGateway(
        vitals=[
            VitalRecord("vital-match", _note(marker, payload_hash), payload_hash),
            VitalRecord("vital-wrong-hash", _note(marker, payload_hash), "b" * 64),
            VitalRecord(
                "vital-wrong-marker", _note("corr-other", payload_hash), payload_hash
            ),
            VitalRecord(
                "vital-marker-collision",
                _note(f"{marker}-extra", payload_hash),
                payload_hash,
            ),
        ]
    )
    backend = OpenEMRVitalBackend(gateway, encounter_id="enc-synthetic-1")

    matches = await backend.find_vitals(
        patient_id="patient-synthetic-a",
        marker=marker,
        payload_hash=payload_hash,
    )

    assert matches == [RemoteMatch("vital-match", payload_hash)]
    assert gateway.vital_list_requests == [("patient-synthetic-a", "enc-synthetic-1")]


@pytest.mark.asyncio
async def test_vital_create_enforces_fixed_encounter_and_marker_note():
    from app.writeback.vitals_api import OpenEMRVitalBackend

    marker = "corr-synthetic-1"
    payload_hash = "a" * 64
    gateway = FakeOpenEMRGateway()
    backend = OpenEMRVitalBackend(gateway, encounter_id="enc-synthetic-1")
    payload = VitalWritePayload(
        encounter_id="enc-synthetic-1",
        values={
            "bps": "120",
            "date": "2026-07-14T12:00:00+00:00",
            "note": _note(marker, payload_hash),
        },
    )

    remote_id = await backend.create_vital(
        patient_id="patient-synthetic-a", marker=marker, payload=payload
    )

    assert remote_id == "vital-created"
    assert gateway.vital_creates == [
        {
            "patient_id": "patient-synthetic-a",
            "encounter_id": "enc-synthetic-1",
            "payload": dict(payload.values),
        }
    ]

    with pytest.raises(OpenEMRWriteError):
        await backend.create_vital(
            patient_id="patient-synthetic-a",
            marker=marker,
            payload=VitalWritePayload("enc-other", payload.values),
        )
    with pytest.raises(OpenEMRWriteError):
        await backend.create_vital(
            patient_id="patient-synthetic-a",
            marker="corr-other",
            payload=payload,
        )
    assert len(gateway.vital_creates) == 1


@pytest.mark.asyncio
async def test_vital_verify_requires_standard_and_fhir_payload_readback():
    from app.writeback.vitals_api import OpenEMRVitalBackend

    marker = "corr-synthetic-1"
    payload_hash = "a" * 64
    gateway = FakeOpenEMRGateway(
        vital_readbacks={
            "vital-1": VitalReadback(
                remote_id="vital-1",
                note=_note(marker, payload_hash),
                standard_payload_hash=payload_hash,
                fhir_payload_hash=payload_hash,
            )
        }
    )
    backend = OpenEMRVitalBackend(gateway, encounter_id="enc-synthetic-1")

    assert await backend.verify_vital(
        patient_id="patient-synthetic-a",
        remote_id="vital-1",
        payload_hash=payload_hash,
    )

    gateway.vital_readbacks["vital-1"] = VitalReadback(
        remote_id="vital-1",
        note=_note(marker, payload_hash),
        standard_payload_hash=payload_hash,
        fhir_payload_hash="b" * 64,
    )
    assert not await backend.verify_vital(
        patient_id="patient-synthetic-a",
        remote_id="vital-1",
        payload_hash=payload_hash,
    )

    gateway.vital_readbacks["vital-1"] = None
    assert not await backend.verify_vital(
        patient_id="patient-synthetic-a",
        remote_id="vital-1",
        payload_hash=payload_hash,
    )
