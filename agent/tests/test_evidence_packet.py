"""E4 — EvidencePacket builder (ARCHITECTURE.md §5, §6a, F-C.6).

The packet is the ONLY thing the LLM and the E6 verifier see; every claim resolves
against an evidence_id, so those ids must be STABLE and UNIQUE within a request.
The audit warns that some records (MedicationRequest/Condition/AllergyIntolerance)
can come back with null/empty FHIR ids — so the builder falls back to a deterministic
synthetic id (hash of type + date + display + patient), and disambiguates any
collision, or citations would break downstream. The packet also carries notices —
which tools failed (missing data) and what was trimmed — so the verifier can surface
them honestly.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.evidence.packet import EvidencePacket, build_evidence_packet
from app.tools.contracts import (
    AllergyRecord,
    ConditionRecord,
    MedicationRecord,
    ToolResult,
    ToolStatus,
)

PID = "pat-1"


def _ok(tool, records):
    return ToolResult(tool=tool, status=ToolStatus.OK, records=records)


def _failed(tool, reason):
    return ToolResult(tool=tool, status=ToolStatus.FAILED, missing_reason=reason)


def _none(tool):
    return ToolResult(tool=tool, status=ToolStatus.NO_RECORDS, records=[])


# --- evidence id: real FHIR id -------------------------------------------

def test_evidence_id_uses_fhir_id_when_present():
    packet = build_evidence_packet(PID, {"get_conditions": _ok(
        "get_conditions", [ConditionRecord(resource_id="cond-1", display="Diabetes", clinical_status="active")])})
    rec = packet.records[0]
    assert rec.evidence_id.startswith("Condition:cond-1:")
    assert rec.resource_type == "Condition"
    # §5a shape: ResourceType:id:hash8
    assert len(rec.evidence_id.split(":")[-1]) == 8


# --- evidence id: null FHIR id → synthetic, deterministic ----------------

def test_synthetic_id_when_fhir_id_null_and_deterministic_across_builds():
    r = ConditionRecord(resource_id="", display="Hypertension", onset=date(2020, 1, 1))
    p1 = build_evidence_packet(PID, {"get_conditions": _ok("get_conditions", [r])})
    p2 = build_evidence_packet(PID, {"get_conditions": _ok("get_conditions", [r])})
    id1 = p1.records[0].evidence_id
    assert "syn-" in id1  # synthetic marker
    assert id1 == p2.records[0].evidence_id  # stable across requests for the same record


def test_synthetic_id_changes_with_patient():
    r = MedicationRecord(resource_id="", name="metformin", authored_on=date(2021, 1, 1))
    a = build_evidence_packet("pat-A", {"get_active_medications": _ok("get_active_medications", [r])})
    b = build_evidence_packet("pat-B", {"get_active_medications": _ok("get_active_medications", [r])})
    assert a.records[0].evidence_id != b.records[0].evidence_id  # patient is part of the key


# --- duplicate / collision → unique + resolvable -------------------------

def test_duplicate_null_id_records_get_unique_resolvable_ids():
    # Two records with the same empty id AND identical stable fields would collide.
    a = AllergyRecord(resource_id="", substance="penicillin")
    b = AllergyRecord(resource_id="", substance="penicillin")
    packet = build_evidence_packet(PID, {"get_allergies": _ok("get_allergies", [a, b])})
    ids = [r.evidence_id for r in packet.records]
    assert len(ids) == 2 and len(set(ids)) == 2  # disambiguated, unique within the request
    for eid in ids:
        assert packet.by_id(eid) is not None  # E6 verifier can resolve each


def test_all_evidence_ids_unique_within_packet():
    recs = [ConditionRecord(resource_id="", display="X") for _ in range(5)]
    packet = build_evidence_packet(PID, {"get_conditions": _ok("get_conditions", recs)})
    ids = [r.evidence_id for r in packet.records]
    assert len(set(ids)) == len(ids) == 5


def test_duplicate_real_fhir_id_also_disambiguated():
    a = ConditionRecord(resource_id="dup", display="A")
    b = ConditionRecord(resource_id="dup", display="B")
    packet = build_evidence_packet(PID, {"get_conditions": _ok("get_conditions", [a, b])})
    ids = [r.evidence_id for r in packet.records]
    assert len(set(ids)) == 2


# --- notices: missing data + trim ----------------------------------------

def test_packet_carries_tool_failed_notice_naming_missing():
    packet = build_evidence_packet(PID, {
        "get_active_medications": _failed("get_active_medications", "medications unavailable: HTTP 503")})
    failed = [n for n in packet.notices if n.kind == "tool_failed"]
    assert failed and failed[0].tool == "get_active_medications"
    assert "medications" in failed[0].detail


def test_packet_carries_no_records_notice_for_allergy():
    packet = build_evidence_packet(PID, {"get_allergies": _none("get_allergies")})
    n = [x for x in packet.notices if x.kind == "no_records" and x.tool == "get_allergies"]
    assert n  # E6 renders "no allergy records returned; confirm with patient" from this (F-D.5)


def test_packet_trims_large_record_set_and_notes_what_was_dropped():
    recs = [ConditionRecord(resource_id=f"c{i}", display=f"cond{i}") for i in range(10)]
    packet = build_evidence_packet(PID, {"get_conditions": _ok("get_conditions", recs)},
                                   max_records_per_type=4)
    assert len(packet.by_type("Condition")) == 4
    trim = [n for n in packet.notices if n.kind == "trimmed" and n.tool == "get_conditions"]
    assert trim and "6" in trim[0].detail  # 10 - 4 = 6 dropped


# --- lookups the verifier uses -------------------------------------------

@pytest.mark.asyncio
async def test_end_to_end_null_fhir_id_from_tool_to_synthetic_packet_id():
    # A FHIR resource with a MISSING id must not crash the mapper (→ resource_id="")
    # and must get a synthetic evidence id in the packet — the audit's null-id case.
    import app.tools.fhir_tools as ftools

    class _FakeClient:
        async def search(self, resource_type, params):
            return {"resourceType": "Bundle", "entry": [
                {"resource": {"resourceType": "Condition", "code": {"text": "Asthma"}}}]}  # no "id"

    result = await ftools.get_conditions(_FakeClient(), PID)
    assert result.records[0].resource_id == ""  # tolerated, not crashed
    packet = build_evidence_packet(PID, {"get_conditions": result})
    assert "syn-" in packet.records[0].evidence_id  # synthetic fallback engaged


def test_by_type_and_by_id_resolution():
    packet = build_evidence_packet(PID, {
        "get_conditions": _ok("get_conditions", [ConditionRecord(resource_id="c1", display="D")]),
        "get_allergies": _ok("get_allergies", [AllergyRecord(resource_id="a1", substance="peanut")]),
    })
    assert isinstance(packet, EvidencePacket)
    assert len(packet.by_type("Condition")) == 1
    assert len(packet.by_type("AllergyIntolerance")) == 1
    eid = packet.by_type("Condition")[0].evidence_id
    assert packet.by_id(eid).fields["display"] == "D"
    assert packet.by_id("nonexistent") is None
