"""Canonical WorkerInput/WorkerOutput adapter for guideline retrieval.

Only a ref to ``EvidenceSearchRequest`` crosses the worker boundary. The adapter
resolves it, re-applies the PHI-free clinical-term builder, calls the real hybrid
retriever, and stores canonical EvidenceSnippet/CitationV2 objects behind refs.

Traceability: W2-D2/W2-D4/W2-D6; W2_ARCHITECTURE.md §2/§4/§5.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from starlette.concurrency import run_in_threadpool

from app.orchestrator.composer import citation_for_guideline
from app.orchestrator.refs import RefResolver
from app.orchestrator.workers.contracts import WorkerCallable
from app.schemas.citations import EvidenceSnippet
from app.schemas.retrieval import EvidenceSearchRequest
from app.schemas.workers import WorkerInput, WorkerOutput
from corpus.retrieval import RetrievalOutcome, build_clinical_query


WORKER_NAME = "evidence_retriever"


class EvidenceRetriever(Protocol):
    def search(
        self,
        query: str,
        *,
        k: int,
        demographic_strings: Sequence[str] = (),
    ) -> RetrievalOutcome: ...


def build_evidence_worker(
    retriever: EvidenceRetriever,
    refs: RefResolver,
    *,
    demographic_strings: Sequence[str] = (),
) -> WorkerCallable:
    """Bind the real retriever and per-turn ref resolver behind one worker callable."""

    demographics = tuple(demographic_strings)

    async def run(payload: WorkerInput) -> WorkerOutput:
        snippet_refs: list[str] = []
        citation_refs: list[str] = []
        degraded = False
        for request_ref in payload.evidence_refs:
            request = refs.resolve(request_ref)
            if not isinstance(request, EvidenceSearchRequest):
                raise TypeError("evidence ref did not resolve to EvidenceSearchRequest")
            query = build_clinical_query(
                re.split(r"[,;|]+", request.query),
                demographic_strings=demographics,
            )
            outcome = await run_in_threadpool(
                retriever.search,
                query,
                k=request.k,
                demographic_strings=demographics,
            )
            degraded = degraded or bool(outcome.degraded_reasons)
            for hit in outcome.items:
                snippet = EvidenceSnippet(
                    source_id=hit.source_id,
                    section=hit.section,
                    chunk_id=hit.chunk_id,
                    quote=hit.quote,
                    score=hit.score,
                    corpus_version=hit.corpus_version,
                )
                citation = citation_for_guideline(snippet)
                if not citation.page_or_section or not citation.page_or_section.strip():
                    raise ValueError("guideline evidence requires a section")
                snippet_refs.append(refs.put(snippet, kind="evidence-snippet"))
                citation_refs.append(refs.put(citation, kind="guideline-citation"))

        return WorkerOutput(
            correlation_id=payload.correlation_id,
            worker=WORKER_NAME,
            status="degraded" if degraded else "complete",
            artifact_refs=snippet_refs,
            citation_refs=citation_refs,
            reason_code=None,
        )

    return run
