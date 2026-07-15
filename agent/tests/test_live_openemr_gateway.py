"""Delegated live OpenEMR gateway contracts (W2-D1/D9/D10; §3/§5)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import httpx
import pytest
from pydantic import SecretStr

from app.writeback.gateway import DocumentRecord
from app.writeback.rest_client import DelegatedPrincipal, OpenEMRWriteError

BASE_URL = "https://openemr.synthetic.example/apis/default"
PATIENT_ID = "11111111-1111-4111-8111-111111111111"
STANDARD_PATIENT_ID = "731"
ENCOUNTER_ID = "22222222-2222-4222-8222-222222222222"
STANDARD_ENCOUNTER_ID = "912"
TOKEN = "synthetic-token-never-log"


def _principal() -> DelegatedPrincipal:
    return DelegatedPrincipal(
        clinician_sub="clinician-synthetic",
        patient_id=PATIENT_ID,
        access_token=SecretStr(TOKEN),
    )


def _bundle(*resources: dict[str, object]) -> dict[str, object]:
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [{"resource": resource} for resource in resources],
    }


def _legacy_attestation(
    *,
    patient_uuid: str = PATIENT_ID,
    encounter_uuid: str = ENCOUNTER_ID,
):
    from app.writeback.live_gateway import LegacyRouteAttestation

    return LegacyRouteAttestation(
        patient_uuid=patient_uuid,
        patient_id=STANDARD_PATIENT_ID,
        encounter_uuid=encounter_uuid,
        encounter_id=STANDARD_ENCOUNTER_ID,
    )


@pytest.mark.asyncio
async def test_attested_category_and_document_create_use_fixed_standard_route():
    from app.writeback.live_gateway import (
        BinaryReadGuard,
        CategoryAttestation,
        OpenEMRLiveGateway,
    )
    from app.writeback.preflight import CategoryMismatch

    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = await request.aread()
        assert request.method == "POST"
        assert (
            request.url.path
            == f"/apis/default/api/patient/{STANDARD_PATIENT_ID}/document"
        )
        assert dict(request.url.params) == {"path": "/AI-Source-Documents"}
        assert b'name="document"' in body
        assert b'filename="document:synthetic:source:v1-source.pdf"' in body
        assert b'name="file"' not in body
        return httpx.Response(200, json=True)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = OpenEMRLiveGateway(
            base_url=BASE_URL,
            principal=_principal(),
            category_attestations=(
                CategoryAttestation("/AI-Source-Documents", "17", True),
                CategoryAttestation("/AI-Extractions", "27", True),
            ),
            legacy_route_attestation=_legacy_attestation(),
            binary_guard=BinaryReadGuard("WARNING"),
            http_client=client,
        )

        assert await gateway.resolve_document_categories("/AI-Source-Documents") == [
            CategoryAttestation("/AI-Source-Documents", "17", True).as_record()
        ]
        assert await gateway.resolve_document_categories("/Unknown") == []
        assert (
            await gateway.create_document(
                patient_id=PATIENT_ID,
                category_path="/AI-Source-Documents",
                filename="document:synthetic:source:v1-source.pdf",
                content_type="application/pdf",
                content=b"%PDF-synthetic",
            )
            is None
        )
        with pytest.raises(CategoryMismatch):
            await gateway.create_document(
                patient_id=PATIENT_ID,
                category_path="/Unknown",
                filename="not-created.pdf",
                content_type="application/pdf",
                content=b"%PDF-not-created",
            )

    assert len(requests) == 1
    assert all(
        request.headers["authorization"] == f"Bearer {TOKEN}"
        for request in requests
    )


@pytest.mark.asyncio
async def test_document_reconcile_maps_bound_uuid_to_pid_and_accepts_empty_404():
    from app.writeback.live_gateway import (
        BinaryReadGuard,
        CategoryAttestation,
        OpenEMRLiveGateway,
    )

    calls: list[tuple[str, str, dict[str, str]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, dict(request.url.params)))
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if request.url.path.endswith(
            f"/api/patient/{STANDARD_PATIENT_ID}/document"
        ):
            # OpenEMR's standard document controller returns 404 for a valid,
            # attested category that currently contains zero documents.
            return httpx.Response(404)
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = OpenEMRLiveGateway(
            base_url=BASE_URL,
            principal=_principal(),
            category_attestations=(
                CategoryAttestation("/AI-Source-Documents", "17", True),
            ),
            legacy_route_attestation=_legacy_attestation(),
            binary_guard=BinaryReadGuard("WARNING"),
            http_client=client,
        )
        assert (
            await gateway.list_documents(
                patient_id=PATIENT_ID,
                category_path="/AI-Source-Documents",
            )
            == []
        )

    assert calls == [
        (
            "GET",
            f"/apis/default/api/patient/{STANDARD_PATIENT_ID}/document",
            {"path": "/AI-Source-Documents"},
        ),
    ]


@pytest.mark.asyncio
async def test_document_pid_resolution_rejects_cross_patient_response():
    from app.writeback.live_gateway import (
        BinaryReadGuard,
        CategoryAttestation,
        LiveGatewayError,
        OpenEMRLiveGateway,
    )

    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = OpenEMRLiveGateway(
            base_url=BASE_URL,
            principal=_principal(),
            category_attestations=(
                CategoryAttestation("/AI-Source-Documents", "17", True),
            ),
            legacy_route_attestation=_legacy_attestation(
                patient_uuid="33333333-3333-4333-8333-333333333333"
            ),
            binary_guard=BinaryReadGuard("WARNING"),
            http_client=client,
        )
        with pytest.raises(LiveGatewayError, match="patient mapping"):
            await gateway.list_documents(
                patient_id=PATIENT_ID,
                category_path="/AI-Source-Documents",
            )

    assert calls == 0


@pytest.mark.parametrize("setting", [None, "", "DEBUG", "debug"])
@pytest.mark.asyncio
async def test_binary_readback_guard_fails_closed_before_any_http(setting: str | None):
    from app.writeback.live_gateway import (
        BinaryReadGuard,
        BinaryReadbackUnsafe,
        CategoryAttestation,
        OpenEMRLiveGateway,
    )

    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = OpenEMRLiveGateway(
            base_url=BASE_URL,
            principal=_principal(),
            category_attestations=(
                CategoryAttestation("/AI-Source-Documents", "17", True),
            ),
            legacy_route_attestation=_legacy_attestation(),
            binary_guard=BinaryReadGuard(setting),
            http_client=client,
        )
        with pytest.raises(BinaryReadbackUnsafe):
            await gateway.read_document_bytes(
                patient_id=PATIENT_ID, remote_id="document-42"
            )

    assert calls == 0


@pytest.mark.asyncio
async def test_document_readback_resolves_documentreference_then_binary():
    from app.writeback.live_gateway import (
        BinaryReadGuard,
        CategoryAttestation,
        OpenEMRLiveGateway,
    )

    marker = "document:synthetic:source:v1-source.pdf"
    calls: list[tuple[str, str, dict[str, str]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, dict(request.url.params)))
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if request.url.path.endswith(
            f"/api/patient/{STANDARD_PATIENT_ID}/document"
        ):
            return httpx.Response(200, json=[{"id": "42", "filename": marker}])
        if request.url.path.endswith("/fhir/DocumentReference"):
            return httpx.Response(
                200,
                json=_bundle(
                    {
                        "resourceType": "DocumentReference",
                        "id": "document-reference-uuid",
                        "subject": {"reference": f"Patient/{PATIENT_ID}"},
                        "content": [
                            {
                                "attachment": {
                                    "title": marker,
                                    "url": f"{BASE_URL}/fhir/Binary/document-binary-uuid",
                                }
                            }
                        ],
                    }
                ),
            )
        if request.url.path.endswith("/fhir/Binary/document-binary-uuid"):
            return httpx.Response(200, content=b"%PDF-persisted-synthetic")
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = OpenEMRLiveGateway(
            base_url=BASE_URL,
            principal=_principal(),
            category_attestations=(
                CategoryAttestation("/AI-Source-Documents", "17", True),
            ),
            legacy_route_attestation=_legacy_attestation(),
            binary_guard=BinaryReadGuard("WARNING"),
            http_client=client,
        )
        documents = await gateway.list_documents(
            patient_id=PATIENT_ID, category_path="/AI-Source-Documents"
        )
        assert documents == [DocumentRecord("42", marker)]
        assert (
            await gateway.read_document_bytes(patient_id=PATIENT_ID, remote_id="42")
            == b"%PDF-persisted-synthetic"
        )

    assert calls == [
        (
            "GET",
            f"/apis/default/api/patient/{STANDARD_PATIENT_ID}/document",
            {"path": "/AI-Source-Documents"},
        ),
        (
            "GET",
            "/apis/default/fhir/DocumentReference",
            {"patient": PATIENT_ID, "_count": "100"},
        ),
        ("GET", "/apis/default/fhir/Binary/document-binary-uuid", {}),
    ]


@pytest.mark.asyncio
async def test_document_readback_uses_patient_list_bound_numeric_binary_fallback():
    """OpenEMR's Binary controller accepts the numeric ID from its patient list.

    The deployed fork currently returns an empty DocumentReference bundle for regular
    patient documents because its internal patient search field is mis-keyed.  The
    fallback remains patient-bound: the numeric ID must first be cached by the attested
    patient/category standard list, and bytes are still read through the guarded FHIR
    Binary endpoint under the delegated principal.
    """

    from app.writeback.live_gateway import (
        BinaryReadGuard,
        CategoryAttestation,
        OpenEMRLiveGateway,
    )

    marker = "document:synthetic:source:v1-source.pdf"
    calls: list[tuple[str, str, dict[str, str]]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, dict(request.url.params)))
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if request.url.path.endswith(
            f"/api/patient/{STANDARD_PATIENT_ID}/document"
        ):
            return httpx.Response(200, json=[{"id": "42", "filename": marker}])
        if request.url.path.endswith("/fhir/DocumentReference"):
            return httpx.Response(200, json=_bundle())
        if request.url.path.endswith("/fhir/Binary/42"):
            return httpx.Response(200, content=b"%PDF-persisted-synthetic")
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = OpenEMRLiveGateway(
            base_url=BASE_URL,
            principal=_principal(),
            category_attestations=(
                CategoryAttestation("/AI-Source-Documents", "17", True),
            ),
            legacy_route_attestation=_legacy_attestation(),
            binary_guard=BinaryReadGuard("WARNING"),
            http_client=client,
        )
        documents = await gateway.list_documents(
            patient_id=PATIENT_ID, category_path="/AI-Source-Documents"
        )
        assert documents == [DocumentRecord("42", marker)]
        assert (
            await gateway.read_document_bytes(patient_id=PATIENT_ID, remote_id="42")
            == b"%PDF-persisted-synthetic"
        )

    assert calls == [
        (
            "GET",
            f"/apis/default/api/patient/{STANDARD_PATIENT_ID}/document",
            {"path": "/AI-Source-Documents"},
        ),
        (
            "GET",
            "/apis/default/fhir/DocumentReference",
            {"patient": PATIENT_ID, "_count": "100"},
        ),
        ("GET", "/apis/default/fhir/Binary/42", {}),
    ]


@pytest.mark.asyncio
async def test_document_numeric_binary_fallback_rejects_uncached_or_noncanonical_ids():
    from app.writeback.live_gateway import (
        BinaryReadGuard,
        CategoryAttestation,
        OpenEMRLiveGateway,
    )

    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith(
            f"/api/patient/{STANDARD_PATIENT_ID}/document"
        ):
            return httpx.Response(
                200, json=[{"id": "0042", "filename": "synthetic.pdf"}]
            )
        if request.url.path.endswith("/fhir/DocumentReference"):
            return httpx.Response(200, json=_bundle())
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = OpenEMRLiveGateway(
            base_url=BASE_URL,
            principal=_principal(),
            category_attestations=(
                CategoryAttestation("/AI-Source-Documents", "17", True),
            ),
            legacy_route_attestation=_legacy_attestation(),
            binary_guard=BinaryReadGuard("WARNING"),
            http_client=client,
        )
        assert (
            await gateway.read_document_bytes(patient_id=PATIENT_ID, remote_id="42")
            is None
        )
        assert await gateway.list_documents(
            patient_id=PATIENT_ID, category_path="/AI-Source-Documents"
        ) == [DocumentRecord("0042", "synthetic.pdf")]
        assert (
            await gateway.read_document_bytes(patient_id=PATIENT_ID, remote_id="0042")
            is None
        )

    # The cached noncanonical ID may participate in normal DocumentReference
    # discovery, but it can never be interpolated into the numeric fallback URL.
    assert calls == [
        f"/apis/default/api/patient/{STANDARD_PATIENT_ID}/document",
        "/apis/default/fhir/DocumentReference",
    ]


@dataclass
class _SourceGateway:
    documents: list[DocumentRecord]
    contents: dict[str, bytes | None]

    async def list_documents(self, *, patient_id: str, category_path: str):
        assert patient_id == PATIENT_ID
        assert category_path == "/AI-Source-Documents"
        return self.documents

    async def read_document_bytes(self, *, patient_id: str, remote_id: str):
        assert patient_id == PATIENT_ID
        return self.contents[remote_id]


@pytest.mark.asyncio
async def test_source_loader_requires_exact_marker_hash_and_unique_match():
    from app.ingestion.repository import InMemoryDocumentRepository, NewDocument
    from app.writeback.source_loader import (
        OpenEMRSourceLoader,
        SourceDocumentUnavailable,
    )

    content = b"%PDF-persisted-source"
    repository = InMemoryDocumentRepository()
    record, _ = await repository.get_or_create(
        NewDocument(
            patient_id=PATIENT_ID,
            content_hash=hashlib.sha256(content).hexdigest(),
            doc_type="lab_pdf",
            filename="source.pdf",
            content_type="application/pdf",
            encounter_id=None,
            correlation_id="correlation-synthetic",
            credential_ref="credential:synthetic",
        )
    )
    marker = f"document:{record.document_id}:source:v1"
    gateway = _SourceGateway(
        documents=[
            DocumentRecord("match", f"{marker}-source.pdf"),
            DocumentRecord("wrong-hash", f"{marker}-other.pdf"),
            DocumentRecord("collision", f"{marker}extra-source.pdf"),
        ],
        contents={
            "match": content,
            "wrong-hash": b"different",
            "collision": content,
        },
    )
    loader = OpenEMRSourceLoader(gateway, category_path="/AI-Source-Documents")

    assert await loader.fetch(record) == content

    gateway.documents.append(DocumentRecord("duplicate", f"{marker}-duplicate.pdf"))
    gateway.contents["duplicate"] = content
    with pytest.raises(SourceDocumentUnavailable):
        await loader.fetch(record)


def _vital_note(marker: str, payload_hash: str) -> str:
    return f"copilot-intent:{marker};payload:{payload_hash}"


def _vital_resources(note: str, *, weight: float = 180.5) -> dict[str, object]:
    return _bundle(
        {
            "resourceType": "Observation",
            "id": "panel-uuid",
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "encounter": {"reference": f"Encounter/{ENCOUNTER_ID}"},
            "code": {"coding": [{"code": "85353-1"}]},
            "note": [{"text": note}],
            "hasMember": [{"reference": "Observation/weight-uuid"}],
        },
        {
            "resourceType": "Observation",
            "id": "weight-uuid",
            "subject": {"reference": f"Patient/{PATIENT_ID}"},
            "encounter": {"reference": f"Encounter/{ENCOUNTER_ID}"},
            "code": {"coding": [{"code": "29463-7"}]},
            "effectiveDateTime": "2026-07-14T12:00:00+00:00",
            "valueQuantity": {"value": weight, "unit": "lb_av"},
        },
    )


@pytest.mark.asyncio
async def test_vital_create_list_and_standard_fhir_readback_use_exact_full_hash(caplog):
    from app.writeback.live_gateway import (
        BinaryReadGuard,
        OpenEMRLiveGateway,
        vital_payload_hash,
    )
    from app.writeback.gateway import VitalReadback, VitalRecord

    marker = "correlation-synthetic"
    payload = {
        "weight": "180.500",
        "date": "2026-07-14T12:00:00+00:00",
        "note": f"copilot-intent:{marker};payload:pending-prefix",
        "user": "spoofed-user",
        "group": "spoofed-group",
        "author": "spoofed-author",
    }
    payload_hash = vital_payload_hash(payload)
    payload["note"] = _vital_note(marker, payload_hash[:12])
    stored_note = _vital_note(marker, payload_hash)
    standard_row = {
        "id": "77",
        "date": "2026-07-14 12:00:00",
        "weight": "180.500000",
        "note": stored_note,
    }
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if request.method == "POST" and path.endswith("/vital"):
            assert path.endswith(
                f"/api/patient/{STANDARD_PATIENT_ID}/encounter/"
                f"{STANDARD_ENCOUNTER_ID}/vital"
            )
            sent = json.loads((await request.aread()).decode())
            assert sent == {
                "weight": "180.500",
                "date": "2026-07-14T12:00:00+00:00",
                "note": stored_note,
            }
            return httpx.Response(201, json={"vid": "77", "fid": "88"})
        if request.method == "GET" and path.endswith("/vital"):
            assert path.endswith(
                f"/api/patient/{STANDARD_PATIENT_ID}/encounter/"
                f"{STANDARD_ENCOUNTER_ID}/vital"
            )
            return httpx.Response(
                200,
                json=[
                    standard_row,
                    {**standard_row, "id": "prefix-only", "note": payload["note"]},
                    {**standard_row, "id": "wrong-value", "weight": "181.0"},
                ],
            )
        if request.method == "GET" and path.endswith("/vital/77"):
            assert path.endswith(
                f"/api/patient/{STANDARD_PATIENT_ID}/encounter/"
                f"{STANDARD_ENCOUNTER_ID}/vital/77"
            )
            return httpx.Response(200, json=standard_row)
        if path.endswith("/fhir/Observation"):
            return httpx.Response(200, json=_vital_resources(stored_note))
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = OpenEMRLiveGateway(
            base_url=BASE_URL,
            principal=_principal(),
            category_attestations=(),
            legacy_route_attestation=_legacy_attestation(),
            binary_guard=BinaryReadGuard("WARNING"),
            http_client=client,
        )
        assert (
            await gateway.create_vital(
                patient_id=PATIENT_ID,
                encounter_id=ENCOUNTER_ID,
                payload=payload,
            )
            == "77"
        )
        assert await gateway.list_vitals(
            patient_id=PATIENT_ID, encounter_id=ENCOUNTER_ID
        ) == [VitalRecord("77", stored_note, payload_hash)]
        assert await gateway.read_vital(
            patient_id=PATIENT_ID,
            encounter_id=ENCOUNTER_ID,
            remote_id="77",
        ) == VitalReadback(
            remote_id="77",
            note=stored_note,
            standard_payload_hash=payload_hash,
            fhir_payload_hash=payload_hash,
        )

    assert all(
        request.headers["authorization"] == f"Bearer {TOKEN}" for request in requests
    )
    assert TOKEN not in caplog.text
    assert "180.5" not in caplog.text


@pytest.mark.asyncio
async def test_vital_create_rejects_a_marker_hash_mismatch_before_http():
    from app.writeback.live_gateway import BinaryReadGuard, OpenEMRLiveGateway

    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = OpenEMRLiveGateway(
            base_url=BASE_URL,
            principal=_principal(),
            category_attestations=(),
            legacy_route_attestation=_legacy_attestation(),
            binary_guard=BinaryReadGuard("WARNING"),
            http_client=client,
        )
        with pytest.raises(OpenEMRWriteError):
            await gateway.create_vital(
                patient_id=PATIENT_ID,
                encounter_id=ENCOUNTER_ID,
                payload={
                    "weight": "180.5",
                    "date": "2026-07-14T12:00:00+00:00",
                    "note": "copilot-intent:correlation-synthetic;payload:deadbeefdead",
                },
            )

    assert calls == 0


@pytest.mark.asyncio
async def test_vital_route_rejects_unattested_encounter_before_http():
    from app.writeback.live_gateway import (
        BinaryReadGuard,
        LiveGatewayError,
        OpenEMRLiveGateway,
    )

    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = OpenEMRLiveGateway(
            base_url=BASE_URL,
            principal=_principal(),
            category_attestations=(),
            legacy_route_attestation=_legacy_attestation(
                encounter_uuid="44444444-4444-4444-8444-444444444444"
            ),
            binary_guard=BinaryReadGuard("WARNING"),
            http_client=client,
        )
        with pytest.raises(LiveGatewayError, match="encounter mapping"):
            await gateway.list_vitals(
                patient_id=PATIENT_ID,
                encounter_id=ENCOUNTER_ID,
            )

    assert calls == 0
