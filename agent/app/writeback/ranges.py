"""Grounded intake-vitals mapping and deterministic physiological bounds.

No conversion is performed. A unit not explicitly pinned here is skipped with the
typed ``unit_mismatch`` reason; an out-of-range value is skipped with
``range_violation`` (W2-D9/D10, W2-F15/F16).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal

from app.schemas.citations import CitationV2
from app.schemas.documents import FailureReason
from app.schemas.extraction import IntakeVitals, NormBBox, VitalCandidate, VitalsWrite


@dataclass(frozen=True)
class VitalWriteCandidate:
    field_id: str
    payload: VitalsWrite
    citation: CitationV2
    bbox: NormBBox
    failure_reason: FailureReason | None = None


@dataclass(frozen=True)
class SkippedVital:
    field_id: str
    reason: str
    failure_reason: FailureReason | None


@dataclass(frozen=True)
class VitalMapping:
    writes: tuple[VitalWriteCandidate, ...]
    skipped: tuple[SkippedVital, ...]


# Inclusive ranges mirror this fork's VitalsFieldRanges (§3 / W2-D10).
_BOUNDS: dict[str, dict[str, tuple[Decimal, Decimal]]] = {
    "weight": {
        "lb": (Decimal("0"), Decimal("2000")),
        "kg": (Decimal("0"), Decimal("910")),
    },
    "height": {
        "in": (Decimal("0"), Decimal("150")),
        "cm": (Decimal("0"), Decimal("381")),
    },
    "bps": {"mmhg": (Decimal("0"), Decimal("400"))},
    "bpd": {"mmhg": (Decimal("0"), Decimal("300"))},
    "pulse": {
        "/min": (Decimal("0"), Decimal("500")),
        "bpm": (Decimal("0"), Decimal("500")),
    },
    "respiration": {
        "/min": (Decimal("0"), Decimal("150")),
        "breaths/min": (Decimal("0"), Decimal("150")),
    },
    "temperature": {
        "°f": (Decimal("0"), Decimal("120")),
        "f": (Decimal("0"), Decimal("120")),
        "°c": (Decimal("0"), Decimal("48.9")),
        "c": (Decimal("0"), Decimal("48.9")),
    },
    "oxygen_saturation": {"%": (Decimal("0"), Decimal("100"))},
}


def _unit(value: str) -> str:
    return value.strip().casefold().replace(" ", "")


def _skip(
    field_id: str, reason: str, failure_reason: FailureReason | None
) -> SkippedVital:
    return SkippedVital(field_id, reason, failure_reason)


def _eligible(candidate: VitalCandidate) -> bool:
    leaves = (candidate.value, candidate.unit, candidate.measurement_date)
    return all(
        leaf.grounded
        and leaf.value is not None
        and leaf.citation is not None
        and leaf.bbox is not None
        for leaf in leaves
    )


def _note(
    *, field_id: str, value: Decimal, unit: str, date: str, correlation_marker: str
) -> str:
    payload = "|".join((field_id, str(value), unit, date, correlation_marker))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"copilot-intent:{correlation_marker};payload:{digest}"


def build_vital_writes(
    vitals: IntakeVitals,
    *,
    encounter_id: str | None,
    correlation_marker: str,
) -> VitalMapping:
    """Map one permanent intent payload per eligible grounded vital field."""

    writes: list[VitalWriteCandidate] = []
    skipped: list[SkippedVital] = []
    for field_id in _BOUNDS:
        candidate = getattr(vitals, field_id)
        if candidate is None:
            continue
        if encounter_id is None:
            skipped.append(_skip(field_id, "no_encounter", None))
            continue
        if not _eligible(candidate):
            skipped.append(_skip(field_id, "unsupported", None))
            continue

        value = candidate.value.value
        raw_unit = candidate.unit.value
        measurement_date = candidate.measurement_date.value
        # _eligible proves these values and citation/bbox are present.
        assert value is not None
        assert raw_unit is not None
        assert measurement_date is not None
        normalized_unit = _unit(raw_unit)
        bounds = _BOUNDS[field_id].get(normalized_unit)
        if bounds is None:
            skipped.append(
                _skip(field_id, "unit_mismatch", FailureReason.UNIT_MISMATCH)
            )
            continue
        lower, upper = bounds
        if not lower <= value <= upper:
            skipped.append(
                _skip(field_id, "range_violation", FailureReason.RANGE_VIOLATION)
            )
            continue

        date = measurement_date.isoformat()
        payload = VitalsWrite(
            **{field_id: value},
            date=date,
            note=_note(
                field_id=field_id,
                value=value,
                unit=normalized_unit,
                date=date,
                correlation_marker=correlation_marker,
            ),
        )
        citation = candidate.value.citation
        bbox = candidate.value.bbox
        assert citation is not None
        assert bbox is not None
        writes.append(
            VitalWriteCandidate(
                field_id=field_id,
                payload=payload,
                citation=citation,
                bbox=bbox,
            )
        )
    return VitalMapping(tuple(writes), tuple(skipped))
