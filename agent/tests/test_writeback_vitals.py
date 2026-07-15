"""Bounded grounded-vitals mapping tests (W2-D9/D10)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.ingestion.reader import NormBBox
from app.schemas.citations import CitationV2
from app.schemas.documents import FailureReason
from app.schemas.extraction import GroundedField, IntakeVitals, VitalCandidate


def _grounded(value, *, field_id: str):
    return GroundedField[type(value)](  # type: ignore[index]
        value=value,
        page=1,
        bbox=NormBBox(x0=0.1, y0=0.1, x1=0.2, y1=0.2),
        grounded=True,
        citation=CitationV2(
            source_type="uploaded_document",
            source_id="doc-synthetic-1",
            page_or_section="1",
            field_or_chunk_id=field_id,
            quote_or_value=str(value),
        ),
    )


def _candidate(value: str, unit: str, field_id: str = "weight") -> VitalCandidate:
    return VitalCandidate(
        value=_grounded(Decimal(value), field_id=f"{field_id}.value"),
        unit=_grounded(unit, field_id=f"{field_id}.unit"),
        measurement_date=_grounded(
            datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),
            field_id=f"{field_id}.measurement_date",
        ),
    )


def test_grounded_in_range_vital_maps_without_caller_attribution():
    from app.writeback.ranges import build_vital_writes

    result = build_vital_writes(
        IntakeVitals(weight=_candidate("180.5", "lb")),
        encounter_id="enc-synthetic-1",
        correlation_marker="corr-1",
    )

    assert len(result.writes) == 1
    write = result.writes[0]
    assert write.field_id == "weight"
    assert write.payload.weight == Decimal("180.5")
    assert write.payload.date == "2026-07-14 12:00:00"
    assert "user" not in write.payload.model_dump()
    assert "group" not in write.payload.model_dump()
    assert write.failure_reason is None


def test_out_of_range_vital_is_skipped_with_typed_reason():
    from app.writeback.ranges import build_vital_writes

    result = build_vital_writes(
        IntakeVitals(weight=_candidate("2000.1", "lb")),
        encounter_id="enc-synthetic-1",
        correlation_marker="corr-1",
    )

    assert result.writes == ()
    assert result.skipped[0].failure_reason is FailureReason.RANGE_VIOLATION


def test_unit_mismatch_is_skipped_and_never_converted():
    from app.writeback.ranges import build_vital_writes

    result = build_vital_writes(
        IntakeVitals(bps=_candidate("120", "kPa", "bps")),
        encounter_id="enc-synthetic-1",
        correlation_marker="corr-1",
    )

    assert result.writes == ()
    assert result.skipped[0].failure_reason is FailureReason.UNIT_MISMATCH


def test_no_encounter_produces_no_vital_write():
    from app.writeback.ranges import build_vital_writes

    result = build_vital_writes(
        IntakeVitals(weight=_candidate("180", "lb")),
        encounter_id=None,
        correlation_marker="corr-1",
    )
    assert result.writes == ()
    assert result.skipped[0].failure_reason is None
    assert result.skipped[0].reason == "no_encounter"
