"""Mounted upload-to-answer runtime proof (W2-D2/D3/D6/D9/D10; §2/§3/§5)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from app.ingestion.reader import WordsBoxes
from app.llm.provider import Usage
from app.middleware.correlation import correlation_id_var
from app.orchestrator.graph import GraphTurnResult
from app.orchestrator.loop import BriefResult
from app.schemas.extraction import (
    Demographics,
    GroundedField,
    IntakeFormExtraction,
    IntakeVitals,
    LabPdfExtraction,
    LabResult,
    VitalCandidate,
)
from app.session.store import Session
from app.writeback.intents import RemoteMatch
from app.writeback.preflight import CategoryResolution
from app.writeback.transports import DocumentWritePayload


_LAB_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "evals"
    / "fixtures"
    / "golden"
    / "lab-clean-glucose.pdf"
)
_INTAKE_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "evals"
    / "fixtures"
    / "golden"
    / "intake-bps-out-of-range-skipped.pdf"
)
_CORPUS = Path(__file__).resolve().parents[1] / "corpus"
_TARGET_CHUNK = "vadod-diabetes-2023:p0025:c002:355ee100856b"


def _proposal(value: object) -> GroundedField:
    return GroundedField(value=value, page=1, grounded=False, citation=None)


class _AnthropicBoundary:
    """Deterministic external-model boundary; local grounding remains production code."""

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


class _IntakeAnthropicBoundary:
    """Strict proposal matching the synthetic range-boundary golden intake."""

    def __init__(self) -> None:
        self.calls = 0

    async def extract(
        self,
        *,
        doc_type,
        source: bytes,
        words_boxes: WordsBoxes,
        source_document_id: str,
    ) -> IntakeFormExtraction:
        self.calls += 1
        assert doc_type == "intake_form"
        assert source.startswith(b"%PDF-")
        assert len(words_boxes.pages) == 1
        assert words_boxes.pages[0].source == "text_layer"
        assert {word.text for word in words_boxes.pages[0].words}.issuperset(
            {"401", "82", "mmHg", "2026-06-22T08:00:00Z"}
        )
        measured_at = datetime(2026, 6, 22, 8, 0, tzinfo=timezone.utc)
        return IntakeFormExtraction(
            demographics=Demographics(
                name=_proposal("ZZPHI-intake-bps-out-of-range-skipped"),
                dob=_proposal(date(1987, 4, 12)),
                sex=_proposal("X"),
                contact=_proposal("synthetic-contact@example.invalid"),
            ),
            chief_concern=_proposal("Routine intake review."),
            current_medications=[_proposal("Vitamin D 1000 IU daily")],
            allergies=[_proposal("Latex - contact rash")],
            family_history=_proposal("Sibling: asthma."),
            vitals=IntakeVitals(
                bps=VitalCandidate(
                    value=_proposal(Decimal("401")),
                    unit=_proposal("mmHg"),
                    measurement_date=_proposal(measured_at),
                ),
                bpd=VitalCandidate(
                    value=_proposal(Decimal("82")),
                    unit=_proposal("mmHg"),
                    measurement_date=_proposal(measured_at),
                ),
            ),
            source_document_id=source_document_id,
        )


class _OpenEmrBoundary:
    """In-memory fake only at the delegated OpenEMR document API boundary."""

    categories = {
        "/AI-Source-Documents": "category-source",
        "/AI-Extractions": "category-artifact",
    }

    def __init__(self) -> None:
        self._documents: dict[str, dict[str, object]] = {}
        self._vitals: dict[str, dict[str, object]] = {}
        self.posts_by_category: dict[str, int] = {path: 0 for path in self.categories}
        self.vital_posts: list[dict[str, object]] = []

    async def resolve_category(self, path: str) -> CategoryResolution:
        return CategoryResolution(
            path=path,
            category_id=self.categories[path],
            writable=True,
        )

    async def find_documents(
        self, *, patient_id: str, marker: str, payload_hash: str
    ) -> list[RemoteMatch]:
        return [
            RemoteMatch(remote_id=remote_id, payload_hash=payload_hash)
            for remote_id, row in self._documents.items()
            if row["patient_id"] == patient_id
            and row["marker"] == marker
            and row["payload_hash"] == payload_hash
        ]

    async def create_document(
        self,
        *,
        patient_id: str,
        category_path: str,
        marker: str,
        payload: DocumentWritePayload,
    ) -> str:
        payload_hash = hashlib.sha256(payload.content).hexdigest()
        remote_id = f"remote-{len(self._documents) + 1}"
        self._documents[remote_id] = {
            "patient_id": patient_id,
            "category_path": category_path,
            "marker": marker,
            "payload_hash": payload_hash,
            "payload": payload,
        }
        self.posts_by_category[category_path] += 1
        return remote_id

    async def verify_document(
        self, *, patient_id: str, remote_id: str, payload_hash: str
    ) -> bool:
        row = self._documents.get(remote_id)
        return bool(
            row
            and row["patient_id"] == patient_id
            and row["payload_hash"] == payload_hash
        )

    async def fetch(self, record) -> bytes:
        matches = [
            row
            for row in self._documents.values()
            if row["patient_id"] == record.patient_id
            and row["category_path"] == "/AI-Source-Documents"
            and row["payload_hash"] == record.content_hash
        ]
        assert len(matches) == 1
        payload = matches[0]["payload"]
        assert isinstance(payload, DocumentWritePayload)
        return payload.content

    async def find_vitals(
        self, *, patient_id: str, marker: str, payload_hash: str
    ) -> list[RemoteMatch]:
        return [
            RemoteMatch(remote_id=remote_id, payload_hash=payload_hash)
            for remote_id, row in self._vitals.items()
            if row["patient_id"] == patient_id
            and row["marker"] == marker
            and row["payload_hash"] == payload_hash
        ]

    async def create_vital(self, *, patient_id: str, marker: str, payload) -> str:
        values = dict(payload.values)
        clinical = {key: value for key, value in values.items() if key != "note"}
        payload_hash = hashlib.sha256(
            json.dumps(clinical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        remote_id = f"vital-{len(self._vitals) + 1}"
        self._vitals[remote_id] = {
            "patient_id": patient_id,
            "marker": marker,
            "payload_hash": payload_hash,
            "values": values,
        }
        self.vital_posts.append(values)
        return remote_id

    async def verify_vital(
        self, *, patient_id: str, remote_id: str, payload_hash: str
    ) -> bool:
        row = self._vitals.get(remote_id)
        return bool(
            row
            and row["patient_id"] == patient_id
            and row["payload_hash"] == payload_hash
        )


class _CommittedQueryEmbedding:
    """Use a committed corpus vector while keeping model downloads outside the test."""

    def __init__(self, corpus_dir: Path, chunk_id: str) -> None:
        chunks = [
            json.loads(line)
            for line in (corpus_dir / "chunks.jsonl").read_text().splitlines()
            if line
        ]
        metadata = json.loads((corpus_dir / "index" / "metadata.json").read_text())
        dimension = int(metadata["dense"]["dimension"])
        matrix = np.fromfile(corpus_dir / "index" / "dense.f32", dtype=np.float32)
        matrix = matrix.reshape(len(chunks), dimension)
        index = next(
            index for index, chunk in enumerate(chunks) if chunk["chunk_id"] == chunk_id
        )
        self._vector = matrix[index].copy()

    def query_vector(self, _query: str):
        return self._vector.copy()


def _session() -> Session:
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    return Session(
        session_id="session-route-runtime",
        clinician_sub="clinician-synthetic",
        patient_id="patient-synthetic",
        created_at=now,
        last_activity_at=now,
        token_expires_at=now + timedelta(hours=1),
        idle_timeout_s=1800,
        turn_cap=20,
    )


class _RuntimeServices:
    """Production B2/B3 composition with only remote systems replaced by fakes."""

    def __init__(self, *, vlm=None) -> None:
        from app.ingestion.artifacts import InMemoryArtifactStore
        from app.ingestion.pipeline import DocumentExtractionPipeline
        from app.ingestion.processor import DocumentProcessor
        from app.ingestion.repository import InMemoryDocumentRepository
        from app.ingestion.service import DocumentCoordinator
        from app.writeback.intents import ExactlyOnceWriter, InMemoryIntentRepository
        from app.writeback.preflight import CategoryExpectation
        from app.writeback.transports import (
            ExtractionArtifactTransport,
            SourceDocumentTransport,
            VitalIntentTransport,
        )
        from corpus.retrieval import HybridRetriever, RerankerSeam

        self.session = _session()
        self.openemr = _OpenEmrBoundary()
        self.vlm = vlm or _AnthropicBoundary()
        self.repository = InMemoryDocumentRepository()
        self.artifacts = InMemoryArtifactStore()
        intents = InMemoryIntentRepository()
        source_transport = SourceDocumentTransport(
            self.openemr,
            category=CategoryExpectation(
                path="/AI-Source-Documents", category_id="category-source"
            ),
        )
        artifact_transport = ExtractionArtifactTransport(
            self.openemr,
            category=CategoryExpectation(
                path="/AI-Extractions", category_id="category-artifact"
            ),
        )
        self.documents = DocumentCoordinator(
            repository=self.repository,
            source_writer=ExactlyOnceWriter(intents, source_transport),
            encounter_belongs_to_patient=self._encounter_belongs,
            credential_ref_for_session=self._credential_ref,
        )
        self.pipeline = DocumentExtractionPipeline(
            repository=self.repository,
            source_loader=self.openemr,
            vlm_extractor=self.vlm,
            artifact_writer=ExactlyOnceWriter(intents, artifact_transport),
            vital_writer=ExactlyOnceWriter(intents, VitalIntentTransport(self.openemr)),
            artifact_store=self.artifacts,
            agent_version="route-e2e",
        )
        self.processor = DocumentProcessor(
            repository=self.repository,
            pipeline=self.pipeline,
            worker_id="worker-route-e2e",
            lease_seconds=30,
            max_attempts=3,
            base_backoff_seconds=5,
        )
        self.retriever = HybridRetriever(
            _CORPUS,
            dense_embedder=_CommittedQueryEmbedding(_CORPUS, _TARGET_CHUNK),
            reranker=RerankerSeam(mode="local", local=None),
        )
        self.last_graph_result: GraphTurnResult | None = None

    async def _encounter_belongs(self, _patient: str, _encounter: str) -> bool:
        return True

    async def _credential_ref(self, _session: Session) -> str:
        return "delegated-session:synthetic"

    async def resolve_session(self, session_id: str) -> Session:
        assert session_id == self.session.session_id
        return self.session

    async def run_brief(
        self, _session: Session, _message: str, *, request_url: str
    ) -> BriefResult:
        assert request_url.startswith("http://testserver/chat")
        return BriefResult(
            text="Verified synthetic chart brief.",
            source="llm",
            degraded=False,
            usage=Usage(),
            iterations=1,
            verdicts=["pass"],
        )

    async def run_graph_turn(
        self, session: Session, message: str, *, request_url: str
    ) -> GraphTurnResult:
        from app.orchestrator.graph import run_graph_turn
        from app.orchestrator.refs import CompositeRefResolver, TurnRefRegistry
        from app.orchestrator.workers.evidence_retriever import build_evidence_worker
        from app.orchestrator.workers.extraction_adapter import build_extraction_worker
        from app.schemas.retrieval import EvidenceSearchRequest
        from app.schemas.workers import WorkerInput

        completed = await self.repository.list_for_patient(
            session.patient_id, state="complete"
        )
        document_refs = [record.document_id for record in completed]
        await self.artifacts.warm_for_documents(document_refs)
        correlation_id = correlation_id_var.get()
        turn_refs = TurnRefRegistry(correlation_id)
        refs = CompositeRefResolver(turn_refs, self.artifacts)
        evidence_ref = refs.put(
            EvidenceSearchRequest(query=message, k=3), kind="evidence-request"
        )

        async def run_brief_pinned() -> BriefResult:
            return await self.run_brief(session, message, request_url=request_url)

        async def run_brief_with_context(answer_context) -> BriefResult:
            # Mirror the observed runtime omission: the typed answer selects exact
            # patient-document claims but forgets the separate guideline selector.  The
            # composer must retain one canonical top-ranked guideline for this anchored,
            # in-scope answer without turning unrelated context into an evidence dump.
            if "blood pressure" in message.lower():
                selected = tuple(
                    claim
                    for claim in answer_context.document_claims
                    if claim.citation.field_or_chunk_id == "vitals.bpd.value"
                )
            else:
                selected = tuple(
                    claim
                    for claim in answer_context.document_claims
                    if claim.citation.field_or_chunk_id.startswith("results[0].")
                )
            return BriefResult(
                text="Verified uploaded-document evidence is provided below.",
                source="llm",
                degraded=False,
                usage=Usage(),
                iterations=1,
                verdicts=[],
                verified_claims=selected,
                answer_reason_code="verified",
            )

        result = await run_graph_turn(
            run_brief=run_brief_pinned,
            run_brief_with_context=run_brief_with_context,
            correlation_id=correlation_id,
            worker_input=WorkerInput(
                correlation_id=correlation_id,
                turn=0,
                patient_ref=f"patient:{session.patient_id}",
                document_refs=document_refs,
                evidence_refs=[evidence_ref],
                request_kind="clinical_question",
            ),
            extraction_worker=build_extraction_worker(self.pipeline),
            retrieval_worker=build_evidence_worker(self.retriever, refs),
            ref_registry=refs,
        )
        self.last_graph_result = result
        return result


def test_mounted_document_runtime_uploads_processes_and_serves_cited_answer(
    complete_env, monkeypatch
):
    """POST route → queue worker → local grounding → graph/composer → cited answer."""

    from app.main import create_app
    from app.schemas.citations import CitationSourceType, CitationV2

    monkeypatch.setenv("W2_GRAPH_ENABLED", "1")
    services = _RuntimeServices()
    app = create_app(services=services, readiness_checks=[])
    content = _LAB_FIXTURE.read_bytes()
    expected_guideline = services.retriever.search("Glucose", k=3).items[0]

    with TestClient(app) as client:
        upload = client.post(
            "/documents",
            data={
                "session_id": services.session.session_id,
                "patient_id": services.session.patient_id,
                "doc_type": "lab_pdf",
            },
            files={"file": (_LAB_FIXTURE.name, content, "application/pdf")},
            headers={"X-Copilot-Request-Id": "corr-route-upload"},
        )
        assert upload.status_code == 202
        accepted = upload.json()
        assert accepted["state"] == "queued"
        assert accepted["correlation_id"] == "corr-route-upload"

        queued = client.get(
            f"/documents/{accepted['document_id']}/status",
            params={"session_id": services.session.session_id},
        )
        assert queued.status_code == 200
        assert queued.json()["state"] == "queued"

        processed = asyncio.run(services.processor.process_once())
        assert processed is not None
        complete = client.get(
            f"/documents/{accepted['document_id']}/status",
            params={"session_id": services.session.session_id},
        )
        assert complete.status_code == 200
        assert complete.json()["state"] == "complete"
        assert (
            complete.json()["fields_grounded"],
            complete.json()["fields_unsupported"],
        ) == (6, 0)

        answer = client.post(
            "/chat",
            json={
                "session_id": services.session.session_id,
                "message": "Glucose",
            },
            headers={"X-Copilot-Request-Id": "corr-route-chat"},
        )

    assert answer.status_code == 200
    body = answer.json()
    assert body["correlation_id"] == "corr-route-chat"
    assert "Uploaded document:" in body["brief"]
    assert "results.0.value: 92" in body["brief"]
    assert "Guideline evidence:" in body["brief"]
    assert expected_guideline.quote in body["brief"]

    citations = [item for item in body["citations"] if isinstance(item, dict)]
    document_citation = next(
        item
        for item in citations
        if item["source_type"] == CitationSourceType.UPLOADED_DOCUMENT.value
        and item["field_or_chunk_id"] == "results[0].value"
    )
    assert document_citation["page_or_section"] == "1"
    assert document_citation["quote_or_value"] == "92"
    guideline_citations = [
        item
        for item in citations
        if item["source_type"] == CitationSourceType.GUIDELINE.value
    ]
    assert len(guideline_citations) == 1
    assert guideline_citations[0] == {
        "source_type": CitationSourceType.GUIDELINE.value,
        "source_id": expected_guideline.source_id,
        "page_or_section": expected_guideline.section,
        "field_or_chunk_id": expected_guideline.chunk_id,
        "quote_or_value": expected_guideline.quote,
    }

    graph_result = services.last_graph_result
    assert graph_result is not None
    assert [record.supervisor_decision.value for record in graph_result.handoffs] == [
        "route_extract",
        "route_retrieve",
        "compose_answer",
        "review_critic",
        "critic_approve",
        "done",
    ]
    document_claim = next(
        claim
        for claim in graph_result.composition.for_source(
            CitationSourceType.UPLOADED_DOCUMENT
        )
        if claim.citation.field_or_chunk_id == "results[0].value"
    )
    assert document_claim.overlay is not None
    assert document_claim.overlay.page == 1
    assert document_claim.overlay.bbox.x0 < document_claim.overlay.bbox.x1
    assert document_claim.overlay.bbox.y0 < document_claim.overlay.bbox.y1
    guideline_claims = graph_result.composition.for_source(
        CitationSourceType.GUIDELINE
    )
    assert len(guideline_claims) == 1
    assert guideline_claims[0].text == expected_guideline.quote
    assert guideline_claims[0].citation == CitationV2.model_validate(
        guideline_citations[0]
    )

    # The graph consumed the durable artifact and exact-once writes; it did not re-extract.
    assert services.vlm.calls == 1
    assert services.openemr.posts_by_category == {
        "/AI-Source-Documents": 1,
        "/AI-Extractions": 1,
    }


def test_mounted_intake_runtime_writes_valid_vital_and_skips_range_violation(
    complete_env, monkeypatch
):
    """A grounded 401 systolic stays artifact-only while valid diastolic is served."""

    from app.main import create_app
    from app.schemas.citations import CitationSourceType
    from app.schemas.extraction import ExtractionArtifact

    monkeypatch.setenv("W2_GRAPH_ENABLED", "1")
    services = _RuntimeServices(vlm=_IntakeAnthropicBoundary())
    app = create_app(services=services, readiness_checks=[])

    with TestClient(app) as client:
        upload = client.post(
            "/documents",
            data={
                "session_id": services.session.session_id,
                "patient_id": services.session.patient_id,
                "doc_type": "intake_form",
                "encounter_id": "encounter-synthetic",
            },
            files={
                "file": (
                    _INTAKE_FIXTURE.name,
                    _INTAKE_FIXTURE.read_bytes(),
                    "application/pdf",
                )
            },
            headers={"X-Copilot-Request-Id": "corr-intake-upload"},
        )
        assert upload.status_code == 202
        document_id = upload.json()["document_id"]
        assert upload.json()["state"] == "queued"

        processed = asyncio.run(services.processor.process_once())
        assert processed is not None
        status = client.get(
            f"/documents/{document_id}/status",
            params={"session_id": services.session.session_id},
        )
        assert status.status_code == 200
        assert status.json()["state"] == "complete"

        answer = client.post(
            "/chat",
            json={
                "session_id": services.session.session_id,
                "message": "hypertension; blood pressure",
            },
            headers={"X-Copilot-Request-Id": "corr-intake-chat"},
        )

    assert answer.status_code == 200
    assert answer.json()["correlation_id"] == "corr-intake-chat"
    assert "vitals.bpd.value: 82" in answer.json()["brief"]
    valid_citation = next(
        item
        for item in answer.json()["citations"]
        if isinstance(item, dict)
        and item["source_type"] == CitationSourceType.UPLOADED_DOCUMENT.value
        and item["field_or_chunk_id"] == "vitals.bpd.value"
    )
    assert valid_citation["page_or_section"] == "1"
    assert valid_citation["quote_or_value"] == "82"

    refs = asyncio.run(services.artifacts.refs_for_document(document_id))
    assert refs is not None
    artifact = services.artifacts.resolve(refs.artifact_ref)
    assert isinstance(artifact, ExtractionArtifact)
    assert isinstance(artifact.extraction, IntakeFormExtraction)
    assert artifact.extraction.vitals.bps is not None
    assert artifact.extraction.vitals.bps.value.value == Decimal("401")
    assert artifact.extraction.vitals.bps.value.grounded is True
    assert artifact.extraction.vitals.bpd is not None
    assert artifact.extraction.vitals.bpd.value.value == Decimal("82")

    # W2-D10: the out-of-range value is retained for review but never reaches Vitals.
    from app.writeback.ranges import build_vital_writes

    mapping = build_vital_writes(
        artifact.extraction.vitals,
        encounter_id="encounter-synthetic",
        correlation_marker=artifact.correlation_id,
    )
    assert [candidate.field_id for candidate in mapping.writes] == ["bpd"]
    assert [
        (candidate.field_id, candidate.reason) for candidate in mapping.skipped
    ] == [("bps", "range_violation")]
    assert len(services.openemr.vital_posts) == 1
    assert "bps" not in services.openemr.vital_posts[0]
    assert str(services.openemr.vital_posts[0]["bpd"]) == "82"

    graph_result = services.last_graph_result
    assert graph_result is not None
    valid_claim = next(
        claim
        for claim in graph_result.composition.for_source(
            CitationSourceType.UPLOADED_DOCUMENT
        )
        if claim.citation.field_or_chunk_id == "vitals.bpd.value"
    )
    assert valid_claim.overlay is not None
    assert valid_claim.overlay.page == 1
    assert valid_claim.overlay.bbox.x0 < valid_claim.overlay.bbox.x1
    assert services.vlm.calls == 1
