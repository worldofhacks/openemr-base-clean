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

from typing import TYPE_CHECKING, Any

from app.evidence.packet import EvidencePacket
from app.verify.claims import Verdict
from app.verify.rules import (
    contains_criticality_risk_phrase,
    contains_forbidden_phrase,
)

if TYPE_CHECKING:
    from app.verify.verifier import VerificationResult

FALLBACK_BANNER = (
    "⚠️ Generated WITHOUT LLM assistance (automated fallback). "
    "Records below are present in the chart; clinical synthesis was not performed."
)

# D13 output is a safety floor, not a second chart viewer. These are hard, user-visible bounds:
# even an unexpectedly large packet or long free-text field cannot turn fallback into a chart dump.
FALLBACK_MAX_RECORDS = 8
FALLBACK_MAX_CHARS = 2_500
_FALLBACK_MAX_FIELD_CHARS = 160

_INACTIVE_CONDITION_STATUSES: frozenset[str] = frozenset({"inactive", "resolved"})
_RESOLUTION_PHRASES: tuple[str, ...] = (
    "cured",
    "resolved",
    "inactive",
    "no longer active",
    "no longer has",
    "no longer have",
    "doesn't have anymore",
    "does not have anymore",
)
_GENERAL_BRIEF_PHRASES: tuple[str, ...] = ("pre-visit brief", "previsit brief", "pre visit brief")
_RESOLUTION_CAVEAT = (
    "I can show which problems are marked inactive or resolved in the chart, but I can't "
    "verify a clinical judgment of what's been cured — confirm with the chart."
)
_SCOPED_CAVEAT = (
    "I couldn't verify a synthesized answer. Only chart records relevant to this question "
    "are shown below; confirm with the chart."
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

_RESOURCE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Condition", ("condition", "problem", "diagnos", "disease")),
    ("MedicationRequest", ("medication", "medicine", "drug", "prescription")),
    ("Observation", ("lab", "observation", "test result", "a1c")),
    ("Encounter", ("encounter", "visit", "appointment")),
    ("AllergyIntolerance", ("allergy", "allergies", "allergic")),
    ("Patient", ("demographic", "birth", "gender", "age")),
)


def _clip_text(value: Any) -> str:
    text = " ".join(str(value).split())
    if len(text) <= _FALLBACK_MAX_FIELD_CHARS:
        return text
    return text[: _FALLBACK_MAX_FIELD_CHARS - 1].rstrip() + "…"


def _s(fields: dict[str, Any], key: str) -> str:
    v = fields.get(key)
    return "" if v is None else _clip_text(v)


def _is_resolution_question(question: str | None) -> bool:
    normalized = " ".join((question or "").lower().split())
    return any(phrase in normalized for phrase in _RESOLUTION_PHRASES)


def _question_resource_types(question: str | None) -> set[str] | None:
    """Return an explicit targeted scope, or ``None`` for a general/unknown request.

    This is intentionally a small deterministic vocabulary, not clinical NLP. A resolution
    question is always Condition-only. Other clear resource words may select one or several
    sections; an unrecognized general brief keeps the normal clinical section order.
    """
    normalized = " ".join((question or "").lower().split())
    if not normalized:
        return None
    if _is_resolution_question(normalized):
        return {"Condition"}
    if any(phrase in normalized for phrase in _GENERAL_BRIEF_PHRASES):
        return None
    selected = {
        resource_type
        for resource_type, keywords in _RESOURCE_KEYWORDS
        if any(keyword in normalized for keyword in keywords)
    }
    return selected or None


def _bounded_output(lines: list[str], max_chars: int) -> str:
    """Join whole lines under a hard character ceiling; never slice a record mid-line."""
    if max_chars <= 0:
        return ""

    kept: list[str] = []
    for line in lines:
        candidate = "\n".join([*kept, line]).rstrip() + "\n"
        if len(candidate) <= max_chars:
            kept.append(line)
            continue

        marker = "⚠ Output limit reached; additional relevant records omitted."
        while kept:
            candidate = "\n".join([*kept, marker]).rstrip() + "\n"
            if len(candidate) <= max_chars:
                kept.append(marker)
                break
            kept.pop()
        break

    return "\n".join(kept).rstrip() + "\n" if kept else ""


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
        return f"⚠ {label}: data unavailable — {_clip_text(detail)} (partial answer, not a silent omission)"
    if kind == "no_records":
        if tool == "get_allergies":
            # F-D.5: an empty allergy result is NOT NKDA.
            return "⚠ Allergies: no allergy records returned — confirm with patient (not evidence of no allergies)."
        return f"{label}: no records returned."
    if kind == "trimmed":
        return f"{label}: {_clip_text(detail)}"
    return None


