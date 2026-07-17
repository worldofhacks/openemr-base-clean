"""Focused final answer-context and CitationV2 boundary contracts.

All values are synthetic.  These tests exercise the real answer loop and HTTP route;
there is no network or provider call.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.evidence.packet import build_evidence_packet
from app.llm.provider import LLMResponse, ToolUseBlock, Usage
from app.orchestrator.composer import (
    build_grounded_answer_context,
    citation_for_guideline,
    compose_answer,
)
from app.orchestrator.graph import GraphTurnResult, run_graph_turn
from app.orchestrator.loop import BriefResult, Orchestrator, ToolRegistry
from app.orchestrator.refs import TurnRefRegistry
from app.schemas.answers import VerifiedClinicalClaim
from app.schemas.citations import CitationSourceType, CitationV2, EvidenceSnippet
from app.schemas.extraction import NormBBox
from app.schemas.workers import WorkerInput, WorkerOutput
from app.session.store import Session


def _snippet(index: int) -> EvidenceSnippet:
    return EvidenceSnippet(
        source_id=f"synthetic-guideline-{index}@{'a' * 64}",
        section=f"Section {index}",
        chunk_id=f"chunk-{index}",
        quote=f"Canonical synthetic guideline quote {index}.",
        score=1.0 - index / 100,
        corpus_version=f"synthetic-corpus@{'b' * 64}",
    )


class _CapturingGuidelineProvider:
    model = "claude-sonnet-4-6"

    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def complete(self, *, system, messages, tools):
        self.requests.append({"system": system, "messages": messages, "tools": tools})
        return LLMResponse(
            content=[
                ToolUseBlock(
                    id="tool-1",
                    name="submit_claims",
                    input={
                        "claims": [
                            # Deliberately reverse valid selections: output must return to
                            # canonical reranker order, not model order.
                            {"type": "guideline", "chunk_id": "chunk-2", "evidence_ids": []},
                            {"type": "guideline", "chunk_id": "chunk-1", "evidence_ids": []},
                            # Canonical chunk with model-authored quote metadata: discard.
                            {
                                "type": "guideline",
                                "chunk_id": "chunk-3",
                                "evidence_ids": [],
                                "quote": "Altered model quote.",
                            },
                            # Chart ids are forbidden on guideline selections: discard.
                            {
                                "type": "guideline",
                                "chunk_id": "chunk-4",
                                "evidence_ids": ["Observation:invented"],
                            },
                            # Outside the supplied top five and unknown: discard.
                            {"type": "guideline", "chunk_id": "chunk-6", "evidence_ids": []},
                            {"type": "guideline", "chunk_id": "unknown", "evidence_ids": []},
                        ]
                    },
                )
            ],
            stop_reason="tool_use",
            usage=Usage(input_tokens=11, output_tokens=7),
            model=self.model,
        )


async def test_answer_model_gets_only_ranked_top_five_and_resolves_canonical_chunks() -> None:
    snippets = tuple(_snippet(index) for index in range(1, 7))
    context = build_grounded_answer_context(
        verified_facts=(),
        evidence_snippets=snippets,
        citations=tuple(citation_for_guideline(snippet) for snippet in snippets),
    )
    assert [item.chunk_id for item in context.guideline_snippets] == [
        "chunk-1",
        "chunk-2",
        "chunk-3",
        "chunk-4",
        "chunk-5",
    ]

    provider = _CapturingGuidelineProvider()
    result = await Orchestrator(provider).run_previsit_brief(
        build_evidence_packet("synthetic-patient", {}),
        "Summarize the grounded evidence.",
        tools=ToolRegistry([]),
        answer_context=context,
    )

    request = provider.requests[0]
    grounded_block = request["messages"][0]["content"][0]["text"]
    assert "GROUNDED ANSWER CONTEXT" in grounded_block
    assert "chunk-6" not in grounded_block
    assert "synthetic-guideline-1" not in grounded_block  # source metadata stays internal
    positions = [grounded_block.index(f'"chunk_id":"chunk-{index}"') for index in range(1, 6)]
    assert positions == sorted(positions)

    answer_schema = request["tools"][-1]["input_schema"]
    assert answer_schema["additionalProperties"] is False
    assert answer_schema["properties"]["claims"]["items"]["additionalProperties"] is False

    claims = result.verified_claims
    assert [claim.citation.field_or_chunk_id for claim in claims] == ["chunk-1", "chunk-2"]
    assert [claim.text for claim in claims] == [
        snippets[0].quote,
        snippets[1].quote,
    ]
    assert all(claim.citation.source_type is CitationSourceType.GUIDELINE for claim in claims)
    assert "Altered model quote" not in repr(result)
    assert result.guideline_selector_attempted is True


def _document_claim() -> VerifiedClinicalClaim:
    citation = CitationV2(
        source_type=CitationSourceType.UPLOADED_DOCUMENT,
        source_id="document:synthetic-magnesium-lab",
        page_or_section="1",
        field_or_chunk_id="results[0].value",
        quote_or_value="1.6",
    )
    return VerifiedClinicalClaim(
        text="results.0.value: 1.6",
        citation=citation,
        page=1,
        bbox=NormBBox(x0=0.42, y0=0.31, x1=0.51, y1=0.35),
    )


def _magnesium_claims() -> tuple[VerifiedClinicalClaim, ...]:
    source_id = "document:synthetic-magnesium-lab"
    name = VerifiedClinicalClaim(
        text="results.0.test_name: Magnesium",
        citation=CitationV2(
            source_type=CitationSourceType.UPLOADED_DOCUMENT,
            source_id=source_id,
            page_or_section="1",
            field_or_chunk_id="results[0].test_name",
            quote_or_value="Magnesium",
        ),
        page=1,
        bbox=NormBBox(x0=0.12, y0=0.31, x1=0.28, y1=0.35),
    )
    unit = VerifiedClinicalClaim(
        text="results.0.unit: mg/dL",
        citation=CitationV2(
            source_type=CitationSourceType.UPLOADED_DOCUMENT,
            source_id=source_id,
            page_or_section="1",
            field_or_chunk_id="results[0].unit",
            quote_or_value="mg/dL",
        ),
        page=1,
        bbox=NormBBox(x0=0.53, y0=0.31, x1=0.62, y1=0.35),
    )
    return name, _document_claim(), unit


def _chart_claim() -> VerifiedClinicalClaim:
    citation = CitationV2(
        source_type=CitationSourceType.PATIENT_RECORD,
        source_id="Observation/synthetic-unrelated",
        page_or_section=None,
        field_or_chunk_id="Observation:synthetic-unrelated:12345678",
        quote_or_value="7.7",
    )
    return VerifiedClinicalClaim(text="Unrelated synthetic chart value: 7.7", citation=citation)


def _selected_chart_claim() -> VerifiedClinicalClaim:
    citation = CitationV2(
        source_type=CitationSourceType.PATIENT_RECORD,
        source_id="Observation/synthetic-selected",
        page_or_section=None,
        field_or_chunk_id="Observation:synthetic-selected:87654321",
        quote_or_value="6.5",
    )
    return VerifiedClinicalClaim(text="Selected synthetic chart value: 6.5", citation=citation)


class _CapturingDocumentProvider:
    model = "claude-sonnet-4-6"

    def __init__(self, claims: list[dict]) -> None:
        self.claims = claims
        self.requests: list[dict] = []

    async def complete(self, *, system, messages, tools):
        self.requests.append({"system": system, "messages": messages, "tools": tools})
        return LLMResponse(
            content=[
                ToolUseBlock(
                    id="tool-document",
                    name="submit_claims",
                    input={"claims": self.claims},
                )
            ],
            stop_reason="tool_use",
            usage=Usage(input_tokens=9, output_tokens=4),
            model=self.model,
        )


async def test_answer_model_resolves_only_exact_canonical_document_selection() -> None:
    document_claim = _document_claim()
    context = build_grounded_answer_context(
        verified_facts=(document_claim,),
        evidence_snippets=(),
        citations=(document_claim.citation,),
    )
    provider = _CapturingDocumentProvider(
        [
            {
                "type": "document",
                "claim_id": "document-claim-1",
                "field_id": "results[0].value",
                "evidence_ids": [],
            },
            # A model-authored value is an embellished selector, so it cannot resolve.
            {
                "type": "document",
                "claim_id": "document-claim-1",
                "field_id": "results[0].value",
                "evidence_ids": [],
                "text": "1.7",
            },
            {
                "type": "document",
                "claim_id": "document-claim-404",
                "field_id": "results[0].value",
                "evidence_ids": [],
            },
        ]
    )

    result = await Orchestrator(provider).run_previsit_brief(
        build_evidence_packet("synthetic-patient", {}),
        "Magnesium",
        tools=ToolRegistry([]),
        answer_context=context,
    )

    grounded_block = provider.requests[0]["messages"][0]["content"][0]["text"]
    assert '"claim_id":"document-claim-1"' in grounded_block
    assert '"field_id":"results[0].value"' in grounded_block
    assert "document:synthetic-magnesium-lab" not in grounded_block
    assert '"page"' not in grounded_block
    assert '"bbox"' not in grounded_block
    assert result.source == "llm"
    assert result.answer_reason_code == "verified"
    assert result.verified_claims == (document_claim,)
    assert result.verified_claims[0].citation.quote_or_value == "1.6"
    assert result.verified_claims[0].bbox == document_claim.bbox
    assert result.guideline_selector_attempted is False


async def _run_document_graph(
    provider: _CapturingDocumentProvider,
    verified_facts: tuple[VerifiedClinicalClaim, ...],
    *,
    question: str = "Magnesium",
) -> GraphTurnResult:
    correlation_id = "corr-document-answer"
    refs = TurnRefRegistry(correlation_id)
    fact_refs = [refs.put(fact, kind="verified-fact") for fact in verified_facts]
    citation_refs = [
        refs.put(fact.citation, kind="citation") for fact in verified_facts
    ]

    async def extractor(payload: WorkerInput) -> WorkerOutput:
        return WorkerOutput(
            correlation_id=payload.correlation_id,
            worker="intake_extractor",
            status="complete",
            artifact_refs=fact_refs,
            citation_refs=citation_refs,
        )

    async def retriever(payload: WorkerInput) -> WorkerOutput:
        return WorkerOutput(
            correlation_id=payload.correlation_id,
            worker="evidence_retriever",
            status="complete",
        )

    orchestrator = Orchestrator(provider)

    async def run_brief() -> BriefResult:
        raise AssertionError("the context-aware production answer path must be used")

    async def run_brief_with_context(context) -> BriefResult:
        return await orchestrator.run_previsit_brief(
            build_evidence_packet("synthetic-patient", {}),
            question,
            tools=ToolRegistry([]),
            answer_context=context,
        )

    return await run_graph_turn(
        run_brief=run_brief,
        run_brief_with_context=run_brief_with_context,
        correlation_id=correlation_id,
        worker_input=WorkerInput(
            correlation_id=correlation_id,
            turn=0,
            patient_ref="session:synthetic-pinned-patient",
            document_refs=["document:synthetic-magnesium-lab"],
            evidence_refs=[],
            request_kind="clinical_question",
        ),
        extraction_worker=extractor,
        retrieval_worker=retriever,
        ref_registry=refs,
    )


async def test_grounded_uploaded_lab_term_returns_verified_citation_v2_answer() -> None:
    document_claims = _magnesium_claims()
    provider = _CapturingDocumentProvider(
        [
            {
                "type": "document",
                "claim_id": "document-claim-1",
                "field_id": "results[0].test_name",
                "evidence_ids": [],
            },
            {
                "type": "document",
                "claim_id": "document-claim-2",
                "field_id": "results[0].value",
                "evidence_ids": [],
            },
            {
                "type": "document",
                "claim_id": "document-claim-3",
                "field_id": "results[0].unit",
                "evidence_ids": [],
            },
        ]
    )

    result = await _run_document_graph(provider, (*document_claims, _chart_claim()))

    assert result.critic_approved is True
    assert result.brief.source == "llm"
    assert result.brief.answer_reason_code == "verified"
    rendered = result.composition.for_source(CitationSourceType.UPLOADED_DOCUMENT)
    assert len(rendered) == 3
    assert " ".join(claim.citation.quote_or_value for claim in rendered) == (
        "Magnesium 1.6 mg/dL"
    )
    assert [claim.citation.field_or_chunk_id for claim in rendered] == [
        "results[0].test_name",
        "results[0].value",
        "results[0].unit",
    ]
    assert all(isinstance(claim.citation, CitationV2) for claim in rendered)
    assert all(claim.overlay is not None for claim in rendered)
    assert [claim.overlay.bbox for claim in rendered if claim.overlay is not None] == [
        document_claim.bbox for document_claim in document_claims
    ]
    assert result.composition.for_source(CitationSourceType.PATIENT_RECORD) == ()
    assert "7.7" not in " ".join(claim.text for claim in result.composition.claims)


async def test_context_composer_keeps_only_explicitly_selected_chart_claim() -> None:
    selected = _selected_chart_claim()
    unrelated = _chart_claim()

    async def unsupported_legacy_path() -> BriefResult:
        raise AssertionError("the context-aware production answer path must be used")

    async def run_with_context(_context) -> BriefResult:
        return BriefResult(
            text="Verified chart evidence is provided below.",
            source="llm",
            degraded=False,
            usage=Usage(),
            iterations=1,
            verified_claims=(selected,),
            answer_reason_code="verified",
        )

    result = await compose_answer(
        verified_facts=(selected, unrelated),
        evidence_snippets=(),
        citations=(selected.citation, unrelated.citation),
        run_brief=unsupported_legacy_path,
        run_brief_with_context=run_with_context,
    )

    rendered = result.composition.for_source(CitationSourceType.PATIENT_RECORD)
    assert [claim.text for claim in rendered] == [selected.text]
    assert unrelated.text not in " ".join(
        claim.text for claim in result.composition.claims
    )


async def test_context_composer_adds_top_guideline_for_anchored_answer() -> None:
    document = _document_claim()
    snippets = (_snippet(1), _snippet(2))

    async def unsupported_legacy_path() -> BriefResult:
        raise AssertionError("the context-aware production answer path must be used")

    async def run_with_context(_context) -> BriefResult:
        # Reproduce the runtime omission: the answer model selected relevant patient
        # evidence but did not emit a separate guideline chunk selector.
        return BriefResult(
            text="Verified uploaded-document evidence is provided below.",
            source="llm",
            degraded=False,
            usage=Usage(),
            iterations=1,
            verified_claims=(document,),
            answer_reason_code="verified",
        )

    result = await compose_answer(
        verified_facts=(document,),
        evidence_snippets=snippets,
        citations=(
            document.citation,
            *(citation_for_guideline(snippet) for snippet in snippets),
        ),
        run_brief=unsupported_legacy_path,
        run_brief_with_context=run_with_context,
    )

    rendered = result.composition.for_source(CitationSourceType.GUIDELINE)
    assert [claim.citation.field_or_chunk_id for claim in rendered] == ["chunk-1"]
    assert rendered[0].text == snippets[0].quote
    assert rendered[0].citation.quote_or_value == snippets[0].quote
    assert rendered[0].citation.page_or_section == snippets[0].section
    assert rendered[0].citation.source_id == snippets[0].source_id


async def test_context_composer_preserves_explicit_guideline_selection() -> None:
    document = _document_claim()
    snippets = (_snippet(1), _snippet(2))
    selected = VerifiedClinicalClaim(
        text=snippets[1].quote,
        citation=citation_for_guideline(snippets[1]),
    )

    async def unsupported_legacy_path() -> BriefResult:
        raise AssertionError("the context-aware production answer path must be used")

    async def run_with_context(_context) -> BriefResult:
        return BriefResult(
            text="Verified document and guideline evidence is provided below.",
            source="llm",
            degraded=False,
            usage=Usage(),
            iterations=1,
            verified_claims=(document, selected),
            answer_reason_code="verified",
        )

    result = await compose_answer(
        verified_facts=(document,),
        evidence_snippets=snippets,
        citations=(
            document.citation,
            *(citation_for_guideline(snippet) for snippet in snippets),
        ),
        run_brief=unsupported_legacy_path,
        run_brief_with_context=run_with_context,
    )

    rendered = result.composition.for_source(CitationSourceType.GUIDELINE)
    assert [claim.citation.field_or_chunk_id for claim in rendered] == ["chunk-2"]
    assert rendered[0].text == snippets[1].quote


async def test_context_composer_does_not_replace_unresolved_guideline_selector() -> None:
    document = _document_claim()
    snippet = _snippet(1)

    async def unsupported_legacy_path() -> BriefResult:
        raise AssertionError("the context-aware production answer path must be used")

    async def run_with_context(_context) -> BriefResult:
        return BriefResult(
            text="Verified uploaded-document evidence is provided below.",
            source="llm",
            degraded=False,
            usage=Usage(),
            iterations=1,
            verified_claims=(document,),
            guideline_selector_attempted=True,
            answer_reason_code="verified",
        )

    result = await compose_answer(
        verified_facts=(document,),
        evidence_snippets=(snippet,),
        citations=(document.citation, citation_for_guideline(snippet)),
        run_brief=unsupported_legacy_path,
        run_brief_with_context=run_with_context,
    )

    assert len(
        result.composition.for_source(CitationSourceType.UPLOADED_DOCUMENT)
    ) == 1
    assert result.composition.for_source(CitationSourceType.GUIDELINE) == ()


async def test_context_composer_does_not_dump_guidelines_without_patient_anchor() -> None:
    snippet = _snippet(1)

    async def unsupported_legacy_path() -> BriefResult:
        raise AssertionError("the context-aware production answer path must be used")

    async def run_with_context(_context) -> BriefResult:
        return BriefResult(
            text="No verified patient-specific claim matched.",
            source="llm",
            degraded=False,
            usage=Usage(),
            iterations=1,
            verified_claims=(),
            answer_reason_code="verified",
        )

    result = await compose_answer(
        verified_facts=(),
        evidence_snippets=(snippet,),
        citations=(citation_for_guideline(snippet),),
        run_brief=unsupported_legacy_path,
        run_brief_with_context=run_with_context,
    )

    assert result.composition.for_source(CitationSourceType.GUIDELINE) == ()


async def test_unmatched_document_query_refuses_without_unrelated_evidence_dump(
    caplog,
) -> None:
    # The answer model found no claim relevant to the out-of-scope query. An explicit empty
    # typed selection must become a helpful refusal, never an unscoped evidence dump.
    provider = _CapturingDocumentProvider([])
    caplog.set_level("INFO")

    result = await _run_document_graph(
        provider,
        (_document_claim(), _chart_claim()),
        question="What will the weather be tomorrow?",
    )

    assert result.critic_approved is True
    assert result.brief.source == "deterministic_refusal"
    assert result.brief.answer_reason_code == "no_claim"
    assert result.brief.verdicts == ["refused:no_claim"]
    assert "Ask about a condition or test" in result.brief.text
    assert "Magnesium" in result.brief.text
    assert "1.6" not in result.brief.text
    assert "7.7" not in result.brief.text
    assert result.brief.citations == []
    assert result.brief.verified_claims == ()
    assert result.composition.claims == ()
    assert any(
        record.getMessage() == "answer_outcome"
        and getattr(record, "reason_code", None) == "all_blocked"
        for record in caplog.records
    )
    assert any(
        record.getMessage() == "graph_answer_outcome"
        and getattr(record, "reason_code", None) == "no_claim"
        for record in caplog.records
    )


async def test_empty_answer_context_refuses_with_no_evidence_reason() -> None:
    provider = _CapturingDocumentProvider([])

    result = await _run_document_graph(provider, ())

    assert result.critic_approved is True
    assert result.brief.source == "deterministic_refusal"
    assert result.brief.answer_reason_code == "no_evidence"
    assert result.brief.verdicts == ["refused:no_evidence"]
    assert "Confirm that a patient is pinned" in result.brief.text
    assert result.brief.citations == []
    assert result.composition.claims == ()


def _chart_citation() -> CitationV2:
    return CitationV2(
        source_type=CitationSourceType.PATIENT_RECORD,
        source_id="MedicationRequest/synthetic-medication",
        page_or_section=None,
        field_or_chunk_id="MedicationRequest:synthetic-medication:12345678",
        quote_or_value='{"dose":"500 mg","name":"synthetic medicine"}',
    )


def _session() -> Session:
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
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


class _BoundaryServices:
    def __init__(self) -> None:
        self.citation = _chart_citation()
        # A deliberately malformed internal fixture proves the route filters rather than
        # serializes a legacy citation.  Production BriefResult producers are V2-only.
        self.result = BriefResult(
            text="Verified synthetic chart fact.",
            source="llm",
            degraded=False,
            usage=Usage(),
            iterations=1,
            verdicts=["pass"],
            citations=[self.citation, "legacy:citation:string"],  # type: ignore[list-item]
        )

    async def resolve_session(self, _session_id: str) -> Session:
        return _session()

    async def run_brief(self, _session, _message, *, request_url: str) -> BriefResult:
        return self.result

    async def run_graph_turn(self, _session, _message, *, request_url: str) -> GraphTurnResult:
        return GraphTurnResult(brief=self.result, handoffs=())


def test_json_and_sse_http_boundaries_are_citation_v2_only(
    complete_env, monkeypatch
) -> None:
    from app.main import create_app

    monkeypatch.setenv("W2_GRAPH_ENABLED", "1")
    services = _BoundaryServices()
    client = TestClient(create_app(services=services, readiness_checks=[]))

    response = client.post(
        "/chat",
        json={"session_id": "synthetic-session", "message": "Give me the brief."},
    )
    assert response.status_code == 200
    assert response.json()["citations"] == [
        services.citation.model_dump(mode="json")
    ]
    assert "legacy:citation:string" not in response.text

    stream = client.post(
        "/chat",
        json={"session_id": "synthetic-session", "message": "Give me the brief."},
        headers={"Accept": "text/event-stream"},
    )
    assert stream.status_code == 200
    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in stream.text.splitlines()
        if line.startswith("data: ")
    ]
    claim_blocks = [item for item in payloads if "claim_block" in item]
    assert claim_blocks
    assert all(
        isinstance(citation, dict)
        for block in claim_blocks
        for citation in block["citations"]
    )
    assert "legacy:citation:string" not in stream.text
