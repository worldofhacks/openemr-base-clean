"""R02 (AF-P0-02): the golden gate must traverse production hybrid retrieval.

These tests pin, RED-first, the seven R02 behaviors:

1.  The accepted evaluator route executes the production ``HybridRetriever`` over the
    committed corpus/index — never the retired term-overlap pseudo-retrieval.
2.  Tier 1 stays network-free and deterministic through recorded embedding/rerank
    adapters keyed like the existing provider recordings.
3.  The golden manifest asserts real retrieval behaviors: relevant guideline citation,
    healthy miss, no-query, retrieval-unavailable, rank stability, and claim/citation
    association.
4.  The aggregate eval result pins corpus version and model/config hashes.
5.  The recorded (PR) tier loads the committed baseline so the PDF's ">5 percentage
    point" category-regression rule binds at PR time.
6.  Two mutation drills (break ranking, break availability) turn the gate red.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus.retrieval import (
    EMBED_ONNX,
    EMBED_REVISION,
    EMBED_SOURCE_REPO,
    RERANK_MODEL,
    RERANK_ONNX,
    RERANK_REVISION,
    HybridRetriever,
    RetrievalUnavailableError,
)
from evals.execution import execute_source, finalize_typed_extraction
from evals.golden_loader import DEFAULT_MANIFEST, load_golden_cases
from evals.recorded_executor import DEFAULT_RECORDINGS, network_disabled
from evals.retrieval_adapters import (
    DEFAULT_RETRIEVAL_RECORDINGS,
    RecordedQueryEmbedder,
    RecordedReranker,
    RetrievalRecordingError,
    default_eval_retriever,
    load_retrieval_recordings,
    reset_cached_retriever,
    retrieval_provenance,
)
from evals.scorers import _retrieval_expectation_met, citation_present
from evals.w2_models import (
    CaseObservation,
    RetrievalExpectation,
    RetrievalObservation,
    Rubric,
    RunStatus,
)
from evals.w2_runner import DEFAULT_RECORDED_BASELINE, run_gate

import evals.execution as execution_module


CORPUS_DIR = Path(__file__).resolve().parents[1] / "corpus"


@pytest.fixture(autouse=True)
def _fresh_retriever_cache():
    reset_cached_retriever()
    yield
    reset_cached_retriever()


async def _run_recorded_gate():
    return await run_gate(
        tier="recorded",
        manifest_path=DEFAULT_MANIFEST,
        recordings_path=DEFAULT_RECORDINGS,
        baseline_path=Path("does-not-exist.json"),
    )


# --- 1. production retriever traversal ---------------------------------------------------


@pytest.mark.asyncio
async def test_accepted_evaluator_route_traverses_production_hybrid_retriever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recorded executor path must call ``HybridRetriever.search`` (AF-P0-02)."""

    calls: list[str] = []
    original_search = HybridRetriever.search

    def counting_search(self, query, **kwargs):
        calls.append(query)
        return original_search(self, query, **kwargs)

    monkeypatch.setattr(HybridRetriever, "search", counting_search)
    case = load_golden_cases()[0]  # lab-clean-glucose
    result = await execute_source(
        case_id=case.case_id,
        doc_type=case.doc_type,
        source_path=case.fixture_path,
        source_document_id=f"fixture:{case.case_id}",
    )
    assert calls == ["glucose"]
    assert result.retrieval_hit_count > 0
    observation = result.retrieval_observation
    assert observation is not None
    assert observation.attempted is True
    assert observation.unavailable is False
    assert observation.hit_chunk_ids
    assert observation.corpus_version
    assert observation.manifest_hash


# --- 2. offline determinism through recorded adapters ------------------------------------


def test_recorded_adapters_replay_production_search_under_network_disabled() -> None:
    index = load_retrieval_recordings(DEFAULT_RETRIEVAL_RECORDINGS)
    with network_disabled():
        retriever = default_eval_retriever()
        first = retriever.search("glucose", k=5)
        second = retriever.search("glucose", k=5)
    assert first.items
    assert [item.chunk_id for item in first.items] == [
        item.chunk_id for item in second.items
    ]
    assert first.degraded_reasons == ()
    assert index.embedder == f"{EMBED_SOURCE_REPO}@{EMBED_REVISION}:{EMBED_ONNX}"
    assert index.reranker == f"{RERANK_MODEL}@{RERANK_REVISION}:{RERANK_ONNX}"


