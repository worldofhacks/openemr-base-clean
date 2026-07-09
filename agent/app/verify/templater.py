"""Deterministic templater — D13 fallback render (ARCHITECTURE.md §6, D13).

`render_packet_fallback` turns an EvidencePacket into a grounded, human-readable brief
WITHOUT any LLM: grouped by resource type in clinical order, values + dates, state-aware,
under an explicit "generated without LLM assistance" banner. It is what the physician
gets when the model hard-fails or the cost cap trips — never "LLM failed, no answer" (§6).

Because it renders only fields already in the packet, its output is grounded by
construction. A few §5 safety phrasings that ride on the packet's data/notices are honored
here too:
  - a missing dose → "confirm before dosing" (rule 6, F-D.2), never an invented dose;
  - an empty allergy result → "confirm with patient," never "NKDA" (F-D.5);
  - criticality is never surfaced as risk (F-D.4).

E6.1 extends this module with `render_from_verified_claims` (the normal-path re-render
from verified typed claims) and E6.2 with the full §5 rule set. The D12 deceased hard-stop
is a pre-flight refusal upstream of any render, so it is not re-checked here.
"""

from __future__ import annotations

from typing import Any

from app.evidence.packet import EvidencePacket

FALLBACK_BANNER = (
    "⚠️ Generated WITHOUT LLM assistance (automated fallback). "
    "Records below are present in the chart; clinical synthesis was not performed."
)

# resource_type → section heading, in clinical reading order.
_SECTION_ORDER: list[tuple[str, str]] = [
    ("Patient", "Patient"),
    ("Condition", "Problems"),
    ("MedicationRequest", "Medications"),
    ("Observation", "Labs / Observations"),
    ("Encounter", "Encounters"),
    ("AllergyIntolerance", "Allergies"),
]

# tool name → the section label its notice belongs under.
_TOOL_LABEL: dict[str, str] = {
    "get_patient_summary": "Patient",
    "get_conditions": "Problems",
    "get_active_medications": "Medications",
    "get_recent_labs": "Labs / Observations",
    "get_encounters": "Encounters",
    "get_allergies": "Allergies",
}


def _s(fields: dict[str, Any], key: str) -> str:
    v = fields.get(key)
    return "" if v is None else str(v)


def _render_record(resource_type: str, f: dict[str, Any]) -> str:
    if resource_type == "Patient":
        bits = [b for b in (_s(f, "name") or "(name unavailable)",
                            _s(f, "gender"), _s(f, "birth_date")) if b]
        return "; ".join(bits)

    if resource_type == "Condition":
        head = _s(f, "display") or _s(f, "code") or "(unspecified condition)"
        status = _s(f, "clinical_status")
        date = _s(f, "onset") or _s(f, "recorded_date")
        parts = [head]
        if status:
            parts.append(f"[{status}]")
        if date:
            parts.append(f"(onset {date})")
        return " ".join(parts)

    if resource_type == "MedicationRequest":
        head = _s(f, "name") or _s(f, "rxnorm") or "(unspecified medication)"
        dose = _s(f, "dose_text") or "dose not specified — confirm before dosing"  # rule 6 / F-D.2
        date = _s(f, "authored_on")
        line = f"{head} — {dose}"
        if date:
            line += f" (authored {date})"
        return line

    if resource_type == "Observation":
        head = _s(f, "display") or _s(f, "loinc") or "(unspecified observation)"
        value = _s(f, "value") or _s(f, "value_string")
        unit = _s(f, "unit")
        date = _s(f, "effective")
        val = (f"{value} {unit}".strip()) if value else "no value recorded"
        line = f"{head}: {val}"
        if date:
            line += f" ({date})"
        return line

    if resource_type == "Encounter":
        head = _s(f, "type_display") or _s(f, "class_") or "encounter"
        start, end = _s(f, "period_start"), _s(f, "period_end")
        reason = _s(f, "reason")
        span = start + (f"–{end}" if end else "")
        line = head + (f" ({span})" if span else "")
        if reason:
            line += f" — {reason}"
        return line

    if resource_type == "AllergyIntolerance":
        # F-D.4: criticality is null dataset-wide and never trusted as risk — do NOT render it.
        head = _s(f, "substance") or "(unspecified substance)"
        reaction = _s(f, "reaction")
        return head + (f" — reaction: {reaction}" if reaction else "")

    return _s(f, "display") or _s(f, "name") or "(record)"


def _render_notice(kind: str, tool: str, detail: str) -> str | None:
    label = _TOOL_LABEL.get(tool, tool)
    if kind == "tool_failed":
        return f"⚠ {label}: data unavailable — {detail} (partial answer, not a silent omission)"
    if kind == "no_records":
        if tool == "get_allergies":
            # F-D.5: an empty allergy result is NOT NKDA.
            return "⚠ Allergies: no allergy records returned — confirm with patient (not evidence of no allergies)."
        return f"{label}: no records returned."
    if kind == "trimmed":
        return f"{label}: {detail}"
    return None


def render_packet_fallback(packet: EvidencePacket) -> str:
    lines: list[str] = [FALLBACK_BANNER, ""]

    notices_by_label: dict[str, list[str]] = {}
    for n in packet.notices:
        rendered = _render_notice(n.kind, n.tool, n.detail)
        if rendered:
            notices_by_label.setdefault(_TOOL_LABEL.get(n.tool, n.tool), []).append(rendered)

    for resource_type, heading in _SECTION_ORDER:
        records = packet.by_type(resource_type)
        notices = notices_by_label.pop(heading, [])
        if not records and not notices:
            continue
        lines.append(f"## {heading}")
        for r in records:
            lines.append(f"- {_render_record(resource_type, r.fields)}  [{r.evidence_id}]")
        lines.extend(notices)
        lines.append("")

    # Any notices whose tool didn't map to a known section (defensive).
    for leftover in notices_by_label.values():
        lines.extend(leftover)

    return "\n".join(lines).rstrip() + "\n"
