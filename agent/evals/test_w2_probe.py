"""Offline contracts for the bounded, non-gating live diagnostic subset."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.llm.provider import Usage
from evals.artifact_scan import scan_eval_result_paths
from evals.golden_loader import DEFAULT_MANIFEST, load_golden_cases
from app.schemas.citations import CitationSourceType, CitationV2
from evals.w2_models import (
    CanonicalAnswerEvidenceObservation,
    CaseObservation,
    GoldenCase,
    RenderedClaimObservation,
    RetrievalObservation,
    RunStatus,
)
from evals.w2_runner import (
    MAX_DIAGNOSTIC_CASES,
    LiveGateLimits,
    baseline_from_result,
    main,
    run_live_subset,
)

import evals.w2_runner as w2_runner


class _OfflineSubsetExecutor:
    """Source-independent fake: proves subset orchestration without provider egress."""

    def __init__(self) -> None:
        self.call_count = 0
        self.case_ids: list[str] = []
        self.cost_usd = 0.0
        self.latencies_ms: list[float] = []
        self.usage = Usage()
        self.retries = 0
        self.retrieval_hit_count = 0
        self.grounding_rates: list[float] = []

    async def __call__(self, case: GoldenCase) -> CaseObservation:
        self.call_count += 1
        self.case_ids.append(case.case_id)
        self.latencies_ms.append(1.0)
        self.grounding_rates.append(1.0)
        extraction_citation = case.expected_citations[0]
        page_label = extraction_citation.page_or_section
        assert isinstance(page_label, str) and page_label.startswith("page ")
        page = int(page_label.removeprefix("page "))
        answer_citation = extraction_citation.model_copy(
            update={"page_or_section": str(page)}
        )
        metadata = {
            "citation": answer_citation,
            "source_class": answer_citation.source_type,
            "overlay_source_id": answer_citation.source_id,
            "overlay_page": page,
            "overlay_bbox": {"x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2},
        }
        canonical = [CanonicalAnswerEvidenceObservation(**metadata)]
        rendered = [RenderedClaimObservation(**metadata)]
        # Honor the case's pinned production-retrieval behavior (R02): this offline
        # fake stays source-independent but must satisfy the strengthened scorer.
        retrieval = None
        expectation = case.expected_retrieval
        if expectation is not None:
            if expectation.outcome == "hit":
                top_chunk_ids = list(expectation.expected_top_chunk_ids)
                guideline_metadata = {
                    "citation": CitationV2(
                        source_type=CitationSourceType.GUIDELINE,
                        source_id="synthetic-guideline@offline-subset",
                        page_or_section="Synthetic guideline section",
                        field_or_chunk_id=top_chunk_ids[0],
                        quote_or_value="synthetic guideline quote",
                    ),
                    "source_class": CitationSourceType.GUIDELINE,
                }
                canonical.append(
                    CanonicalAnswerEvidenceObservation(**guideline_metadata)
                )
                rendered.append(RenderedClaimObservation(**guideline_metadata))
                retrieval = RetrievalObservation(
                    attempted=True, hit_chunk_ids=top_chunk_ids
                )
            elif expectation.outcome == "miss":
                retrieval = RetrievalObservation(attempted=True)
            elif expectation.outcome == "no_query":
                retrieval = RetrievalObservation(attempted=False)
            else:
                retrieval = RetrievalObservation(attempted=True, unavailable=True)
        return CaseObservation(
            case_id=case.case_id,
            fields=case.expected_fields,
            citations=case.expected_citations,
            canonical_answer_evidence=canonical,
            rendered_claims=rendered,
            verdict=case.expected_verdict,
            factual_judgement=True,
            retrieval=retrieval,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_ids",
    [
        ["not-a-manifest-case"],
        ["lab-clean-glucose", "lab-clean-glucose"],
        [case.case_id for case in load_golden_cases()[: MAX_DIAGNOSTIC_CASES + 1]],
    ],
    ids=("unknown", "duplicate", "over-limit"),
)
async def test_live_subset_rejects_invalid_selection_before_provider_construction(
    monkeypatch: pytest.MonkeyPatch,
    case_ids: list[str],
) -> None:
    def provider_must_not_be_built() -> None:
        raise AssertionError("invalid diagnostic selection reached the live provider")

    monkeypatch.setattr(w2_runner, "make_live_executor", provider_must_not_be_built)

    with pytest.raises(ValueError, match="diagnostic live subset"):
        await run_live_subset(case_ids=case_ids, manifest_path=DEFAULT_MANIFEST)


@pytest.mark.asyncio
async def test_live_subset_runs_only_selected_cases_in_canonical_manifest_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = load_golden_cases()
    canonical_ids = [cases[0].case_id, cases[1].case_id]
    executor = _OfflineSubsetExecutor()
    monkeypatch.setattr(w2_runner, "make_live_executor", lambda: executor)

    report, result = await run_live_subset(
        # Deliberately reverse the request: execution remains stable in manifest order.
        case_ids=list(reversed(canonical_ids)),
        manifest_path=DEFAULT_MANIFEST,
        live_limits=LiveGateLimits(max_cost_usd=0.25, max_seconds=5.0),
    )

    assert report.status is RunStatus.PASS
    assert executor.case_ids == canonical_ids
    assert result["tier"] == "live_subset"
    assert result["case_count"] == len(canonical_ids)
    assert result["executor_call_count"] == len(canonical_ids)
    assert [case["case_id"] for case in result["cases"]] == canonical_ids
    assert result["limits"] == {"max_cost_usd": 0.25, "max_seconds": 5.0}
    assert result["recordings_sha256"] is None
    assert all(category["baseline_score"] is None for category in result["categories"])

    with pytest.raises(ValueError, match="complete green live 50-case result"):
        baseline_from_result(result)


def test_diagnose_live_cli_writes_scanner_safe_non_gating_aggregate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    selected = load_golden_cases()[0].case_id
    executor = _OfflineSubsetExecutor()
    monkeypatch.setattr(w2_runner, "make_live_executor", lambda: executor)
    output = tmp_path / "live-subset.json"

    assert main(
        [
            "diagnose-live",
            "--case-id",
            selected,
            "--max-cost-usd",
            "0.25",
            "--max-seconds",
            "5",
            "--output",
            str(output),
        ]
    ) == 0

    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "PASS"
    assert result["tier"] == "live_subset"
    assert result["case_count"] == result["executor_call_count"] == 1
    assert result["cases"] == [
        {
            "case_id": selected,
            "status": "PASS",
            "rubrics": {
                "citation_present": True,
                "no_phi_in_logs": True,
                "schema_valid": True,
            },
        }
    ]
    assert scan_eval_result_paths([output]) == (True, 1, 0)

    with pytest.raises(ValueError, match="complete green live 50-case result"):
        baseline_from_result(result)