def test_unrecorded_query_replays_the_production_unavailability_path() -> None:
    embedder = RecordedQueryEmbedder(load_retrieval_recordings())
    with pytest.raises(RetrievalUnavailableError):
        embedder.query_vector("query that was never recorded")


def test_recorded_index_binds_the_committed_corpus_and_models(tmp_path: Path) -> None:
    raw = json.loads(DEFAULT_RETRIEVAL_RECORDINGS.read_text(encoding="utf-8"))
    raw["corpus_manifest_sha256"] = "f" * 64
    stale = tmp_path / "retrieval.json"
    stale.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RetrievalRecordingError):
        load_retrieval_recordings(stale)


def test_recorded_reranker_scores_are_keyed_by_query_and_document() -> None:
    index = load_retrieval_recordings()
    reranker = RecordedReranker(index)
    chunks = [
        json.loads(line)
        for line in (CORPUS_DIR / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    quote = next(
        chunk["quote"] for chunk in chunks if "hba1c" in chunk["quote"].casefold()
    )
    scores = reranker.scores("hemoglobin a1c", [quote])
    assert len(scores) == 1
    assert 0.0 <= scores[0] <= 1.0


# --- 3. golden manifest pins the six retrieval behaviors ---------------------------------


def test_manifest_pins_all_required_retrieval_behaviors() -> None:
    cases = load_golden_cases()
    assert len(cases) == 50
    expectations = {
        case.case_id: case.expected_retrieval
        for case in cases
        if case.expected_retrieval is not None
    }
    outcomes = {expectation.outcome for expectation in expectations.values()}
    assert {"hit", "miss", "no_query", "unavailable"} <= outcomes

    hits = [item for item in expectations.values() if item.outcome == "hit"]
    # Relevant guideline citation + claim/citation association.
    assert any(item.require_rendered_guideline for item in hits)
    # Rank stability: at least one hit pins an exact ordered chunk-id prefix.
    assert any(len(item.expected_top_chunk_ids) >= 2 for item in hits)
    for item in hits:
        assert item.expected_top_chunk_ids


@pytest.mark.asyncio
async def test_guideline_hit_case_renders_the_expected_guideline_citation() -> None:
    from evals.recorded_executor import make_recorded_executor
    from app.schemas.citations import CitationSourceType

    case = next(
        item
        for item in load_golden_cases()
        if item.expected_retrieval is not None
        and item.expected_retrieval.outcome == "hit"
        and item.expected_retrieval.require_rendered_guideline
    )
    observation = await make_recorded_executor()(case)
    retrieval = observation.retrieval
    assert retrieval is not None
    expected_top = case.expected_retrieval.expected_top_chunk_ids[0]
    assert retrieval.hit_chunk_ids[0] == expected_top
    assert any(
        claim.source_class is CitationSourceType.GUIDELINE
        and getattr(claim.citation, "field_or_chunk_id", None) == expected_top
        for claim in observation.rendered_claims
    )
    assert citation_present(case, observation) is True


# --- scorer contracts for each retrieval outcome -----------------------------------------


def _observation_for(case, retrieval: RetrievalObservation | None) -> CaseObservation:
    return CaseObservation(
        case_id=case.case_id,
        fields={},
        citations=[],
        verdict="extract",
        retrieval=retrieval,
    )


def test_citation_present_fails_when_expected_retrieval_observation_is_absent() -> None:
    case = load_golden_cases()[0].model_copy(
        update={
            "expected_retrieval": RetrievalExpectation(
                outcome="miss",
            )
        }
    )
    assert citation_present(case, _observation_for(case, None)) is False


@pytest.mark.parametrize(
    ("expectation", "observation", "passes"),
    [
        # healthy miss: no hits, no degradation, nothing fabricated
        (
            RetrievalExpectation(outcome="miss"),
            RetrievalObservation(attempted=True, hit_chunk_ids=[]),
            True,
        ),
        # a miss expectation must fail when retrieval silently degraded
        (
            RetrievalExpectation(outcome="miss"),
            RetrievalObservation(
                attempted=True, hit_chunk_ids=[], degraded_reasons=["local_unavailable"]
            ),
            False,
        ),
        # a miss expectation must fail when there are surprise hits
        (
            RetrievalExpectation(outcome="miss"),
            RetrievalObservation(attempted=True, hit_chunk_ids=["chunk-1"]),
            False,
        ),
        # no query must mean retrieval was never attempted
        (
            RetrievalExpectation(outcome="no_query"),
            RetrievalObservation(attempted=False),
            True,
        ),
        (
            RetrievalExpectation(outcome="no_query"),
            RetrievalObservation(attempted=True, hit_chunk_ids=[]),
            False,
        ),
        # unavailability must be explicit, never silently green
        (
            RetrievalExpectation(outcome="unavailable"),
            RetrievalObservation(attempted=True, unavailable=True),
            True,
        ),
        (
            RetrievalExpectation(outcome="unavailable"),
            RetrievalObservation(attempted=True, unavailable=False),
            False,
        ),
        # rank stability: the observed ordered prefix must match exactly
        (
            RetrievalExpectation(
                outcome="hit", expected_top_chunk_ids=["chunk-a", "chunk-b"]
            ),
            RetrievalObservation(
                attempted=True, hit_chunk_ids=["chunk-a", "chunk-b", "chunk-c"]
            ),
            True,
        ),
        (
            RetrievalExpectation(
                outcome="hit", expected_top_chunk_ids=["chunk-a", "chunk-b"]
            ),
            RetrievalObservation(
                attempted=True, hit_chunk_ids=["chunk-b", "chunk-a", "chunk-c"]
            ),
            False,
        ),
        (
            RetrievalExpectation(
                outcome="hit", expected_top_chunk_ids=["chunk-a", "chunk-b"]
            ),
            RetrievalObservation(attempted=True, unavailable=True),
            False,
        ),
        # a silently degraded hit is not a stable production ranking
        (
            RetrievalExpectation(
                outcome="hit", expected_top_chunk_ids=["chunk-a"]
            ),
            RetrievalObservation(
                attempted=True,
                hit_chunk_ids=["chunk-a"],
                degraded_reasons=["local_unavailable"],
            ),
            False,
        ),
    ],
)
def test_retrieval_expectation_scoring_matrix(
    expectation: RetrievalExpectation,
    observation: RetrievalObservation,
    passes: bool,
) -> None:
    case = load_golden_cases()[0].model_copy(update={"expected_retrieval": expectation})
    assert (
        _retrieval_expectation_met(case, _observation_for(case, observation)) is passes
    )
    if not passes:
        # The full scorer can never pass when the retrieval expectation fails.
        assert citation_present(case, _observation_for(case, observation)) is False


def test_miss_expectation_rejects_fabricated_guideline_answer_evidence() -> None:
    from app.schemas.citations import CitationSourceType, CitationV2
    from evals.w2_models import CanonicalAnswerEvidenceObservation

    case = load_golden_cases()[0].model_copy(
        update={"expected_retrieval": RetrievalExpectation(outcome="miss")}
    )
    fabricated = CanonicalAnswerEvidenceObservation(
        citation=CitationV2(
            source_type=CitationSourceType.GUIDELINE,
            source_id="synthetic-guideline@deadbeef",
            page_or_section="Synthetic section",
            field_or_chunk_id="chunk-x",
            quote_or_value="synthetic guideline text",
        ),
        source_class=CitationSourceType.GUIDELINE,
    )
    observation = CaseObservation(
        case_id=case.case_id,
        fields={},
        citations=[],
        verdict="extract",
        canonical_answer_evidence=[fabricated],
        retrieval=RetrievalObservation(attempted=True, hit_chunk_ids=[]),
    )
    assert citation_present(case, observation) is False


# --- 4. corpus/config pins in the eval output --------------------------------------------


def test_retrieval_provenance_pins_corpus_and_model_hashes() -> None:
    provenance = retrieval_provenance()
    metadata = json.loads(
        (CORPUS_DIR / "index" / "metadata.json").read_text(encoding="utf-8")
    )
    assert provenance["corpus_version"] == metadata["corpus_version"]
    assert provenance["embedder"] == f"{EMBED_SOURCE_REPO}@{EMBED_REVISION}:{EMBED_ONNX}"
    assert provenance["reranker"] == f"{RERANK_MODEL}@{RERANK_REVISION}:{RERANK_ONNX}"
    assert len(provenance["corpus_manifest_sha256"]) == 64
    assert len(provenance["retrieval_recordings_sha256"]) == 64


@pytest.mark.asyncio
async def test_aggregate_recorded_result_carries_retrieval_pins() -> None:
    _report, result = await _run_recorded_gate()
    assert result["retrieval"] == retrieval_provenance()


# --- 5. recorded (PR) tier loads the committed baseline ----------------------------------


@pytest.mark.asyncio
async def test_recorded_tier_loads_committed_baseline_and_activates_delta_rule() -> None:
    report, result = await _run_recorded_gate()
    assert report.status is RunStatus.PASS
    assert DEFAULT_RECORDED_BASELINE.is_file()
    for category in result["categories"]:
        assert category["baseline_score"] is not None
        assert category["percentage_point_delta"] is not None
    factual = next(
        item for item in report.categories if item.rubric is Rubric.FACTUALLY_CONSISTENT
    )
    assert factual.baseline_score is not None
    assert factual.percentage_point_delta is not None


@pytest.mark.asyncio
async def test_recorded_tier_missing_baseline_fails_closed_in_ci(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CI", "true")
    with pytest.raises(ValueError, match="recorded baseline"):
        await run_gate(
            tier="recorded",
            manifest_path=DEFAULT_MANIFEST,
            recordings_path=DEFAULT_RECORDINGS,
            baseline_path=Path("does-not-exist.json"),
            recorded_baseline_path=tmp_path / "missing-recorded-baseline.json",
        )


@pytest.mark.asyncio
async def test_recorded_baseline_regression_greater_than_five_points_fails() -> None:
    """A committed baseline with factual=1.0 makes a synthetic 21/23 run red (>5pp)."""

    from evals.harness import aggregate_scores
    from evals.w2_models import CaseRubricResult, RecordedEvalBaseline

    baseline = RecordedEvalBaseline.model_validate_json(
        DEFAULT_RECORDED_BASELINE.read_text(encoding="utf-8")
    )
    factual_baseline = next(
        item
        for item in baseline.categories
        if item.rubric is Rubric.FACTUALLY_CONSISTENT
    )
    assert factual_baseline.score == 1.0
    denominator = factual_baseline.denominator
    rows = [
        CaseRubricResult(
            case_id=f"synthetic-{index}",
            rubric=Rubric.FACTUALLY_CONSISTENT,
            applicable=True,
            passed=index >= 2,
            detail="synthetic",
        )
        for index in range(denominator)
    ]
    summary = aggregate_scores(rows, baseline=baseline)[Rubric.FACTUALLY_CONSISTENT]
    assert summary.passed is False
    assert summary.trigger == "failed >5 percentage-point baseline regression"


# --- 6. mutation drills must turn the gate red -------------------------------------------


@pytest.mark.asyncio
async def test_ranking_mutation_drill_turns_the_gate_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drill (a): invert reranker ordering inside production retrieval -> gate FAIL."""

    from corpus.retrieval import RerankerSeam

    original_rerank = RerankerSeam.rerank

    def inverted_rerank(self, query, documents, **kwargs):
        scores, reason = original_rerank(self, query, documents, **kwargs)
        if scores is None:
            return scores, reason
        return [1.0 - score for score in scores], reason

    monkeypatch.setattr(RerankerSeam, "rerank", inverted_rerank)
    report, result = await _run_recorded_gate()
    assert report.status is RunStatus.FAIL
    citation = next(
        item for item in result["categories"] if item["rubric"] == "citation_present"
    )
    assert citation["passed"] is False


@pytest.mark.asyncio
async def test_availability_mutation_drill_turns_the_gate_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drill (b): make production retrieval unavailable -> gate FAIL, never silent green."""

    def broken_retriever():
        raise RetrievalUnavailableError("drill: retrieval backend unavailable")

    monkeypatch.setattr(execution_module, "default_eval_retriever", broken_retriever)
    report, result = await _run_recorded_gate()
    assert report.status is RunStatus.FAIL
    citation = next(
        item for item in result["categories"] if item["rubric"] == "citation_present"
    )
    assert citation["passed"] is False


# --- retired pseudo-retrieval ------------------------------------------------------------


@pytest.mark.asyncio
async def test_term_overlap_pseudo_retrieval_is_not_the_accepted_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_local_retrieve`` may exist for unit tests but must never serve the gate."""

    def poisoned_local_retrieve(*_args, **_kwargs):
        raise AssertionError("retired term-overlap retrieval was invoked by the gate")

    monkeypatch.setattr(execution_module, "_local_retrieve", poisoned_local_retrieve)
    case = load_golden_cases()[0]
    result = await execute_source(
        case_id=case.case_id,
        doc_type=case.doc_type,
        source_path=case.fixture_path,
        source_document_id=f"fixture:{case.case_id}",
    )
    assert result.retrieval_hit_count > 0
