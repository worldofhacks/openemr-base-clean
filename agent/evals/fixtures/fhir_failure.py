"""FHIR-failure fixture → drives the F3 partial-answer rule: a failed tool yields a partial
answer that NAMES what is missing, never a silent omission.

One tool (medications) comes back FAILED with a reason; the others succeed. The rendered brief
must surface the present data AND name the missing data ("medications unavailable"), so the
physician is never misled into thinking an empty section means "none."
"""

from __future__ import annotations

from app.evidence.packet import EvidencePacket, build_evidence_packet
from app.tools.contracts import ConditionRecord, ToolResult, ToolStatus


def partial_packet(patient_id: str = "pat-fhir-fail") -> EvidencePacket:
    return build_evidence_packet(patient_id, {
        "get_conditions": ToolResult(tool="get_conditions", status=ToolStatus.OK,
                                     records=[ConditionRecord(resource_id="c1", display="Asthma")]),
        "get_active_medications": ToolResult(tool="get_active_medications", status=ToolStatus.FAILED,
                                             missing_reason="medications unavailable: HTTP 503")})
