"""LLM-failure fixture → drives the D13 deterministic degradation banner.

A provider that always fails (retries exhausted) forces the orchestrator down the D13 path:
render the EvidencePacket through the templater with the explicit "generated WITHOUT LLM
assistance" banner. The physician always gets something grounded — never "LLM failed, no
answer." Paired with a real (grounded) packet so the fallback has content to render.
"""

from __future__ import annotations

from app.evidence.packet import EvidencePacket, build_evidence_packet
from app.llm.provider import LLMResponse, LLMUnavailable
from app.tools.contracts import ConditionRecord, MedicationRecord, ToolResult, ToolStatus


def grounded_packet(patient_id: str = "pat-llm-fail") -> EvidencePacket:
    return build_evidence_packet(patient_id, {
        "get_conditions": ToolResult(tool="get_conditions", status=ToolStatus.OK,
                                     records=[ConditionRecord(resource_id="c1", display="Type 2 diabetes")]),
        "get_active_medications": ToolResult(tool="get_active_medications", status=ToolStatus.OK,
                                             records=[MedicationRecord(resource_id="m1", name="metformin",
                                                                       dose_text="500 mg")])})


class FailingProvider:
    """Always raises the transient-failure signal, as if the SDK's retries were exhausted."""

    model = "claude-sonnet-4-6"

    async def complete(self, *, system, messages, tools) -> LLMResponse:
        raise LLMUnavailable("retries exhausted (eval fixture)")
