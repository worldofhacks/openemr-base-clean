"""No-allergy fixture → drives the F-D.5 phrasing rule: an empty allergy result renders
"no allergy records returned; confirm with patient", NEVER "NKDA"/"no known allergies".

The canonical demo patients all have allergy records, so absence — the actual hazard — is
untestable against live data. The fixture returns a packet whose allergy tool came back
NO_RECORDS (queried successfully, none found).
"""

from __future__ import annotations

from app.evidence.packet import EvidencePacket, build_evidence_packet
from app.tools.contracts import ToolResult, ToolStatus


def no_allergy_packet(patient_id: str = "pat-no-allergy") -> EvidencePacket:
    return build_evidence_packet(patient_id, {"get_allergies": ToolResult(
        tool="get_allergies", status=ToolStatus.NO_RECORDS, records=[])})
