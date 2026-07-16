"""Focused B3 completion contracts (W2-D2/D3/D4/D6; §2/§2a/§3/§5).

Synthetic values only. These tests pin the canonical worker boundary, the real
retrieval adapter, and the composer's verify-then-render rule without coupling to
the still-independent B2 implementation module.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.llm.provider import Usage
from app.orchestrator.composer import (
    CandidateClaim,
    citation_for_chart_fact,
    verify_then_render,
)
from app.orchestrator.graph import run_graph_turn
from app.orchestrator.loop import BriefResult
from app.orchestrator.refs import TurnRefRegistry
from app.orchestrator.workers.evidence_retriever import build_evidence_worker
from app.schemas.citations import (
    CitationSourceType,
    CitationV2,
    EvidenceSnippet,
)
from app.schemas.answers import VerifiedClinicalClaim
from app.schemas.extraction import NormBBox
from app.schemas.retrieval import EvidenceSearchRequest, K_MAX
from app.schemas.workers import WorkerInput, WorkerOutput
from corpus.retrieval import EvidenceHit, RetrievalOutcome


def _citation(
    source_type: CitationSourceType,
    *,
    page_or_section: str | None,
    field_id: str,
) -> CitationV2:
    return CitationV2(
        source_type=source_type,
        source_id="synthetic-source",
        page_or_section=page_or_section,
        field_or_chunk_id=field_id,
        quote_or_value="synthetic grounded value",
    )


def _brief() -> BriefResult:
    citation = CitationV2(
        source_type=CitationSourceType.PATIENT_RECORD,
        source_id="Observation/synthetic-observation",
        page_or_section=None,
        field_or_chunk_id="Observation:synthetic:01234567",
        quote_or_value="synthetic grounded value",
    )
    return BriefResult(
        text="Existing W1 verified brief.",
        source="llm",
        degraded=False,
        usage=Usage(),
        iterations=1,
        verdicts=["pass"],
        citations=[citation],
        verified_claims=(
            VerifiedClinicalClaim(
                text="Existing W1 verified brief.", citation=citation
            ),
        ),
    )


def test_verify_then_render_enforces_source_specific_citations_and_bbox() -> None:
    box = NormBBox(x0=0.1, y0=0.2, x1=0.3, y1=0.4)
    result = verify_then_render(
        [
            CandidateClaim(
                text="Chart fact",
                citation=_citation(
                    CitationSourceType.PATIENT_RECORD,
                    page_or_section=None,
                    field_id="chart-field",
                ),
                verified=True,
            ),
            CandidateClaim(
                text="Grounded document fact",
                citation=_citation(
                    CitationSourceType.UPLOADED_DOCUMENT,
                    page_or_section="2",
                    field_id="results.0.value",
                ),
                verified=True,
                page=2,
                bbox=box,
            ),
            CandidateClaim(
                text="Guideline evidence",
                citation=_citation(
                    CitationSourceType.GUIDELINE,
                    page_or_section="Recommendations",
                    field_id="chunk-1",
                ),
                verified=True,
            ),
            # Each unsafe leg must be structurally absent from the rendered result.
            CandidateClaim(text="Uncited", citation=None, verified=True),
            CandidateClaim(
                text="Unverified",
                citation=_citation(
                    CitationSourceType.GUIDELINE,
                    page_or_section="Recommendations",
                    field_id="chunk-2",
                ),
                verified=False,
            ),
            CandidateClaim(
                text="Guideline missing section",
                # Construction now rejects this shape. ``model_construct`` deliberately
                # simulates a corrupted legacy/internal value so the composer remains a
                # second fail-closed boundary.
                citation=CitationV2.model_construct(
                    source_type=CitationSourceType.GUIDELINE,
                    source_id="synthetic-source",
                    page_or_section=None,
                    field_or_chunk_id="chunk-3",
                    quote_or_value="synthetic grounded value",
                ),
                verified=True,
            ),
            CandidateClaim(
                text="Document missing overlay",
                citation=_citation(
                    CitationSourceType.UPLOADED_DOCUMENT,
                    page_or_section="3",
                    field_id="results.1.value",
                ),
                verified=True,
                page=3,
            ),
            CandidateClaim(
                text="Document page mismatch",
                citation=_citation(
                    CitationSourceType.UPLOADED_DOCUMENT,
                    page_or_section="4",
                    field_id="results.2.value",
                ),
                verified=True,
                page=3,
                bbox=box,
            ),
        ]
    )

    assert [claim.text for claim in result.claims] == [
        "Chart fact",
        "Grounded document fact",
        "Guideline evidence",
    ]
    assert [claim.source_class for claim in result.claims] == [
        CitationSourceType.PATIENT_RECORD,
        CitationSourceType.UPLOADED_DOCUMENT,
        CitationSourceType.GUIDELINE,
    ]
    document_claim = result.claims[1]
    assert document_claim.overlay is not None
    assert document_claim.overlay.page == 2
    assert document_claim.overlay.bbox == box
    assert all(claim.citation is not None for claim in result.claims)


def test_uploaded_document_verified_claim_pages_are_one_based() -> None:
    citation = _citation(
        CitationSourceType.UPLOADED_DOCUMENT,
        page_or_section="0",
        field_id="results.0.value",
    )
    with pytest.raises(ValidationError):
        VerifiedClinicalClaim(
            text="Synthetic grounded fact",
            citation=citation,
            page=0,
            bbox=NormBBox(x0=0.1, y0=0.2, x1=0.3, y1=0.4),
        )


def test_chart_citation_adapter_pins_the_frozen_w1_mapping() -> None:
    citation = citation_for_chart_fact(
        resource_type="Observation",
        resource_id="synthetic-observation",
        evidence_id="Observation:synthetic-observation:12345678",
        verified_value="6.5 %",
    )

    assert citation.source_type is CitationSourceType.PATIENT_RECORD
    assert citation.source_id == "Observation/synthetic-observation"
    assert citation.page_or_section is None
    assert citation.field_or_chunk_id == "Observation:synthetic-observation:12345678"
    assert citation.quote_or_value == "6.5 %"


async def test_evidence_worker_uses_canonical_refs_and_emits_guideline_citations() -> None:
    manifest_hash = "a" * 64
    corpus_version = f"vadod-cpg-trio@{manifest_hash}"

    class FakeRetriever:
        def search(self, query: str, *, k: int, demographic_strings=()):
            assert query == "type 2 diabetes hba1c"
            assert k == K_MAX
            assert demographic_strings == ()
            return RetrievalOutcome(
                items=(
                    EvidenceHit(
                        source_id=f"vadod-diabetes-2023@{manifest_hash}",
                        section="Recommendations",
                        chunk_id="chunk-1",
                        quote="Synthetic public guideline evidence.",
                        score=0.9,
                        corpus_version=corpus_version,
                    ),
                ),
                corpus_version=corpus_version,
                manifest_hash=manifest_hash,
                degraded_reasons=(),
            )

    refs = TurnRefRegistry("corr-retrieval")
    request_ref = refs.put(
        EvidenceSearchRequest(query="type 2 diabetes; HbA1c", k=K_MAX),
        kind="evidence-request",
    )
    worker = build_evidence_worker(FakeRetriever(), refs)
    payload = WorkerInput(
        correlation_id="corr-retrieval",
        turn=0,
        patient_ref="session:synthetic",
        document_refs=[],
        evidence_refs=[request_ref],
        request_kind="guideline_evidence",
    )

    output = await worker(payload)

    assert isinstance(output, WorkerOutput)
    assert output.status == "complete"
    assert len(output.artifact_refs) == len(output.citation_refs) == 1
    snippet = refs.resolve(output.artifact_refs[0])
    citation = refs.resolve(output.citation_refs[0])
    assert isinstance(snippet, EvidenceSnippet)
    assert isinstance(citation, CitationV2)
    assert citation.source_type is CitationSourceType.GUIDELINE
    assert citation.page_or_section == snippet.section
    assert citation.field_or_chunk_id == snippet.chunk_id


async def test_graph_calls_workers_only_through_canonical_worker_payloads() -> None:
    calls: list[tuple[str, WorkerInput]] = []

    async def extractor(payload: WorkerInput) -> WorkerOutput:
        calls.append(("extract", payload))
        return WorkerOutput(
            correlation_id=payload.correlation_id,
            worker="intake_extractor",
            status="complete",
            artifact_refs=["artifact:synthetic"],
            citation_refs=["citation:document"],
            reason_code=None,
        )

    async def retriever(payload: WorkerInput) -> WorkerOutput:
        calls.append(("retrieve", payload))
        return WorkerOutput(
            correlation_id=payload.correlation_id,
            worker="evidence_retriever",
            status="complete",
            artifact_refs=["evidence:synthetic"],
            citation_refs=["citation:guideline"],
            reason_code=None,
        )

    async def run_brief() -> BriefResult:
        return _brief()

    initial = WorkerInput(
        correlation_id="corr-graph",
        turn=0,
        patient_ref="session:synthetic",
        document_refs=["document:synthetic"],
        evidence_refs=["query:synthetic"],
        request_kind="clinical_question",
    )
    result = await run_graph_turn(
        run_brief=run_brief,
        correlation_id="corr-graph",
        worker_input=initial,
        extraction_worker=extractor,
        retrieval_worker=retriever,
    )

    assert [name for name, _payload in calls] == ["extract", "retrieve"]
    assert all(isinstance(payload, WorkerInput) for _name, payload in calls)
    assert [payload.turn for _name, payload in calls] == [0, 1]
    assert result.brief == _brief()
    assert all(record.correlation_id == "corr-graph" for record in result.handoffs)


async def test_graph_retrieval_refs_reach_verified_guideline_composition() -> None:
    manifest_hash = "b" * 64
    corpus_version = f"vadod-cpg-trio@{manifest_hash}"

    class FakeRetriever:
        def search(self, query: str, *, k: int, demographic_strings=()):
            return RetrievalOutcome(
                items=(
                    EvidenceHit(
                        source_id=f"vadod-diabetes-2023@{manifest_hash}",
                        section="Recommendations: Glycemic Targets",
                        chunk_id="chunk-hba1c",
                        quote="Synthetic public guideline text about individualized targets.",
                        score=0.95,
                        corpus_version=corpus_version,
                    ),
                ),
                corpus_version=corpus_version,
                manifest_hash=manifest_hash,
                degraded_reasons=(),
            )

    refs = TurnRefRegistry("corr-compose")
    request_ref = refs.put(
        EvidenceSearchRequest(query="type 2 diabetes; HbA1c", k=3),
        kind="evidence-request",
    )
    payload = WorkerInput(
        correlation_id="corr-compose",
        turn=0,
        patient_ref="session:synthetic",
        document_refs=[],
        evidence_refs=[request_ref],
        request_kind="clinical_question",
    )

    async def extractor(worker_input: WorkerInput) -> WorkerOutput:
        return WorkerOutput(
            correlation_id=worker_input.correlation_id,
            worker="intake_extractor",
            status="complete",
            artifact_refs=[],
            citation_refs=[],
            reason_code=None,
        )

    async def run_brief() -> BriefResult:
        return _brief()

    result = await run_graph_turn(
        run_brief=run_brief,
        correlation_id="corr-compose",
        worker_input=payload,
        extraction_worker=extractor,
        retrieval_worker=build_evidence_worker(FakeRetriever(), refs),
        ref_registry=refs,
    )

    guideline_claims = result.composition.for_source(CitationSourceType.GUIDELINE)
    assert len(guideline_claims) == 1
    assert guideline_claims[0].citation.field_or_chunk_id == "chunk-hba1c"
    assert guideline_claims[0].citation.page_or_section == (
        "Recommendations: Glycemic Targets"
    )


async def test_graph_retries_malformed_worker_once_then_refuses() -> None:
    calls = 0

    async def malformed(payload: WorkerInput) -> WorkerOutput:
        nonlocal calls
        calls += 1
        return WorkerOutput(
            correlation_id="wrong-correlation",
            worker="intake_extractor",
            status="complete",
            artifact_refs=[],
            citation_refs=[],
            reason_code=None,
        )

    async def run_brief() -> BriefResult:
        raise AssertionError("composer must not run after repeated malformed output")

    result = await run_graph_turn(
        run_brief=run_brief,
        correlation_id="corr-retry",
        extraction_worker=malformed,
    )

    assert calls == 2
    assert result.brief.source == "deterministic_refusal"
    assert [record.supervisor_decision.value for record in result.handoffs] == [
        "route_extract",
        "route_extract",
        "refuse",
    ]


def test_evidence_route_reexports_frozen_schema_models_and_bound() -> None:
    from app.routes import evidence
    from app.schemas.citations import EvidenceSnippet as CanonicalSnippet
    from app.schemas.retrieval import (
        EvidenceSearchRequest as CanonicalRequest,
        EvidenceSearchResponse as CanonicalResponse,
    )

    assert evidence.EvidenceSearchRequest is CanonicalRequest
    assert evidence.EvidenceSearchResponse is CanonicalResponse
    assert evidence.EvidenceSnippet is CanonicalSnippet
    assert K_MAX == 20
