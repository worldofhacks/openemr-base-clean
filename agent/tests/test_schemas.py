"""W2-M6 — Pydantic v2 canonical schema inventory: frozen failing tests (RED-first).

This is the field-for-field snapshot of the canonical contracts in
W2_ARCHITECTURE.md §2 (lines ~216–302), encoded as EXPLICIT test-authored
assertions so the snapshot cannot be satisfied by "whatever the implementation
happens to emit". Every model's exact field set, every enum's exact member set,
and the SAFETY-CRITICAL grounding/citation biconditional are pinned here. The
implementation conforms to these tests, never the other way around.

Traces: W2_ARCHITECTURE.md §2 (canonical inventory + composition rule), §2a
(CitationV2 migration mapping), W2-D3 (grounding), W2-D6 (citation shape, source
separation), W2-D10 (grounded intake-vitals + typed FailureReason skip reasons),
and docs/week2/W2_IMPLEMENTATION_PLAN.md W2-M6 (file inventory + accept criteria +
edge cases). PRD core requirement 2 (canonical schemas + validation tests).

FROZEN PUBLIC CONTRACT these tests pin (module → models):

- ``app.schemas.extraction``
    * ``GroundedField[T]`` — GENERIC leaf wrapper owning EXACTLY
      ``{value, page, bbox, grounded, citation}`` (§2). A construct-time model
      validator enforces the citation/grounding BICONDITIONAL (safety-critical,
      §2 composition rule / W2-D3):
        - ``grounded=True``  ⇒ a complete ``CitationV2`` (``citation is not None``)
          AND a ``bbox`` (``bbox is not None``); the field renders/writes as fact.
        - ``grounded=False`` ⇒ ``citation is None``; the field renders UNSUPPORTED
          and MUST NOT write as fact. It MAY still carry a ``bbox`` as an
          UNSUPPORTED review region.
      Both contradictions are REJECTED at construction with ``ValidationError``:
      (grounded=True + citation=None) and (grounded=False + citation set).
    * ``NormBBox`` — the canonical box, UNIFIED from the M4 reader: the same class
      object is re-exported by ``app.ingestion.reader`` (identity, not a copy).
    * ``LabResult`` — every leaf is a ``GroundedField`` of the right T; its
      ``collection_date`` is RESULT-level (not report-level), ``GroundedField[date]``.
    * ``LabPdfExtraction{results: list[LabResult], source_document_id}``.
    * ``Demographics``, ``VitalCandidate``, ``IntakeVitals``, ``IntakeFormExtraction``.
    * ``ExtractionArtifact``, ``VitalsWrite`` (per W2-D10).

- ``app.schemas.citations`` — ``CitationV2`` (exactly 5 PRD fields; closed
  ``source_type``), ``EvidenceSnippet``.

- ``app.schemas.handoff`` — ``HandoffRecord``, ``SupervisorDecision``,
  ``ReasonCode`` (closed enums, per-decision reason sets). This is the CANONICAL
  home; ``app.orchestrator.state`` re-exports the SAME class objects (identity).

- ``app.schemas.documents`` — ``UploadRequest``, ``UploadAccepted``,
  ``RetryRequest``, ``RetryAccepted``, ``DocumentStatus``, ``FailureReason``
  (closed 21-member enum).

- ``app.schemas.retrieval`` — ``EvidenceSearchRequest`` (``query`` non-empty;
  ``1 ≤ k ≤ K_MAX``), ``EvidenceSearchResponse``, module constant ``K_MAX``.

- ``app.schemas.jobs`` — ``JobRecord`` (closed ``state`` enum).

- ``app.schemas.writeback`` — ``WriteIntent`` (closed ``leg``/``state``),
  ``WriteResult``.

- ``app.schemas.workers`` — ``WorkerInput``, ``WorkerOutput`` (refs, not raw PHI).

- ``app.observability.events`` — ``LogEventEnvelope`` (the SOLE structured-log
  envelope; PHI-free attribute schema enforced).

Every canonical model carries ``model_config`` with ``extra="forbid"`` — an
unknown field raises ``pydantic.ValidationError``.

All data is synthetic and NON-CLINICAL; no PHI, no secrets, no network.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

# =======================================================================================
# Small shared helpers — synthetic constructors so the biconditional/round-trip tests do
# not each have to re-spell a valid CitationV2 / NormBBox / GroundedField.
# =======================================================================================


def _bbox(**over):
    """A valid canonical NormBBox (§2: ∈[0,1], x0<x1, y0<y1)."""
    from app.schemas.extraction import NormBBox

    kwargs = {"x0": 0.10, "y0": 0.20, "x1": 0.30, "y1": 0.40}
    kwargs.update(over)
    return NormBBox(**kwargs)


def _citation(**over):
    """A COMPLETE CitationV2 (all 5 PRD fields present)."""
    from app.schemas.citations import CitationV2

    kwargs = {
        "source_type": "uploaded_document",
        "source_id": "doc-123",
        "page_or_section": "1",
        "field_or_chunk_id": "field-hba1c",
        "quote_or_value": "6.5 %",
    }
    kwargs.update(over)
    return CitationV2(**kwargs)


def _grounded_str(value="6.5", **over):
    """A grounded GroundedField[str] with a complete citation + bbox (renders as fact)."""
    from app.schemas.extraction import GroundedField

    kwargs = {
        "value": value,
        "page": 1,
        "bbox": _bbox(),
        "grounded": True,
        "citation": _citation(quote_or_value=value),
    }
    kwargs.update(over)
    return GroundedField[str](**kwargs)


def _model_fields(model: type[BaseModel]) -> set[str]:
    return set(model.model_fields)


def _enum_values(enum_cls) -> set[str]:
    return {member.value for member in enum_cls}


def _config_forbids_extra(model: type[BaseModel]) -> bool:
    """True iff the model rejects unknown fields (extra='forbid')."""
    return model.model_config.get("extra") == "forbid"


# =======================================================================================
# §2 GroundedField[T] — the SAFETY-CRITICAL citation/grounding biconditional
# =======================================================================================


def test_grounded_field_owns_exactly_the_five_canonical_fields():
    # spec(W2-M6:grounded-field-shape) — §2 GroundedField[T]{value,page,bbox,grounded,citation}
    # guards: a leaf wrapper that grows/loses a field, breaking every downstream consumer.
    from app.schemas.extraction import GroundedField

    assert _model_fields(GroundedField) == {
        "value", "page", "bbox", "grounded", "citation"
    }
    assert _config_forbids_extra(GroundedField)


def test_grounded_field_extra_field_is_rejected():
    # spec(W2-M6:grounded-field-extra-forbid) — §2 extra="forbid"
    # guards: an extra="allow" leaf letting PHI or an invented flag ride alongside value.
    from app.schemas.extraction import GroundedField

    dump = _grounded_str().model_dump()
    GroundedField[str].model_validate(dump)  # control: the clean dump round-trips
    with pytest.raises(ValidationError):
        GroundedField[str].model_validate({**dump, "smuggled": "x"})


def test_grounded_true_requires_a_complete_citation():
    # spec(W2-M6:biconditional-forward) — §2 composition rule / W2-D3 (SAFETY-CRITICAL)
    # guards: a field claiming grounded=True (renders/writes as FACT) with NO citation —
    # exactly the invention the grounding contract exists to make impossible.
    from app.schemas.extraction import GroundedField

    with pytest.raises(ValidationError):
        GroundedField[str](
            value="6.5", page=1, bbox=_bbox(), grounded=True, citation=None
        )


def test_grounded_false_forbids_a_citation():
    # spec(W2-M6:biconditional-reverse) — §2 composition rule (SAFETY-CRITICAL)
    # guards: an UNSUPPORTED field carrying a citation — it would render/write as fact
    # despite grounding having FAILED.
    from app.schemas.extraction import GroundedField

    with pytest.raises(ValidationError):
        GroundedField[str](
            value="6.5", page=1, bbox=_bbox(), grounded=False, citation=_citation()
        )


def test_grounded_true_requires_a_bbox():
    # spec(W2-M6:grounded-requires-bbox) — §2 / plan accept ("grounded requires citation+bbox")
    # guards: a grounded fact with no on-page location — the overlay/write has nothing to
    # anchor to.
    from app.schemas.extraction import GroundedField

    with pytest.raises(ValidationError):
        GroundedField[str](
            value="6.5", page=1, bbox=None, grounded=True, citation=_citation()
        )


def test_grounded_true_with_complete_citation_and_bbox_is_valid():
    # spec(W2-M6:biconditional-positive) — §2 (the fact path)
    field = _grounded_str("6.5")
    assert field.grounded is True
    assert field.citation is not None
    assert field.bbox is not None
    assert field.value == "6.5"


def test_ungrounded_field_with_no_citation_is_valid_and_may_keep_a_bbox():
    # spec(W2-M6:unsupported-review-region) — §2 / plan ("may retain a bbox as an
    # UNSUPPORTED review region")
    # guards: forbidding the review-region bbox on ungrounded fields — the "verify against
    # source document" overlay needs it even when grounding FAILED.
    from app.schemas.extraction import GroundedField

    # ungrounded WITHOUT a bbox is valid …
    bare = GroundedField[str](
        value="6.5", page=1, bbox=None, grounded=False, citation=None
    )
    assert bare.grounded is False and bare.citation is None

    # … and ungrounded WITH a bbox (review region) is ALSO valid — but still no citation.
    review = GroundedField[str](
        value="6.5", page=1, bbox=_bbox(), grounded=False, citation=None
    )
    assert review.grounded is False
    assert review.citation is None
    assert review.bbox is not None


# =======================================================================================
# §2 NormBBox UNIFICATION — the M4 shape IS the canonical §2 shape (identity, not a copy)
# =======================================================================================


def test_normbbox_is_the_same_class_object_across_reader_and_schemas():
    # spec(W2-M6:normbbox-unification) — §2 "the shape unified into the canonical module,
    # not duplicated". Identity assertion: the M4 reader must RE-EXPORT the schemas class.
    # guards: a second, divergent NormBBox definition — two coordinate spaces that silently
    # disagree would corrupt every overlay.
    import app.ingestion.reader as reader
    import app.schemas.extraction as extraction

    assert reader.NormBBox is extraction.NormBBox


def test_normbbox_canonical_invariants_still_hold():
    # spec(W2-M6:normbbox-invariants) — §2 (∈[0,1], x0<x1, y0<y1, frozen, extra=forbid)
    # Re-asserted here so a divergent re-definition of the canonical box fails THIS test.
    from app.schemas.extraction import NormBBox

    assert _config_forbids_extra(NormBBox)
    assert NormBBox.model_config.get("frozen") is True
    assert _model_fields(NormBBox) == {"x0", "y0", "x1", "y1"}

    # Boundary endpoints 0.0 and 1.0 are VALID.
    NormBBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0)

    # Out-of-range rejects.
    with pytest.raises(ValidationError):
        NormBBox(x0=0.0, y0=0.0, x1=1.5, y1=1.0)
    # Inverted / degenerate rejects.
    with pytest.raises(ValidationError):
        NormBBox(x0=0.5, y0=0.0, x1=0.5, y1=1.0)  # x0 == x1
    with pytest.raises(ValidationError):
        NormBBox(x0=0.6, y0=0.0, x1=0.4, y1=1.0)  # x0 > x1
    # Frozen: no mutation after construction.
    box = NormBBox(x0=0.1, y0=0.1, x1=0.2, y1=0.2)
    with pytest.raises(ValidationError):
        box.x0 = 0.3


def test_normbbox_rejects_unknown_field():
    # spec(W2-M6:normbbox-extra-forbid) — §2 extra="forbid"
    from app.schemas.extraction import NormBBox

    with pytest.raises(ValidationError):
        NormBBox(x0=0.1, y0=0.1, x1=0.2, y1=0.2, z=0.5)


# =======================================================================================
# §2 HandoffRecord RECONCILIATION — canonical home is schemas.handoff; orchestrator re-exports
# =======================================================================================


def test_handoff_record_and_decision_are_the_same_objects_across_state_and_handoff():
    # spec(W2-M6:handoff-reconciliation) — §2 "canonical home is schemas.handoff;
    # orchestrator re-exports". Identity assertions: the M3 orchestrator.state must
    # RE-EXPORT the schemas.handoff classes, not define parallel ones.
    # guards: two HandoffRecord shapes drifting apart — the supervisor-worker boundary and
    # the schema inventory disagreeing on the audited hop record.
    import app.orchestrator.state as state
    import app.schemas.handoff as handoff

    assert state.HandoffRecord is handoff.HandoffRecord
    assert state.SupervisorDecision is handoff.SupervisorDecision


def test_supervisor_decision_is_the_closed_critic_aware_member_set():
    # spec(W2-M6:supervisor-decision-closed) — §2 closed decision vocabulary
    from app.schemas.handoff import SupervisorDecision

    assert _enum_values(SupervisorDecision) == {
        "route_extract", "route_retrieve", "compose_answer", "review_critic",
        "critic_approve", "critic_reject", "refuse", "done",
    }


def test_handoff_record_shape_and_per_decision_reason_sets_hold():
    # spec(W2-M6:handoff-shape) — §2 HandoffRecord fields + per-decision closed reason sets
    # guards: a HandoffRecord that lost/gained a field or accepted a decision/reason
    # mismatch, making the routing audit unreconstructable.
    from app.schemas.handoff import HandoffRecord, ReasonCode, SupervisorDecision

    assert _model_fields(HandoffRecord) == {
        "correlation_id", "turn", "supervisor_decision", "reason_code", "worker",
        "input_ref", "output_ref", "handoff_ts",
    }
    assert _config_forbids_extra(HandoffRecord)

    def _record(decision, reason):
        return HandoffRecord(
            correlation_id="corr-1", turn=0, supervisor_decision=decision,
            reason_code=reason, worker="extractor", input_ref="in-1",
            output_ref="out-1", handoff_ts="2026-07-14T12:00:00+00:00",
        )

    # A decision paired with a reason OUTSIDE its closed set must reject (both dimensions
    # closed). route_extract's legal reason is not step_budget_exceeded (a refuse reason).
    _record(SupervisorDecision.ROUTE_EXTRACT, ReasonCode.EXTRACTION_REQUESTED)  # legal
    with pytest.raises(ValidationError):
        _record(SupervisorDecision.ROUTE_EXTRACT, ReasonCode.STEP_BUDGET_EXCEEDED)


# =======================================================================================
# §2 / D6 CitationV2 — exactly 5 PRD fields; closed source_type; incomplete = invalid
# =======================================================================================


def test_citation_v2_has_exactly_the_five_prd_fields():
    # spec(W2-M6:citation-five-fields) — §2 / W2-D6 (the five prescribed citation fields)
    from app.schemas.citations import CitationV2

    assert _model_fields(CitationV2) == {
        "source_type", "source_id", "page_or_section", "field_or_chunk_id",
        "quote_or_value",
    }
    assert _config_forbids_extra(CitationV2)


def test_citation_v2_source_type_is_the_closed_three_member_set():
    # spec(W2-M6:citation-source-type-closed) — §2 / W2-D6 source separation
    # guards: a free-text source_type letting patient facts and guideline evidence blur —
    # the UI can no longer render them visually distinct (PRD requirement).
    _citation(source_type="patient_record", page_or_section=None)
    _citation(source_type="uploaded_document", page_or_section="1")
    _citation(source_type="guideline", page_or_section="Recommendations")
    with pytest.raises(ValidationError):
        _citation(source_type="wikipedia")


@pytest.mark.parametrize("page_or_section", ["1", "section", " "])
def test_patient_record_citation_requires_null_location(page_or_section: str):
    with pytest.raises(ValidationError, match="page_or_section=null"):
        _citation(source_type="patient_record", page_or_section=page_or_section)


@pytest.mark.parametrize("source_type", ["uploaded_document", "guideline"])
@pytest.mark.parametrize("page_or_section", [None, "", "   "])
def test_document_and_guideline_citations_require_location(
    source_type: str, page_or_section: str | None
):
    with pytest.raises(ValidationError, match="require a page or section"):
        _citation(source_type=source_type, page_or_section=page_or_section)


def test_incomplete_citation_is_rejected():
    # spec(W2-M6:citation-incomplete-invalid) — §2 / W2-D6 "incomplete citation = claim
    # does not render". A citation missing ANY of the 5 fields is invalid.
    # guards: a half-formed citation being treated as grounding — a claim rendering as fact
    # without a resolvable source.
    from app.schemas.citations import CitationV2

    complete = {
        "source_type": "uploaded_document", "source_id": "doc-1",
        "page_or_section": "1", "field_or_chunk_id": "f1", "quote_or_value": "6.5",
    }
    for missing in complete:
        partial = {k: v for k, v in complete.items() if k != missing}
        with pytest.raises(ValidationError):
            CitationV2(**partial)


# =======================================================================================
# §2 FailureReason — the closed 21-member enum; no free-text substitute
# =======================================================================================


_FAILURE_REASONS = {
    "patient_mismatch", "encounter_mismatch", "unit_mismatch", "range_violation",
    "scope_mismatch", "category_mismatch", "binary_readback_unsafe", "upload_rejected",
    "unsupported_media_type", "size_or_page_cap_exceeded", "storage_write_failed",
    "ocr_failed", "vlm_timeout", "vlm_unavailable", "schema_violation", "auth_expired",
    "writeback_failed", "writeback_verify_failed", "doc_type_mismatch", "worker_restart",
}


def test_failure_reason_is_the_closed_enumerated_member_set():
    # spec(W2-M6:failure-reason-closed) — §2 lines 271–276 (the complete closed enum)
    # guards: a missing/extra reason code — a §5 row, log event, or negative test with no
    # enum member to map to, or a smuggled free-text reason.
    from app.schemas.documents import FailureReason

    # §2 lines 271–276 enumerate EXACTLY 20 distinct member names (the task prose's
    # "21-member" figure is a miscount of that same explicit list — the authoritative
    # source is the enumerated names, pinned member-for-member here).
    assert len(_FAILURE_REASONS) == 20  # sanity on the fixed set spelled out above
    assert _enum_values(FailureReason) == _FAILURE_REASONS


def test_document_status_reason_accepts_every_failure_reason_member():
    # spec(W2-M6:failure-reason-usable) — §2 (every member maps to a DocumentStatus.reason)
    # guards: an enum member the status model can't actually carry.
    from app.schemas.documents import DocumentStatus, FailureReason

    for reason in FailureReason:
        status = DocumentStatus(
            document_id="doc-1", state="failed", reason=reason,
            correlation_id="corr-1", updated_ts="2026-07-14T12:00:00+00:00",
            fields_grounded=0, fields_unsupported=0, attempt_count=1, next_retry_at=None,
        )
        assert status.reason is reason


def test_document_status_reason_rejects_free_text():
    # spec(W2-M6:no-free-text-reason) — §2 "no free-text reason substitutes for an enum"
    from app.schemas.documents import DocumentStatus

    with pytest.raises(ValidationError):
        DocumentStatus(
            document_id="doc-1", state="failed", reason="something_went_wrong",
            correlation_id="corr-1", updated_ts="2026-07-14T12:00:00+00:00",
            fields_grounded=0, fields_unsupported=0, attempt_count=1, next_retry_at=None,
        )


def test_document_status_field_set_and_extra_forbid():
    # spec(W2-M6:document-status-shape) — §2 DocumentStatus inventory
    from app.schemas.documents import DocumentStatus

    assert _model_fields(DocumentStatus) == {
        "document_id", "state", "reason", "correlation_id", "updated_ts",
        "fields_grounded", "fields_unsupported", "attempt_count", "next_retry_at",
    }
    assert _config_forbids_extra(DocumentStatus)


def test_retry_request_and_accepted_shapes():
    # spec(W2-M6:retry-shapes) — §2 RetryRequest{expected_state:"failed"} / RetryAccepted
    from app.schemas.documents import RetryAccepted, RetryRequest

    assert _model_fields(RetryAccepted) == {
        "job_id", "document_id", "state", "status_url", "correlation_id",
    }
    assert _config_forbids_extra(RetryRequest)
    assert _config_forbids_extra(RetryAccepted)
    # expected_state is pinned to the literal "failed" (only a failed job is retryable).
    RetryRequest(expected_state="failed")
    with pytest.raises(ValidationError):
        RetryRequest(expected_state="queued")


def test_upload_request_and_accepted_exist_and_forbid_extra():
    # spec(W2-M6:upload-shapes) — §2 UploadRequest / UploadAccepted
    from app.schemas.documents import UploadAccepted, UploadRequest

    assert _config_forbids_extra(UploadRequest)
    assert _config_forbids_extra(UploadAccepted)


# =======================================================================================
# §2 LabResult / LabPdfExtraction — every leaf is a GroundedField of the right T
# =======================================================================================


def test_lab_result_leaves_are_grounded_fields_of_the_right_types():
    # spec(W2-M6:labresult-leaves) — §2 LabResult inventory; collection_date is RESULT-level
    # guards: a report-level collection_date (superseded shape) or a bare-string leaf that
    # skips the grounding wrapper.
    from app.schemas.extraction import GroundedField, LabResult

    assert _model_fields(LabResult) == {
        "test_name", "value", "unit", "reference_range", "abnormal_flag",
        "collection_date",
    }
    assert _config_forbids_extra(LabResult)

    # Each str leaf is a GroundedField[str]; collection_date is GroundedField[date] — a
    # RESULT-level grounded date, not a report-level one. Constructing the model with those
    # exact leaf types proves the T bindings and the result-level placement.
    grounded_date = GroundedField[date](
        value=date(2026, 1, 15), page=1, bbox=_bbox(), grounded=True,
        citation=_citation(quote_or_value="2026-01-15"),
    )
    result = LabResult(
        test_name=_grounded_str("HbA1c"),
        value=_grounded_str("6.5"),
        unit=_grounded_str("%"),
        reference_range=_grounded_str("4.0-5.6"),
        abnormal_flag=_grounded_str("H"),
        collection_date=grounded_date,
    )
    assert result.collection_date.value == date(2026, 1, 15)


def test_lab_pdf_extraction_empty_results_is_valid():
    # spec(W2-M6:empty-results) — plan edge case: empty LabPdfExtraction.results
    from app.schemas.extraction import LabPdfExtraction

    assert _model_fields(LabPdfExtraction) == {"results", "source_document_id"}
    assert _config_forbids_extra(LabPdfExtraction)
    empty = LabPdfExtraction(results=[], source_document_id="doc-1")
    assert empty.results == []


def test_lab_result_bad_date_is_rejected():
    # spec(W2-M6:bad-date) — plan edge case: invalid date string → reject
    # guards: a malformed collection_date silently coerced, corrupting trend queries.
    from app.schemas.extraction import GroundedField

    with pytest.raises(ValidationError):
        GroundedField[date](
            value="not-a-date", page=1, bbox=_bbox(), grounded=True,
            citation=_citation(),
        )


def test_multi_date_lab_results_round_trip_distinctly():
    # spec(W2-M6:multi-date) — §2 / plan "Multi-date fixtures must round-trip distinct
    # result dates". Two results with DISTINCT collection_dates survive round-trip distinct.
    from app.schemas.extraction import GroundedField, LabPdfExtraction, LabResult

    def _result(iso, day):
        return LabResult(
            test_name=_grounded_str("HbA1c"),
            value=_grounded_str("6.5"),
            unit=_grounded_str("%"),
            reference_range=_grounded_str("4.0-5.6"),
            abnormal_flag=_grounded_str("H"),
            collection_date=GroundedField[date](
                value=day, page=1, bbox=_bbox(), grounded=True,
                citation=_citation(quote_or_value=iso),
            ),
        )

    extraction = LabPdfExtraction(
        results=[
            _result("2026-01-15", date(2026, 1, 15)),
            _result("2026-04-20", date(2026, 4, 20)),
        ],
        source_document_id="doc-1",
    )
    reloaded = LabPdfExtraction.model_validate(extraction.model_dump())
    dates = [r.collection_date.value for r in reloaded.results]
    assert dates == [date(2026, 1, 15), date(2026, 4, 20)]
    assert dates[0] != dates[1]


# =======================================================================================
# §2 / D10 IntakeVitals / VitalCandidate / IntakeFormExtraction
# =======================================================================================

_VITAL_FIELDS = {
    "bps", "bpd", "weight", "height", "temperature", "pulse", "respiration",
    "oxygen_saturation",
}


def test_intake_vitals_owns_exactly_the_eight_optional_vital_candidates():
    # spec(W2-M6:intake-vitals-eight) — §2 / W2-D10 the 8 vitals, each VitalCandidate|None
    from app.schemas.extraction import IntakeVitals

    assert _model_fields(IntakeVitals) == _VITAL_FIELDS
    assert _config_forbids_extra(IntakeVitals)
    # All-None (nothing measured) is valid — every vital is optional.
    empty = IntakeVitals()
    for field in _VITAL_FIELDS:
        assert getattr(empty, field) is None


def test_vital_candidate_shape_and_types():
    # spec(W2-M6:vital-candidate-shape) — §2 / W2-D10 VitalCandidate leaves
    from app.schemas.extraction import VitalCandidate

    assert _model_fields(VitalCandidate) == {"value", "unit", "measurement_date"}
    assert _config_forbids_extra(VitalCandidate)


def test_vital_candidate_decimal_value_and_unit_survive_round_trip_without_coercion():
    # spec(W2-M6:unit-preservation) — §2 / W2-D10; plan edge case "unit preservation"
    # guards: a Decimal weight coerced to float (precision loss) or a dropped unit string —
    # either would fail the unit-mismatch/range checks silently.
    from app.schemas.extraction import GroundedField, IntakeVitals, VitalCandidate

    candidate = VitalCandidate(
        value=GroundedField[Decimal](
            value=Decimal("70.5"), page=1, bbox=_bbox(), grounded=True,
            citation=_citation(quote_or_value="70.5"),
        ),
        unit=GroundedField[str](
            value="kg", page=1, bbox=_bbox(), grounded=True,
            citation=_citation(quote_or_value="kg"),
        ),
        measurement_date=GroundedField[datetime](
            value=datetime(2026, 1, 15, 9, 30, tzinfo=timezone.utc), page=1,
            bbox=_bbox(), grounded=True, citation=_citation(quote_or_value="2026-01-15"),
        ),
    )
    vitals = IntakeVitals(weight=candidate)
    reloaded = IntakeVitals.model_validate(vitals.model_dump())
    assert reloaded.weight is not None
    # Decimal is preserved as Decimal, exact value — NOT coerced to float.
    assert isinstance(reloaded.weight.value.value, Decimal)
    assert reloaded.weight.value.value == Decimal("70.5")
    assert reloaded.weight.unit.value == "kg"


def test_intake_form_extraction_has_clinical_fields_and_note_is_not_extracted():
    # spec(W2-M6:intake-form-shape) — §2 / W2-D10; "note is generated provenance, never
    # an extracted field". Assert the extracted clinical fields are present and ``note`` is
    # absent from the extracted-field set (provenance-only, not an extracted leaf).
    from app.schemas.extraction import IntakeFormExtraction

    fields = _model_fields(IntakeFormExtraction)
    assert {
        "demographics", "chief_concern", "current_medications", "allergies",
        "family_history", "vitals", "source_document_id",
    } <= fields
    assert "note" not in fields, "note is generated provenance, never an extracted field"
    assert _config_forbids_extra(IntakeFormExtraction)


def test_demographics_exists_and_forbids_extra():
    # spec(W2-M6:demographics-shape) — §2 Demographics{name, dob, sex, contact, ...}
    from app.schemas.extraction import Demographics

    assert _config_forbids_extra(Demographics)
    # The named demographics leaves are present (the "..." leaves room for more).
    assert {"name", "dob", "sex", "contact"} <= _model_fields(Demographics)


# =======================================================================================
# §2 EvidenceSearchRequest / EvidenceSnippet / EvidenceSearchResponse — bounded k
# =======================================================================================


def test_evidence_search_request_query_non_empty_and_k_bounded():
    # spec(W2-M6:evidence-request-bounds) — §2 EvidenceSearchRequest (query non-empty;
    # 1 ≤ k ≤ K_MAX). Import K_MAX from the module (the bound is a named constant).
    # guards: an empty query or an out-of-range k reaching the retriever.
    from app.schemas.retrieval import K_MAX, EvidenceSearchRequest

    assert isinstance(K_MAX, int) and K_MAX >= 1
    assert _model_fields(EvidenceSearchRequest) == {"query", "k"}
    assert _config_forbids_extra(EvidenceSearchRequest)

    # Valid bounds.
    EvidenceSearchRequest(query="hypertension management", k=1)
    EvidenceSearchRequest(query="hypertension management", k=K_MAX)

    # Empty query rejects.
    with pytest.raises(ValidationError):
        EvidenceSearchRequest(query="", k=5)
    with pytest.raises(ValidationError):
        EvidenceSearchRequest(query="x" * 181, k=5)

    # k below/above the bounds rejects.
    with pytest.raises(ValidationError):
        EvidenceSearchRequest(query="hypertension management", k=0)
    with pytest.raises(ValidationError):
        EvidenceSearchRequest(query="hypertension management", k=K_MAX + 1)


def test_evidence_snippet_shape():
    # spec(W2-M6:evidence-snippet-shape) — §2 EvidenceSnippet inventory
    from app.schemas.citations import EvidenceSnippet

    assert _model_fields(EvidenceSnippet) == {
        "source_id", "section", "chunk_id", "quote", "score", "corpus_version",
    }
    assert _config_forbids_extra(EvidenceSnippet)


def test_evidence_search_response_shape():
    # spec(W2-M6:evidence-response-shape) — §2 EvidenceSearchResponse inventory
    from app.schemas.retrieval import EvidenceSearchResponse

    assert _model_fields(EvidenceSearchResponse) == {
        "items", "corpus_version", "correlation_id",
    }
    assert _config_forbids_extra(EvidenceSearchResponse)


# =======================================================================================
# §2 / D10 JobRecord / WriteIntent / WriteResult — closed state/leg enums
# =======================================================================================


def test_job_record_shape_and_closed_state_enum():
    # spec(W2-M6:job-record) — §2 JobRecord inventory + closed state enum
    from app.schemas.jobs import JobRecord

    assert _model_fields(JobRecord) == {
        "job_id", "document_id", "patient_id", "content_hash", "correlation_id",
        "credential_ref", "state", "claim_owner", "lease_expires_at", "heartbeat_at",
        "attempt_count", "next_attempt_at", "created_ts", "updated_ts",
    }
    assert _config_forbids_extra(JobRecord)

    # The state vocabulary is exactly the §2 closed set.
    from app.schemas.jobs import JobState  # closed enum owning the states

    assert _enum_values(JobState) == {
        "storing", "reconciling", "queued", "extracting", "grounding", "writing",
        "complete", "failed",
    }


def test_write_intent_closed_leg_and_state_enums():
    # spec(W2-M6:write-intent) — §2 / W2-D10 WriteIntent leg ∈ {source_document,
    # extraction_artifact, vital}, state ∈ {pending, unknown, complete}
    from app.schemas.writeback import WriteIntent

    assert _model_fields(WriteIntent) == {
        "intent_id", "patient_id", "document_id_or_content_hash", "leg", "version",
        "field_id", "correlation_marker", "payload_hash", "state", "remote_id",
        "attempt_count", "updated_ts",
    }
    assert _config_forbids_extra(WriteIntent)

    from app.schemas.writeback import WriteLeg, WriteState

    assert _enum_values(WriteLeg) == {
        "source_document", "extraction_artifact", "vital",
    }
    assert _enum_values(WriteState) == {"pending", "unknown", "complete"}


def test_write_result_shape():
    # spec(W2-M6:write-result) — §2 WriteResult inventory
    from app.schemas.writeback import WriteResult

    assert _model_fields(WriteResult) == {
        "intent_id", "state", "remote_id", "payload_hash", "verified", "failure_reason",
    }
    assert _config_forbids_extra(WriteResult)


# =======================================================================================
# §2 WorkerInput / WorkerOutput — refs, not raw PHI
# =======================================================================================


def test_worker_input_and_output_field_sets_are_refs_not_phi():
    # spec(W2-M6:worker-io) — §2 WorkerInput/WorkerOutput are the ONLY supervisor/worker
    # payloads; refs (ids), not raw PHI, cross the handoff boundary.
    # guards: a worker payload growing a raw-PHI field, leaking clinical values across the
    # supervisor-worker boundary.
    from app.schemas.workers import WorkerInput, WorkerOutput

    assert _model_fields(WorkerInput) == {
        "correlation_id", "turn", "patient_ref", "document_refs", "evidence_refs",
        "request_kind",
    }
    assert _model_fields(WorkerOutput) == {
        "correlation_id", "worker", "status", "artifact_refs", "citation_refs",
        "reason_code",
    }
    assert _config_forbids_extra(WorkerInput)
    assert _config_forbids_extra(WorkerOutput)


# =======================================================================================
# §2 ExtractionArtifact / VitalsWrite — the persisted artifact + the typed vitals write
# =======================================================================================


def test_extraction_artifact_shape():
    # spec(W2-M6:extraction-artifact) — §2 ExtractionArtifact inventory
    from app.schemas.extraction import ExtractionArtifact

    assert _model_fields(ExtractionArtifact) == {
        "artifact_version", "document_id", "content_hash", "correlation_id", "doc_type",
        "extraction", "grounding_summary", "created_ts", "agent_version",
    }
    assert _config_forbids_extra(ExtractionArtifact)


def test_vitals_write_carries_no_caller_attribution_fields():
    # spec(W2-M6:vitals-write) — §2 / W2-D10 VitalsWrite "contains no caller user/group
    # fields" (W2-F16: caller attribution is stripped).
    # guards: a VitalsWrite that re-admits a request-body user/group performer — exactly the
    # attribution spoofing W2-F16 forbids.
    from app.schemas.extraction import VitalsWrite

    fields = _model_fields(VitalsWrite)
    assert "user" not in fields
    assert "group" not in fields
    # The typed vital slots + date + note are present (note is generated provenance HERE,
    # on the write, not an extracted field).
    assert _VITAL_FIELDS <= fields
    assert {"date", "note"} <= fields
    assert _config_forbids_extra(VitalsWrite)


# =======================================================================================
# §2 LogEventEnvelope — the sole event envelope; PHI-free attribute schema enforced
# =======================================================================================


def test_log_event_envelope_field_set_and_optional_ids_nullable():
    # spec(W2-M6:log-envelope-shape) — §2 LogEventEnvelope inventory; optional IDs explicit None
    from app.observability.events import LogEventEnvelope

    assert _model_fields(LogEventEnvelope) == {
        "schema_version", "event_id", "event_type", "occurred_at", "case_id", "job_id",
        "correlation_id", "component", "severity", "attributes",
    }
    assert _config_forbids_extra(LogEventEnvelope)

    # Optional IDs (case_id, job_id, correlation_id) are explicitly None-able.
    envelope = LogEventEnvelope(
        schema_version=1, event_id="evt-1", event_type="job.claimed",
        occurred_at="2026-07-14T12:00:00+00:00", case_id=None, job_id=None,
        correlation_id=None, component="worker", severity="info", attributes={},
    )
    assert envelope.case_id is None
    assert envelope.job_id is None
    assert envelope.correlation_id is None


def test_log_event_envelope_attributes_reject_raw_phi_and_free_form_bodies():
    # spec(W2-M6:log-envelope-redaction) — §2 "attributes permits only the approved PHI-free
    # scalar/list schema, never raw document text or extracted values" (D5 / W2-D7 posture).
    # guards: a log event carrying raw document text, an extracted value, token material, or
    # a free-form exception body — the no_phi_in_logs invariant (W2-D5) broken at the source.
    from app.observability.events import LogEventEnvelope

    def _envelope(attributes):
        return LogEventEnvelope(
            schema_version=1, event_id="evt-1", event_type="job.failed",
            occurred_at="2026-07-14T12:00:00+00:00", case_id="case-1", job_id="job-1",
            correlation_id="corr-1", component="worker", severity="error",
            attributes=attributes,
        )

    # PHI-free scalar/list attributes are allowed.
    _envelope({"reason": "vlm_timeout", "attempt_count": 3, "legs": ["source", "vital"]})

    # A nested dict / structured object is NOT an approved scalar-or-list attribute value —
    # it is where raw document text, extracted values, or exception bodies would ride.
    with pytest.raises(ValidationError):
        _envelope({"raw_document": {"text": "patient John Doe HbA1c 6.5%"}})

    # A free-form exception body (multi-line traceback-shaped blob) is rejected. The
    # approved schema carries a reason CODE, not an arbitrary message body.
    with pytest.raises(ValidationError):
        _envelope({
            "exception_body": "Traceback (most recent call last):\n  File ...\n"
                              "ValueError: patient MRN 12345 not found in ...",
        })
