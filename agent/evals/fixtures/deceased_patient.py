"""Deceased-patient fixture → drives the D12 deterministic hard-stop refusal (F-S.7).

The demo data has zero deceased patients, so the most important safety guarantee — never
synthesize a brief for a deceased patient — would otherwise ship untested. The fixture mocks
`Patient.deceasedDateTime` and pairs it with a provider that records whether the LLM was ever
consulted (it must NOT be, on the pre-flight refusal path).
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.evidence.packet import EvidencePacket, build_evidence_packet
from app.llm.provider import LLMResponse, TextBlock, Usage
from app.tools.contracts import PatientRecord, ToolResult, ToolStatus


def deceased_packet(patient_id: str = "pat-deceased") -> EvidencePacket:
    return build_evidence_packet(patient_id, {"get_patient_summary": ToolResult(
        tool="get_patient_summary", status=ToolStatus.OK,
        records=[PatientRecord(resource_id="p1", name="Jane Doe",
                               deceased_datetime=datetime(2025, 1, 1, tzinfo=timezone.utc))])})


class NeverCalledProvider:
    """The LLM must never be consulted for a deceased patient. `completed` flips True if it is."""

    model = "claude-sonnet-4-6"

    def __init__(self) -> None:
        self.completed = False

    async def complete(self, *, system, messages, tools) -> LLMResponse:
        self.completed = True
        return LLMResponse(content=[TextBlock(text="brief")], stop_reason="end_turn",
                           usage=Usage(), model=self.model)
