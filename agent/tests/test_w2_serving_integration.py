"""Focused B3 serving integration checks (W2-D2/D4/D6; §2/§2a/§5).

The graph flag changes routing, not the W1 HTTP contract.  Retrieval is shared and
lazy, the committed corpus integrity check is a soft readiness dependency, and the
runtime image carries the corpus plus the native ONNX runtime library.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.auth.smart_client import TokenResponse
from app.llm.provider import Usage
from app.orchestrator.graph import GraphTurnResult
from app.orchestrator.composer import (
    BBoxOverlay,
    RenderedClaim,
    VerifiedComposition,
)
from app.orchestrator.loop import BriefResult
from app.orchestrator.refs import TurnRefRegistry
from app.orchestrator.workers.evidence_retriever import build_evidence_worker
from app.schemas.citations import CitationSourceType, CitationV2
from app.schemas.extraction import NormBBox
from app.schemas.retrieval import EvidenceSearchRequest
from app.schemas.workers import WorkerInput
from app.session.store import Session
from corpus.retrieval import EvidenceHit, RetrievalOutcome, RetrievalUnavailableError


def _session() -> Session:
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    return Session(
        session_id="synthetic-session",
        clinician_sub="synthetic-clinician",
        patient_id="synthetic-patient",
        created_at=now,
        last_activity_at=now,
        token_expires_at=now + timedelta(hours=1),
        idle_timeout_s=1800,
        turn_cap=20,
    )


def _brief(text: str) -> BriefResult:
    return BriefResult(
        text=text,
        source="llm",
        degraded=False,
        usage=Usage(),
        iterations=1,
        tool_calls=[],
        verdicts=["pass"],
        citations=["MedicationRequest:synthetic:01234567"],
    )


class _GraphAwareServices:
    def __init__(self, composition: VerifiedComposition | None = None) -> None:
        self.graph_calls = 0
        self.direct_calls = 0
        self.composition = composition or VerifiedComposition()

    async def resolve_session(self, _session_id: str) -> Session:
        return _session()

    async def run_brief(
        self, _session: Session, _message: str, *, request_url: str
    ) -> BriefResult:
        self.direct_calls += 1
        return _brief("direct verified brief")

    async def run_graph_turn(
        self, _session: Session, _message: str, *, request_url: str
    ) -> GraphTurnResult:
        self.graph_calls += 1
        return GraphTurnResult(
            brief=_brief("graph verified brief"),
            handoffs=(),
            composition=self.composition,
        )


def _client(services: object):
    from app.main import create_app

    return TestClient(create_app(services=services, readiness_checks=[]))


def test_graph_flag_routes_json_and_sse_through_services_without_changing_envelopes(
    complete_env, monkeypatch
):
    monkeypatch.setenv("W2_GRAPH_ENABLED", "1")
    services = _GraphAwareServices()
    client = _client(services)

    json_response = client.post(
        "/chat",
        json={"session_id": "synthetic-session", "message": "type 2 diabetes; HbA1c"},
    )
    assert json_response.status_code == 200
    assert set(json_response.json()) == {
        "brief",
        "source",
        "degraded",
        "verdicts",
        "citations",
        "patient",
        "correlation_id",
    }
    assert json_response.json()["brief"] == "graph verified brief"

    stream_response = client.post(
        "/chat",
        json={"session_id": "synthetic-session", "message": "type 2 diabetes; HbA1c"},
        headers={"Accept": "text/event-stream"},
    )
    assert stream_response.status_code == 200
    assert stream_response.headers["content-type"].startswith("text/event-stream")
    assert "graph verified brief" in stream_response.text
    assert services.graph_calls == 2
    assert services.direct_calls == 0


def test_graph_flag_off_never_calls_service_graph(complete_env, monkeypatch):
    monkeypatch.delenv("W2_GRAPH_ENABLED", raising=False)
    services = _GraphAwareServices()

    response = _client(services).post(
        "/chat",
        json={"session_id": "synthetic-session", "message": "type 2 diabetes; HbA1c"},
    )

    assert response.status_code == 200
    assert response.json()["brief"] == "direct verified brief"
    assert services.graph_calls == 0
    assert services.direct_calls == 1


def test_graph_serving_renders_verified_source_classes_and_document_overlay(
    complete_env, monkeypatch
):
    """W2-D3/D6/\u00a75: only composer-approved claims reach JSON/SSE surfaces."""

    monkeypatch.setenv("W2_GRAPH_ENABLED", "1")
    bbox = NormBBox(x0=0.1, y0=0.2, x1=0.3, y1=0.4)
    document_citation = CitationV2(
        source_type="uploaded_document",
        source_id="document:synthetic",
        page_or_section="1",
        field_or_chunk_id="results.0.value",
        quote_or_value="92",
    )
    guideline_citation = CitationV2(
        source_type="guideline",
        source_id="vadod-diabetes@" + "a" * 64,
        page_or_section="Glycemic Targets",
        field_or_chunk_id="vadod-diabetes-targets-001",
        quote_or_value="Use individualized glycemic targets.",
    )
    composition = VerifiedComposition(
        claims=(
            RenderedClaim(
                text="results.0.value: 92",
                citation=document_citation,
                source_class=CitationSourceType.UPLOADED_DOCUMENT,
                overlay=BBoxOverlay(source_id="document:synthetic", page=1, bbox=bbox),
            ),
            RenderedClaim(
                text="Use individualized glycemic targets.",
                citation=guideline_citation,
                source_class=CitationSourceType.GUIDELINE,
            ),
        )
    )
    client = _client(_GraphAwareServices(composition))

    response = client.post(
        "/chat",
        json={"session_id": "synthetic-session", "message": "type 2 diabetes; HbA1c"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "Uploaded document" in body["brief"]
    assert "Guideline evidence" in body["brief"]
    assert any(
        isinstance(citation, dict) and citation["source_type"] == "uploaded_document"
        for citation in body["citations"]
    )

    stream = client.post(
        "/chat",
        json={"session_id": "synthetic-session", "message": "type 2 diabetes; HbA1c"},
        headers={"Accept": "text/event-stream"},
    )
    assert stream.status_code == 200
    assert '"source_class": "uploaded_document"' in stream.text
    assert '"page": 1' in stream.text
    assert '"bbox"' in stream.text


class _FixtureRetriever:
    def search(self, query: str, *, k: int, demographic_strings=()) -> RetrievalOutcome:
        manifest_hash = "a" * 64
        return RetrievalOutcome(
            items=(
                EvidenceHit(
                    source_id=f"vadod-diabetes@{manifest_hash}",
                    section="Glycemic Targets",
                    chunk_id="vadod-diabetes-targets-001",
                    quote="Use individualized glycemic targets.",
                    score=0.91,
                    corpus_version=f"vadod-cpg-trio@{manifest_hash}",
                ),
            ),
            corpus_version=f"vadod-cpg-trio@{manifest_hash}",
            manifest_hash=manifest_hash,
            degraded_reasons=(),
        )


class _EvidenceServices:
    def __init__(self) -> None:
        self.factory_calls = 0

    async def resolve_session(self, session_id: str) -> Session:
        assert session_id == "synthetic-session"
        return _session()

    def get_evidence_retriever(self):
        self.factory_calls += 1
        return _FixtureRetriever()


def test_evidence_router_is_mounted_and_uses_service_lazy_factory(complete_env):
    services = _EvidenceServices()
    client = _client(services)
    assert services.factory_calls == 0

    response = client.post(
        "/evidence/search",
        json={"query": "type 2 diabetes; HbA1c", "k": 1},
        headers={"X-Copilot-Session-Id": "synthetic-session"},
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["chunk_id"] == "vadod-diabetes-targets-001"
    assert services.factory_calls == 1


def test_documents_router_is_mounted_on_the_application(complete_env):
    """W2-D9/\u00a72a: the typed upload surface is part of the deployed app."""

    paths = set(_client(_EvidenceServices()).get("/openapi.json").json()["paths"])

    assert "/documents" in paths
    assert "/documents/{document_id}/status" in paths
    assert "/documents/{document_id}/retry" in paths
    assert "/documents/{document_id}/pages/{page_number}" in paths


def test_agent_services_builds_one_shared_retriever_lazily(monkeypatch, tmp_path):
    import app.service as service_module

    built: list[Path] = []
    sentinel = object()

    def fake_hybrid_retriever(path: Path):
        built.append(path)
        return sentinel

    monkeypatch.setenv("EVIDENCE_CORPUS_DIR", str(tmp_path))
    monkeypatch.setattr(service_module, "HybridRetriever", fake_hybrid_retriever)
    services = object.__new__(service_module.AgentServices)
    services._evidence_retriever = None
    services._evidence_retriever_lock = threading.Lock()

    assert services.get_evidence_retriever() is sentinel
    assert services.get_evidence_retriever() is sentinel
    assert built == [tmp_path]


def test_agent_services_passes_real_retrieval_worker_and_ref_registry(monkeypatch):
    import app.service as service_module

    captured: dict = {}

    async def fake_graph_turn(**kwargs):
        captured.update(kwargs)
        return GraphTurnResult(brief=_brief("graph verified brief"), handoffs=())

    services = object.__new__(service_module.AgentServices)
    services._tokens = {
        "synthetic-session": TokenResponse(
            access_token="synthetic-token",
            scope="openid patient/Patient.read",
            patient="synthetic-patient",
        )
    }
    services.settings = type(
        "SettingsStub", (), {"smart_client_id": "synthetic-client"}
    )()
    services.tracer = object()
    services.get_evidence_retriever = lambda: _FixtureRetriever()
    services.run_brief = lambda *_args, **_kwargs: None
    monkeypatch.setattr(
        service_module.orchestrator_graph, "run_graph_turn", fake_graph_turn
    )

    result = asyncio.run(
        services.run_graph_turn(
            _session(), "type 2 diabetes; HbA1c", request_url="https://agent.test/chat"
        )
    )

    assert result.brief.text == "graph verified brief"
    assert callable(captured["retrieval_worker"])
    assert captured["ref_registry"] is not None
    worker_input = captured["worker_input"]
    assert worker_input.patient_ref == "session:synthetic-session"
    assert worker_input.evidence_refs
    request = captured["ref_registry"].resolve(worker_input.evidence_refs[0])
    assert request.query == "type 2 diabetes hba1c"


def test_agent_services_hydrates_complete_documents_and_real_extraction_worker(
    monkeypatch,
):
    """W2-D2/D3/§3: graph turns re-read persisted artifacts, never VLM state."""

    import app.service as service_module
    from app.orchestrator.workers.intake_extractor import PersistedExtraction

    captured: dict = {}

    async def fake_graph_turn(**kwargs):
        captured.update(kwargs)
        return GraphTurnResult(brief=_brief("graph verified brief"), handoffs=())

    class Documents:
        async def list_for_patient(self, patient_id: str, *, state: str | None = None):
            assert (patient_id, state) == ("synthetic-patient", "complete")
            return [type("Document", (), {"document_id": "document-synthetic"})()]

    class Artifacts:
        def __init__(self) -> None:
            self.warmed: list[str] = []

        async def warm_for_documents(self, document_ids: list[str]) -> None:
            self.warmed = document_ids

        def resolve(self, ref: str):
            if ref == "document:synthetic:artifact":
                return {"persisted": True}
            return None

    class Pipeline:
        def __init__(self) -> None:
            self.calls = 0

        async def extract_document(
            self, document_ref: str, *, patient_ref: str, correlation_id: str
        ) -> PersistedExtraction:
            self.calls += 1
            assert document_ref == "document-synthetic"
            assert patient_ref == "patient:synthetic-patient"
            return PersistedExtraction(
                artifact_ref="document:synthetic:artifact",
                citation_refs=("document:synthetic:citation:0",),
            )

    services = object.__new__(service_module.AgentServices)
    services._tokens = {
        "synthetic-session": TokenResponse(
            access_token="synthetic-token",
            scope="openid patient/Patient.read",
            patient="synthetic-patient",
        )
    }
    services.settings = type(
        "SettingsStub", (), {"smart_client_id": "synthetic-client"}
    )()
    services.tracer = object()
    services.get_evidence_retriever = lambda: _FixtureRetriever()
    services.run_brief = lambda *_args, **_kwargs: None
    services.document_repository = Documents()
    services.artifact_store = Artifacts()
    services.extraction_pipeline = Pipeline()
    monkeypatch.setattr(
        service_module.orchestrator_graph, "run_graph_turn", fake_graph_turn
    )

    asyncio.run(
        services.run_graph_turn(
            _session(), "type 2 diabetes; HbA1c", request_url="https://agent.test/chat"
        )
    )

    assert captured["worker_input"].document_refs == ["document-synthetic"]
    assert services.artifact_store.warmed == ["document-synthetic"]
    extraction = asyncio.run(captured["extraction_worker"](captured["worker_input"]))
    assert extraction.artifact_refs == ["document:synthetic:artifact"]
    assert services.extraction_pipeline.calls == 1
    assert captured["ref_registry"].resolve("document:synthetic:artifact") == {
        "persisted": True
    }


def test_graph_retrieval_unavailable_is_distinct_degradation_not_a_failed_hop():
    class UnavailableRetriever:
        def search(self, query: str, *, k: int, demographic_strings=()):
            raise RetrievalUnavailableError("synthetic unavailable")

    refs = TurnRefRegistry("synthetic-correlation")
    request_ref = refs.put(
        EvidenceSearchRequest(query="type 2 diabetes hba1c", k=3),
        kind="evidence-request",
    )
    payload = WorkerInput(
        correlation_id="synthetic-correlation",
        turn=0,
        patient_ref="session:synthetic-session",
        evidence_refs=[request_ref],
        request_kind="previsit_brief",
    )

    output = asyncio.run(build_evidence_worker(UnavailableRetriever(), refs)(payload))

    assert output.status == "degraded"
    assert output.artifact_refs == []
    assert output.citation_refs == []


def test_retrieval_index_readiness_is_soft_and_integrity_bound(
    complete_env, monkeypatch, tmp_path
):
    from app.config import get_settings
    from app.health import probe_retrieval_index

    monkeypatch.delenv("EVIDENCE_CORPUS_DIR", raising=False)
    healthy = asyncio.run(probe_retrieval_index(get_settings()))
    assert healthy.name == "retrieval_index"
    assert healthy.kind == "soft"
    assert healthy.ok is True

    monkeypatch.setenv("EVIDENCE_CORPUS_DIR", str(tmp_path / "missing"))
    missing = asyncio.run(probe_retrieval_index(get_settings()))
    assert missing.kind == "soft"
    assert missing.ok is False
    assert missing.detail == "integrity_check_failed"


def test_runtime_declares_retrieval_dependency_and_packages_corpus():
    agent_dir = Path(__file__).resolve().parents[1]
    pyproject = (agent_dir / "pyproject.toml").read_text(encoding="utf-8")
    dockerfile = (agent_dir / "Dockerfile").read_text(encoding="utf-8")

    assert '"rank-bm25>=' in pyproject
    assert "COPY corpus ./corpus" in dockerfile
    assert "COPY migrations ./migrations" in dockerfile
    assert "libgomp1" in dockerfile
    assert "tesseract-ocr" in dockerfile and "tesseract-ocr-eng" in dockerfile
