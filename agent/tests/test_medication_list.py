"""Medication-list source + grounded-artifact-only extension regressions."""

from __future__ import annotations

from datetime import date
import hashlib
from io import BytesIO

import pytest
from PIL import Image
from pydantic import ValidationError

from app.ingestion.reader import NormBBox, PageWords, Word, WordsBoxes
from app.schemas.extraction import (
    ExtractionArtifact,
    GroundedField,
    LabPdfExtraction,
    MedicationListEntry,
    MedicationListExtraction,
)


def _proposal(value):
    return GroundedField(value=value, page=1, grounded=False, citation=None)


def _extraction(document_id: str) -> MedicationListExtraction:
    return MedicationListExtraction(
        medications=[
            MedicationListEntry(
                medication_name=_proposal("Metformin"),
                strength=_proposal("500mg"),
                dose=_proposal("one"),
                route=_proposal("oral"),
                frequency=_proposal("twice"),
                status=_proposal("active"),
            )
        ],
        as_of_date=_proposal(date(2026, 7, 15)),
        source_document_id=document_id,
    )


def _image(format_name: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (24, 18), "white").save(output, format=format_name)
    return output.getvalue()


@pytest.mark.parametrize(
    ("filename", "content_type", "data"),
    [
        ("list.png", "image/png", _image("PNG")),
        ("list.jpg", "image/jpeg", _image("JPEG")),
    ],
)
def test_medication_list_accepts_existing_image_limits(
    filename: str, content_type: str, data: bytes
) -> None:
    from app.ingestion.uploads import validate_upload

    upload = validate_upload(
        filename=filename,
        content_type=content_type,
        data=data,
        doc_type="medication_list",
    )

    assert upload.doc_type == "medication_list"
    assert upload.page_count == 1


def test_medication_list_accepts_pdf_under_the_existing_page_cap() -> None:
    from pathlib import Path

    from app.ingestion.uploads import validate_upload

    fixture = (
        Path(__file__).resolve().parents[1]
        / "evals"
        / "fixtures"
        / "documents"
        / "clean.pdf"
    )
    upload = validate_upload(
        filename="list.pdf",
        content_type="application/pdf",
        data=fixture.read_bytes(),
        doc_type="medication_list",
    )

    assert upload.page_count <= 20


def test_artifact_v2_is_additive_and_v1_lab_remains_readable() -> None:
    medication = ExtractionArtifact(
        artifact_version=2,
        document_id="document-medication",
        content_hash="a" * 64,
        correlation_id="corr-medication",
        doc_type="medication_list",
        extraction=_extraction("document-medication"),
        grounding_summary={"fields_grounded": 0, "fields_unsupported": 7},
        created_ts="2026-07-15T12:00:00+00:00",
        agent_version="test",
    )
    restored = ExtractionArtifact.model_validate_json(medication.model_dump_json())
    assert restored.artifact_version == 2
    assert isinstance(restored.extraction, MedicationListExtraction)

    legacy = ExtractionArtifact.model_validate(
        {
            "artifact_version": 1,
            "document_id": "document-lab",
            "content_hash": "b" * 64,
            "correlation_id": "corr-lab",
            "doc_type": "lab_pdf",
            "extraction": {
                "results": [],
                "source_document_id": "document-lab",
            },
            "grounding_summary": {
                "fields_grounded": 0,
                "fields_unsupported": 0,
            },
            "created_ts": "2026-07-15T12:00:00+00:00",
            "agent_version": "test",
        }
    )
    assert isinstance(legacy.extraction, LabPdfExtraction)

    with pytest.raises(ValidationError):
        ExtractionArtifact.model_validate(
            medication.model_copy(update={"artifact_version": 1}).model_dump()
        )


class _SourceLoader:
    def __init__(self, content: bytes) -> None:
        self.content = content

    async def fetch(self, _record) -> bytes:
        return self.content


class _Vlm:
    async def extract(self, *, doc_type, source, words_boxes, source_document_id):
        assert doc_type == "medication_list"
        assert source and words_boxes.pages
        return _extraction(source_document_id)


class _VerifiedTransport:
    def __init__(self) -> None:
        self.posts: list[object] = []

    async def discover(self, _intent):
        return []

    async def post(self, intent, payload):
        self.posts.append(payload)
        return f"remote-{intent.field_id}"

    async def verify(self, _intent, _match, _payload_hash):
        return True


def _words(_record, _source: bytes) -> WordsBoxes:
    tokens = ["Metformin", "500mg", "one", "oral", "twice", "active", "2026-07-15"]
    return WordsBoxes(
        pages=[
            PageWords(
                page_index=0,
                source="text_layer",
                render_dpi=200,
                page_pixel_dims=(1000, 1000),
                words=[
                    Word(
                        text=token,
                        bbox=NormBBox(
                            x0=0.05 + index * 0.1,
                            y0=0.1,
                            x1=0.10 + index * 0.1,
                            y1=0.15,
                        ),
                    )
                    for index, token in enumerate(tokens)
                ],
            )
        ]
    )


@pytest.mark.asyncio
async def test_pipeline_persists_only_source_grounded_artifact_and_never_vitals() -> None:
    from app.ingestion.artifacts import InMemoryArtifactStore
    from app.ingestion.pipeline import DocumentExtractionPipeline
    from app.ingestion.repository import InMemoryDocumentRepository, NewDocument
    from app.writeback.intents import ExactlyOnceWriter, InMemoryIntentRepository

    content = b"synthetic medication list bytes"
    repository = InMemoryDocumentRepository()
    record, _ = await repository.get_or_create(
        NewDocument(
            patient_id="patient-synthetic",
            content_hash=hashlib.sha256(content).hexdigest(),
            doc_type="medication_list",
            filename="list.png",
            content_type="image/png",
            encounter_id="encounter-must-not-be-used",
            correlation_id="corr-medication",
            credential_ref="credential-synthetic",
        )
    )
    artifact_transport = _VerifiedTransport()
    vital_transport = _VerifiedTransport()
    artifacts = InMemoryArtifactStore()
    pipeline = DocumentExtractionPipeline(
        repository=repository,
        source_loader=_SourceLoader(content),
        vlm_extractor=_Vlm(),
        artifact_writer=ExactlyOnceWriter(
            InMemoryIntentRepository(), artifact_transport
        ),
        vital_writer=ExactlyOnceWriter(InMemoryIntentRepository(), vital_transport),
        artifact_store=artifacts,
        words_reader=_words,
        agent_version="test-medication-list",
    )

    result = await pipeline.extract_document(
        record.document_id,
        patient_ref="patient:patient-synthetic",
        correlation_id="corr-medication",
    )

    artifact = artifacts.resolve(result.artifact_ref)
    assert isinstance(artifact, ExtractionArtifact)
    assert artifact.artifact_version == 2
    assert artifact.doc_type == "medication_list"
    assert artifact.grounding_summary == {
        "fields_grounded": 7,
        "fields_unsupported": 0,
    }
    assert len(artifact_transport.posts) == 1
    assert vital_transport.posts == []
