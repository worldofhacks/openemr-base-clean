"""Synthetic lab-PDF upload-to-composition integration (W2-D2/D3/D6/D10; §2/§3/§5)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.ingestion.reader import WordsBoxes
from app.llm.provider import Usage
from app.orchestrator.loop import BriefResult
from app.schemas.extraction import GroundedField, LabPdfExtraction, LabResult
from app.session.store import Session

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "evals"
    / "fixtures"
    / "golden"
    / "lab-clean-glucose.pdf"
)


async def _return(value):
    return value


class _VerifiedTransport:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.posts: list[object] = []

    async def discover(self, _intent):
        return []

    async def post(self, intent, payload):
        self.posts.append(payload)
        return f"{self.prefix}-{intent.field_id}"

    async def verify(self, _intent, _match, _payload_hash):
        return True


class _SourceLoader:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls = 0

    async def fetch(self, _record) -> bytes:
        self.calls += 1
        return self.content


def _proposal(value: object) -> GroundedField:
    return GroundedField(value=value, page=1, grounded=False, citation=None)


class _StrictGlucoseVlm:
    """Deterministic proposal only; the local verifier owns grounding and citations."""

    def __init__(self) -> None:
        self.calls = 0

    async def extract(
        self,
        *,
        doc_type,
        source: bytes,
        words_boxes: WordsBoxes,
        source_document_id: str,
    ) -> LabPdfExtraction:
        self.calls += 1
        assert doc_type == "lab_pdf"
        assert source.startswith(b"%PDF-")
        assert len(words_boxes.pages) == 1
        assert words_boxes.pages[0].source == "text_layer"
        assert not words_boxes.pages[0].unreadable
        assert {word.text for word in words_boxes.pages[0].words}.issuperset(
            {"Glucose", "92", "mg/dL", "70-99", "2026-06-01", "N"}
        )
        return LabPdfExtraction(
            results=[
                LabResult(
                    test_name=_proposal("Glucose"),
                    value=_proposal("92"),
                    unit=_proposal("mg/dL"),
                    reference_range=_proposal("70-99"),
                    collection_date=_proposal(date(2026, 6, 1)),
                    abnormal_flag=_proposal("N"),
                )
            ],
            source_document_id=source_document_id,
        )


class _GuidelineRetriever:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, query: str, *, k: int, demographic_strings=()):
        from corpus.retrieval import EvidenceHit, RetrievalOutcome

        self.calls += 1
        assert query == "diabetes glucose"
        assert k == 1
        assert demographic_strings == ()
        manifest_hash = "a" * 64
        corpus_version = f"vadod-cpg-trio@{manifest_hash}"
        return RetrievalOutcome(
            items=(
                EvidenceHit(
                    source_id=f"vadod-diabetes-2023@{manifest_hash}",
                    section="Recommendations: Glycemic Targets",
                    chunk_id="diabetes-glycemic-targets-001",
                    quote="Use individualized glycemic targets for adults with diabetes.",
                    score=0.97,
                    corpus_version=corpus_version,
                ),
            ),
            corpus_version=corpus_version,
            manifest_hash=manifest_hash,
            degraded_reasons=(),
        )


def _session() -> Session:
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    return Session(
        session_id="session-lab-e2e",
        clinician_sub="clinician-synthetic",
        patient_id="patient-synthetic",
        created_at=now,
        last_activity_at=now,
        token_expires_at=now + timedelta(hours=1),
        idle_timeout_s=1800,
        turn_cap=20,
    )


async def _brief() -> BriefResult:
    return BriefResult(
        text="Existing verified chart brief.",
        source="llm",
        degraded=False,
        usage=Usage(),
        iterations=1,
        verdicts=["pass"],
    )


@pytest.mark.asyncio
async def test_synthetic_lab_pdf_full_path_renders_grounded_and_guideline_evidence():
    from app.ingestion.artifacts import InMemoryArtifactStore
    from app.ingestion.pipeline import DocumentExtractionPipeline
    from app.ingestion.processor import DocumentProcessor
    from app.ingestion.repository import InMemoryDocumentRepository
    from app.ingestion.service import DocumentCoordinator
    from app.ingestion.uploads import validate_upload
    from app.orchestrator.graph import run_graph_turn
    from app.orchestrator.refs import CompositeRefResolver, TurnRefRegistry
    from app.orchestrator.workers.evidence_retriever import build_evidence_worker
    from app.orchestrator.workers.extraction_adapter import build_extraction_worker
    from app.schemas.citations import CitationSourceType, CitationV2
    from app.schemas.extraction import ExtractionArtifact
    from app.schemas.retrieval import EvidenceSearchRequest
    from app.schemas.workers import WorkerInput
    from app.writeback.intents import ExactlyOnceWriter, InMemoryIntentRepository

    content = _FIXTURE.read_bytes()
    upload = validate_upload(
        filename=_FIXTURE.name,
        content_type="application/pdf",
        data=content,
        doc_type="lab_pdf",
    )
    documents = InMemoryDocumentRepository()
    source_transport = _VerifiedTransport("source")
    coordinator = DocumentCoordinator(
        repository=documents,
        source_writer=ExactlyOnceWriter(InMemoryIntentRepository(), source_transport),
        encounter_belongs_to_patient=lambda _patient, _encounter: _return(True),
        credential_ref_for_session=lambda _session: _return("credential:synthetic"),
    )
    session = _session()

    submission = await coordinator.submit(
        session,
        upload,
        encounter_id=None,
        correlation_id="corr-lab-upload",
    )
    assert submission.accepted.state == "queued"
    queued = await coordinator.status(session, submission.accepted.document_id)
    assert queued.state == "queued"

    vlm = _StrictGlucoseVlm()
    artifacts = InMemoryArtifactStore()
    artifact_transport = _VerifiedTransport("artifact")
    source_loader = _SourceLoader(content)
    pipeline = DocumentExtractionPipeline(
        repository=documents,
        source_loader=source_loader,
        vlm_extractor=vlm,
        artifact_writer=ExactlyOnceWriter(
            InMemoryIntentRepository(), artifact_transport
        ),
        vital_writer=None,
        artifact_store=artifacts,
        agent_version="test-lab-e2e",
    )
    processor = DocumentProcessor(
        repository=documents,
        pipeline=pipeline,
        worker_id="worker-lab-e2e",
        lease_seconds=30,
        max_attempts=3,
        base_backoff_seconds=5,
    )

    processed = await processor.process_once()
    assert processed is not None
    complete = await coordinator.status(session, submission.accepted.document_id)
    assert complete.state == "complete"
    assert (complete.fields_grounded, complete.fields_unsupported) == (6, 0)
    assert vlm.calls == 1
    assert source_loader.calls == 1
    assert len(source_transport.posts) == len(artifact_transport.posts) == 1

    persisted = await artifacts.refs_for_document(complete.document_id)
    assert persisted is not None
    artifact = artifacts.resolve(persisted.artifact_ref)
    assert isinstance(artifact, ExtractionArtifact)
    assert artifact.grounding_summary == {
        "fields_grounded": 6,
        "fields_unsupported": 0,
    }
    assert len(persisted.citation_refs) == 6
    persisted_citations = [artifacts.resolve(ref) for ref in persisted.citation_refs]
    assert all(isinstance(citation, CitationV2) for citation in persisted_citations)
    assert all(citation.page_or_section == "1" for citation in persisted_citations)

    turn_refs = TurnRefRegistry("corr-lab-graph")
    refs = CompositeRefResolver(turn_refs, artifacts)
    evidence_ref = refs.put(
        EvidenceSearchRequest(query="diabetes; glucose", k=1),
        kind="evidence-request",
    )
    retriever = _GuidelineRetriever()
    worker_input = WorkerInput(
        correlation_id="corr-lab-graph",
        turn=0,
        patient_ref="patient:patient-synthetic",
        document_refs=[complete.document_id],
        evidence_refs=[evidence_ref],
        request_kind="clinical_question",
    )

    result = await run_graph_turn(
        run_brief=_brief,
        correlation_id="corr-lab-graph",
        worker_input=worker_input,
        extraction_worker=build_extraction_worker(pipeline),
        retrieval_worker=build_evidence_worker(retriever, refs),
        ref_registry=refs,
    )

    assert vlm.calls == 1  # graph reused the persisted artifact; no VLM rerun
    assert len(source_transport.posts) == len(artifact_transport.posts) == 1
    assert retriever.calls == 1
    assert [record.supervisor_decision.value for record in result.handoffs] == [
        "route_extract",
        "route_retrieve",
        "compose_answer",
        "review_critic",
        "critic_approve",
        "done",
    ]

    document_claims = result.composition.for_source(
        CitationSourceType.UPLOADED_DOCUMENT
    )
    assert len(document_claims) == 6
    glucose_value = next(
        claim
        for claim in document_claims
        if claim.citation.field_or_chunk_id == "results[0].value"
    )
    assert glucose_value.text == "results.0.value: 92"
    assert glucose_value.citation.page_or_section == "1"
    assert glucose_value.overlay is not None
    assert glucose_value.overlay.page == 1
    assert glucose_value.overlay.bbox == artifact.extraction.results[0].value.bbox

    guideline_claims = result.composition.for_source(CitationSourceType.GUIDELINE)
    assert len(guideline_claims) == 1
    assert guideline_claims[0].citation.page_or_section == (
        "Recommendations: Glycemic Targets"
    )
    assert guideline_claims[0].text == (
        "Use individualized glycemic targets for adults with diabetes."
    )
