"""Typed POST /evidence/search route contract (W2-M14, W2-D4, architecture §2a)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.middleware.correlation import CorrelationIdMiddleware
from app.routes import evidence
from app.routes.evidence import (
    EvidenceSearchRequest,
    EvidenceSearchResponse,
    EvidenceSnippet,
)
from app.schemas.citations import EvidenceSnippet as CanonicalEvidenceSnippet
from app.schemas.retrieval import (
    K_MAX,
    EvidenceSearchRequest as CanonicalEvidenceSearchRequest,
    EvidenceSearchResponse as CanonicalEvidenceSearchResponse,
)
from corpus.retrieval import EvidenceHit, RetrievalOutcome, RetrievalUnavailableError


MANIFEST_HASH = "a" * 64
CORPUS_VERSION = f"vadod-cpg-trio@{MANIFEST_HASH}"


class _FakeRetriever:
    def __init__(
        self, *, unavailable: bool = False, empty: bool = False, reject_query: bool = False
    ):
        self.unavailable = unavailable
        self.empty = empty
        self.reject_query = reject_query
        self.calls: list[tuple[str, int, tuple[str, ...]]] = []

    def search(
        self, query: str, *, k: int, demographic_strings: tuple[str, ...] = ()
    ) -> RetrievalOutcome:
        self.calls.append((query, k, demographic_strings))
        if self.unavailable:
            raise RetrievalUnavailableError("synthetic unavailable")
        if self.reject_query:
            from corpus.retrieval import QueryContractError

            raise QueryContractError("synthetic defensive rejection")
        items = () if self.empty else (
            EvidenceHit(
                source_id=f"vadod-hypertension-2020@{MANIFEST_HASH}",
                section="Recommendations: Treatment Goals",
                chunk_id="vadod-hypertension-2020:p0033:c001:abc123",
                quote="We recommend treating to a systolic blood pressure goal of less than 130 mm Hg.",
                score=0.98,
                corpus_version=CORPUS_VERSION,
            ),
        )
        return RetrievalOutcome(
            items=items,
            corpus_version=CORPUS_VERSION,
            manifest_hash=MANIFEST_HASH,
            degraded_reasons=(),
        )


def _client(retriever: _FakeRetriever) -> TestClient:
    app = FastAPI()
    app.state.evidence_retriever = retriever
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(evidence.router)
    return TestClient(app)


def test_models_are_named_strict_and_field_for_field_typed() -> None:
    assert EvidenceSearchRequest is CanonicalEvidenceSearchRequest
    assert EvidenceSearchResponse is CanonicalEvidenceSearchResponse
    assert EvidenceSnippet is CanonicalEvidenceSnippet
    assert EvidenceSearchRequest.model_config["extra"] == "forbid"
    assert EvidenceSearchResponse.model_config["extra"] == "forbid"
    assert set(EvidenceSearchRequest.model_fields) == {"query", "k"}
    assert set(EvidenceSnippet.model_fields) == {
        "source_id", "section", "chunk_id", "quote", "score", "corpus_version"
    }
    assert set(EvidenceSearchResponse.model_fields) == {
        "items", "corpus_version", "correlation_id"
    }


def test_route_validation_rejects_duplicate_or_cross_version_evidence() -> None:
    hit = EvidenceHit(
        source_id=f"vadod-hypertension-2020@{MANIFEST_HASH}",
        section="Recommendations",
        chunk_id="chunk-1",
        quote="Public guideline text.",
        score=0.9,
        corpus_version=CORPUS_VERSION,
    )
    with pytest.raises(RetrievalUnavailableError):
        evidence._validated_items(
            RetrievalOutcome(
                items=(hit, hit),
                corpus_version=CORPUS_VERSION,
                manifest_hash=MANIFEST_HASH,
                degraded_reasons=(),
            )
        )
    with pytest.raises(RetrievalUnavailableError):
        evidence._validated_items(
            RetrievalOutcome(
                items=(
                    EvidenceHit(
                        **{
                            **hit.__dict__,
                            "corpus_version": "other@" + "b" * 64,
                        }
                    ),
                ),
                corpus_version=CORPUS_VERSION,
                manifest_hash=MANIFEST_HASH,
                degraded_reasons=(),
            )
        )


def test_search_route_returns_typed_items_and_correlation_id() -> None:
    retriever = _FakeRetriever()
    client = _client(retriever)
    response = client.post(
        "/evidence/search",
        json={"query": "hypertension blood pressure", "k": 3},
        headers={"X-Copilot-Request-Id": "corr-evidence-1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["correlation_id"] == "corr-evidence-1"
    assert body["corpus_version"] == CORPUS_VERSION
    assert body["items"][0]["source_id"].endswith("@" + MANIFEST_HASH)
    assert retriever.calls == [("hypertension blood pressure", 3, ())]


def test_openapi_uses_named_request_and_response_models() -> None:
    client = _client(_FakeRetriever())
    operation = client.get("/openapi.json").json()["paths"]["/evidence/search"]["post"]
    request_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    response_ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert request_ref.endswith("/EvidenceSearchRequest")
    assert response_ref.endswith("/EvidenceSearchResponse")


def test_request_bounds_extra_fields_and_phi_are_rejected_before_retrieval() -> None:
    retriever = _FakeRetriever()
    client = _client(retriever)
    bad_payloads = [
        {"query": "hypertension", "k": 0},
        {"query": "hypertension", "k": K_MAX + 1},
        {"query": "hypertension", "k": "not-an-int"},
        {"query": "What should I tell my patient?", "k": 3},
        {"query": "hypertension MRN AB123456", "k": 3},
        {"query": "hypertension", "k": 3, "patient_id": "synthetic"},
    ]
    for payload in bad_payloads:
        assert client.post("/evidence/search", json=payload).status_code == 422
    assert retriever.calls == []


def test_healthy_empty_is_200_but_unavailable_index_is_503() -> None:
    empty = _client(_FakeRetriever(empty=True)).post(
        "/evidence/search", json={"query": "xylophonemia", "k": 3}
    )
    assert empty.status_code == 200
    assert empty.json()["items"] == []

    unavailable = _client(_FakeRetriever(unavailable=True)).post(
        "/evidence/search", json={"query": "hypertension", "k": 3}
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == "guideline retrieval unavailable"


def test_import_is_lazy_and_does_not_initialize_models_or_network() -> None:
    assert evidence._default_retriever is None


def test_request_scoped_demographics_reach_the_final_egress_screen() -> None:
    retriever = _FakeRetriever()
    app = FastAPI()
    app.state.evidence_retriever = retriever

    @app.middleware("http")
    async def attach_demographics(request, call_next):
        request.state.evidence_demographic_strings = ("Ada Lovelace", "1815-12-10")
        return await call_next(request)

    app.include_router(evidence.router)
    response = TestClient(app).post(
        "/evidence/search", json={"query": "hypertension", "k": 2}
    )
    assert response.status_code == 200
    assert retriever.calls == [
        ("hypertension", 2, ("Ada Lovelace", "1815-12-10"))
    ]


def test_defensive_query_rejection_is_a_typed_422_not_a_500() -> None:
    response = _client(_FakeRetriever(reject_query=True)).post(
        "/evidence/search", json={"query": "hypertension", "k": 2}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "query must contain PHI-free condition/test terms only"