def render_packet_fallback(
    packet: EvidencePacket,
    *,
    question: str | None = None,
    max_records: int = FALLBACK_MAX_RECORDS,
    max_chars: int = FALLBACK_MAX_CHARS,
) -> str:
    """Render a grounded D13 answer, scoped to a targeted question and always bounded.

    Resolution questions are a special safety boundary: ``inactive``/``resolved`` is a chart
    status that can be shown, while ``cured`` is a clinical inference the packet cannot prove.
    Those questions therefore render only inactive/resolved Conditions plus an explicit caveat.
    """
    max_records = max(0, max_records)
    resolution_question = _is_resolution_question(question)
    selected_types = _question_resource_types(question)
    lines: list[str] = [FALLBACK_BANNER, ""]
    if resolution_question:
        lines.extend([_RESOLUTION_CAVEAT, ""])
    elif selected_types is not None:
        lines.extend([_SCOPED_CAVEAT, ""])

    notices_by_label: dict[str, list[str]] = {}
    for n in packet.notices:
        rendered = _render_notice(n.kind, n.tool, n.detail)
        if rendered:
            notices_by_label.setdefault(_TOOL_LABEL.get(n.tool, n.tool), []).append(rendered)

    total_matching_records = 0
    rendered_records = 0
    rendered_any_section = False
    for resource_type, heading in _SECTION_ORDER:
        if selected_types is not None and resource_type not in selected_types:
            continue
        records = packet.by_type(resource_type)
        if resolution_question and resource_type == "Condition":
            records = [
                record for record in records
                if str(record.fields.get("clinical_status") or "").strip().lower()
                in _INACTIVE_CONDITION_STATUSES
            ]
        total_matching_records += len(records)
        notices = notices_by_label.pop(heading, [])
        remaining = max_records - rendered_records
        records_to_render = records[:remaining] if remaining > 0 else []
        if not records_to_render and not notices:
            continue
        rendered_any_section = True
        scoped_heading = "Problems marked inactive or resolved" if resolution_question else heading
        lines.append(f"## {scoped_heading}")
        for r in records_to_render:
            lines.append(f"- {_render_record(resource_type, r.fields)}  [{r.evidence_id}]")
            rendered_records += 1
        lines.extend(notices)
        lines.append("")

    if resolution_question and total_matching_records == 0:
        lines.append("No conditions are marked inactive or resolved in the available chart data.")
    elif selected_types is not None and not rendered_any_section:
        lines.append("No relevant records were available for this question.")

    omitted = max(0, total_matching_records - rendered_records)
    if omitted:
        lines.append(
            f"⚠ {omitted} additional relevant chart record(s) omitted by the fallback safety limit."
        )

    if selected_types is None:
        # Unknown/general request: keep honest gap notices, but never let a targeted scope leak
        # unrelated notices from the rest of the packet.
        for leftover in notices_by_label.values():
            lines.extend(leftover)

    return _bounded_output(lines, max_chars)


# ---------------------------------------------------------------------------
# E6.1 — verified-claims re-render (the normal path; ARCHITECTURE.md §5, D7)
# ---------------------------------------------------------------------------

_VERIFIED_HEADER = "Verified summary (each line re-rendered from cited evidence):"


def _render_verified_line(verified: dict[str, Any]) -> str | None:
    """Deterministically render ONE verified result's fields into a display line.

    Text is built ONLY from `verified` (fields the verifier proved against the cited
    record) — never from the model's prose — so a divergent number can never survive. Field
    presence, not claim type, selects the phrasing.
    """
    if "substance" in verified:
        # F-D.4: only the substance renders; criticality is never surfaced as risk.
        return f"Allergy: {verified['substance']}"

    if "vaccine" in verified:
        return f"Immunization: {verified['vaccine']}"

    if "display" in verified and ("value" in verified or "unit" in verified):
        # §5a lab: value + unit from the verified fields.
        value = verified.get("value")
        unit = verified.get("unit")
        val = f"{value} {unit}".strip() if value is not None else "no value recorded"
        return f"{verified['display']}: {val}"

    if "name" in verified:
        # Medication: dose only if the record actually carried one (rule 6/F-D.2 — never invent).
        dose = verified.get("dose")
        if dose:
            return f"{verified['name']} — {dose}"
        return f"{verified['name']} — dose not specified — confirm before dosing"

    if "display" in verified:
        # Condition (no lab value/unit): re-render the display, status-aware.
        status = verified.get("clinical_status")
        head = str(verified["display"])
        return f"{head} [{status}]" if status else head

    return None


def render_from_verified(
    results: list["VerificationResult"], *, packet: EvidencePacket | None = None
) -> str:
    """Re-render display text ONLY from the verified fields of PASS/FLAGGED results (§5 D7).

    The LLM's prose is discarded: every line is rebuilt from fields the verifier proved
    against the cited record, so a BLOCKED/REFUSED result's contradicted value never appears.
    Deterministic (same input → identical output). Forbidden phrasing (F-D.1 status-inversion,
    F-D.4 criticality-as-risk) is screened out as a final backstop.

    With no verified content and a `packet`, the no-records path is surfaced from the packet's
    notices — an empty allergy result renders "confirm with patient," never "NKDA" (F-D.5).
    """
    lines: list[str] = []
    for result in results:
        if result.verdict not in (Verdict.PASS, Verdict.FLAGGED):
            continue  # blocked/refused content is never rendered as verified
        line = _render_verified_line(result.verified)
        if line is None:
            continue
        if contains_forbidden_phrase(line) or contains_criticality_risk_phrase(line):
            continue  # final backstop — trap phrasing never survives the re-render
        lines.append(line)

    body: list[str] = []
    if lines:
        body.append(_VERIFIED_HEADER)
        body.extend(f"- {line}" for line in lines)

    # No verified content: surface the packet's honest gap notices (F-D.5 confirm-with-patient).
    if packet is not None:
        for notice in packet.notices:
            rendered = _render_notice(notice.kind, notice.tool, notice.detail)
            if rendered:
                body.append(rendered)

    if not body:
        return ""
    return "\n".join(body).rstrip() + "\n"
