"""EvidencePacket builder — the single source the LLM and verifier see (ARCHITECTURE.md §5, §6a).

Normalizes the six tools' `ToolResult`s into typed `EvidenceRecord`s, each with a
STABLE, UNIQUE `evidence_id` of the §5a form `ResourceType:id:hash8`. The audit warns
some records return null/empty FHIR ids (MedicationRequest/Condition/AllergyIntolerance),
so when the FHIR id is absent we fall back to a **deterministic synthetic id** — a hash
of the record's stable fields (type + date + display + patient). Uniqueness within a
request is guaranteed (collisions are disambiguated with `#n`), because the E6 verifier
resolves every claim against these ids — if two records shared an id, a citation would
be ambiguous.

The packet also carries `notices`: which tools FAILED (missing data), which returned
no records (e.g. allergies → "confirm with patient", never NKDA), and what was trimmed
for very large charts — so the verifier and templater surface gaps honestly, never
silently.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.tools.contracts import (
    AllergyRecord,
    ConditionRecord,
    EncounterRecord,
    LabObservation,
    MedicationRecord,
    PatientRecord,
    ToolResult,
    ToolStatus,
    _Record,
)

# record class → (resource_type, date_attr, display_attr) for the synthetic key.
_RECORD_META: dict[type, tuple[str, str | None, str | None]] = {
    PatientRecord: ("Patient", "birth_date", "name"),
    ConditionRecord: ("Condition", "onset", "display"),
    MedicationRecord: ("MedicationRequest", "authored_on", "name"),
    LabObservation: ("Observation", "effective", "display"),
    EncounterRecord: ("Encounter", "period_start", "type_display"),
    AllergyRecord: ("AllergyIntolerance", None, "substance"),
}


def _hash8(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def _content_hash(rec: _Record) -> str:
    return _hash8(json.dumps(rec.model_dump(), sort_keys=True, default=str))


def _synthetic_key(rec: _Record, patient_id: str) -> str:
    rt, date_attr, disp_attr = _RECORD_META[type(rec)]
    date = str(getattr(rec, date_attr, None) or "") if date_attr else ""
    disp = str(getattr(rec, disp_attr, None) or "") if disp_attr else ""
    return "|".join([rt, date, disp, patient_id])


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    resource_type: str
    source_resource_id: str  # the raw FHIR id ("" if it was null → synthetic)
    fields: dict[str, Any]


class Notice(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str  # "tool_failed" | "no_records" | "trimmed"
    tool: str
    detail: str


class EvidencePacket(BaseModel):
    model_config = ConfigDict(frozen=True)

    patient_id: str
    records: list[EvidenceRecord]
    notices: list[Notice]

    def by_id(self, evidence_id: str) -> EvidenceRecord | None:
        for r in self.records:
            if r.evidence_id == evidence_id:
                return r
        return None

    def by_type(self, resource_type: str) -> list[EvidenceRecord]:
        return [r for r in self.records if r.resource_type == resource_type]

    @property
    def failed_tools(self) -> list[str]:
        return [n.tool for n in self.notices if n.kind == "tool_failed"]


def _make_evidence_id(rec: _Record, patient_id: str, seen: set[str]) -> tuple[str, str]:
    """Return (evidence_id, source_resource_id). Uses the FHIR id when present, else a
    deterministic synthetic id; guarantees uniqueness within the request."""
    resource_type = _RECORD_META[type(rec)][0]
    content = _content_hash(rec)
    raw_id = (rec.resource_id or "").strip()
    if raw_id:
        base = f"{resource_type}:{raw_id}:{content}"
    else:
        base = f"{resource_type}:syn-{_hash8(_synthetic_key(rec, patient_id))}:{content}"
    evidence_id = base
    n = 1
    while evidence_id in seen:  # disambiguate duplicates → each claim stays resolvable
        evidence_id = f"{base}#{n}"
        n += 1
    seen.add(evidence_id)
    return evidence_id, raw_id


def build_evidence_packet(
    patient_id: str,
    fanout: dict[str, ToolResult],
    *,
    max_records_per_type: int | None = None,
) -> EvidencePacket:
    records: list[EvidenceRecord] = []
    notices: list[Notice] = []
    seen: set[str] = set()

    for tool, result in fanout.items():
        if result.status == ToolStatus.FAILED:
            notices.append(Notice(kind="tool_failed", tool=tool,
                                  detail=result.missing_reason or "unavailable"))
            continue
        if result.status == ToolStatus.NO_RECORDS:
            notices.append(Notice(kind="no_records", tool=tool,
                                  detail=f"{tool} returned no records"))
            continue

        kept = result.records
        if max_records_per_type is not None and len(kept) > max_records_per_type:
            dropped = len(kept) - max_records_per_type
            kept = kept[:max_records_per_type]
            notices.append(Notice(kind="trimmed", tool=tool,
                                  detail=f"showing {max_records_per_type} of {len(result.records)}; "
                                         f"{dropped} not shown (large chart)"))
        for rec in kept:
            evidence_id, raw_id = _make_evidence_id(rec, patient_id, seen)
            records.append(EvidenceRecord(
                evidence_id=evidence_id,
                resource_type=_RECORD_META[type(rec)][0],
                source_resource_id=raw_id,
                fields=rec.model_dump(),
            ))

    return EvidencePacket(patient_id=patient_id, records=records, notices=notices)


# resource_type → the tool whose notice section it belongs under (for trim notices).
_RESOURCE_TO_TOOL: dict[str, str] = {
    "Patient": "get_patient_summary",
    "Condition": "get_conditions",
    "MedicationRequest": "get_active_medications",
    "Observation": "get_recent_labs",
    "Encounter": "get_encounters",
    "AllergyIntolerance": "get_allergies",
}


def trim_packet(packet: EvidencePacket, max_records_per_type: int) -> EvidencePacket:
    """Return a smaller copy of `packet` keeping at most `max_records_per_type` records of each
    resource type, with a `trimmed` notice per type that was cut. This is the E4 trim policy
    applied post-hoc: the orchestrator uses it to recover from a 413 (prompt too large) by
    shrinking the evidence prefix and retrying — never a silent omission (the notice names the
    drop). Record order is preserved, so kept ids stay stable and resolvable."""
    kept: list[EvidenceRecord] = []
    counts: dict[str, int] = {}
    dropped: dict[str, int] = {}
    for r in packet.records:
        seen = counts.get(r.resource_type, 0)
        if seen < max_records_per_type:
            kept.append(r)
            counts[r.resource_type] = seen + 1
        else:
            dropped[r.resource_type] = dropped.get(r.resource_type, 0) + 1

    notices = list(packet.notices)
    for rt, n in dropped.items():
        total = counts.get(rt, 0) + n
        notices.append(Notice(
            kind="trimmed", tool=_RESOURCE_TO_TOOL.get(rt, rt),
            detail=f"showing {counts.get(rt, 0)} of {total} {rt}; {n} not shown (prompt-size trim)"))
    return EvidencePacket(patient_id=packet.patient_id, records=kept, notices=notices)
