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
)
from app.orchestrator.graph import GraphTurnResult
from app.orchestrator.loop import BriefResult, Orchestrator, ToolRegistry
from app.schemas.citations import CitationSourceType, CitationV2, EvidenceSnippet
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
