"""The six read tools + parallel fan-out (ARCHITECTURE.md §2, §3 UC1, D9, D10, F-P.2, §6/F3).

Each tool reads one FHIR resource for the bound patient and returns a tri-state
`ToolResult`: OK (records), NO_RECORDS (queried, none found), or FAILED (errored/timed
out, with `missing_reason` naming what's missing). `run_previsit_fanout` runs the six
independent reads concurrently (D10) with a per-call timeout and a total turn budget;
anything unfinished at the budget is FAILED — a partial answer, never a silent
omission. The labs tool passes an explicit `category=laboratory` (F-P.2).

Mappers translate FHIR JSON into the typed evidence-record shapes (contracts.py). They
KEEP the fields the §5 rules distrust (allergy criticality, encounter/med status) so
the verifier can apply its rules — mapping never drops or asserts them.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from app.tools.contracts import (
    AllergyRecord,
    ConditionRecord,
    EncounterRecord,
    LabObservation,
    MedicationRecord,
    PatientRecord,
    ToolResult,
    ToolStatus,
)


# --- small safe extractors -------------------------------------------------

def _text_or_display(cc: dict | None) -> str | None:
    if not cc:
        return None
    if cc.get("text"):
        return cc["text"]
    for c in cc.get("coding", []):
        if c.get("display"):
            return c["display"]
    return None


def _coding_code(cc: dict | None) -> str | None:
    if not cc:
        return None
    for c in cc.get("coding", []):
        if c.get("code"):
            return c["code"]
    return None


def _status_code(node: dict | None) -> str | None:
    return _coding_code(node) if isinstance(node, dict) else None


def _as_date(s: Any) -> str | None:
    return s[:10] if isinstance(s, str) and s else None


# --- mappers (FHIR resource → typed evidence record) -----------------------

def map_patient(res: dict) -> PatientRecord:
    name = None
    for n in res.get("name", []):
        given = " ".join(n.get("given", []))
        name = f"{given} {n.get('family', '')}".strip() or n.get("text")
        break
    return PatientRecord(
        resource_id=res.get("id") or "", name=name, birth_date=_as_date(res.get("birthDate")),
        gender=res.get("gender"),
        deceased_boolean=res.get("deceasedBoolean"),
        deceased_datetime=res.get("deceasedDateTime"),
    )


def map_condition(res: dict) -> ConditionRecord:
    return ConditionRecord(
        resource_id=res.get("id") or "", code=_coding_code(res.get("code")),
        display=_text_or_display(res.get("code")),
        clinical_status=_status_code(res.get("clinicalStatus")),  # rule 4: keep it
        onset=_as_date(res.get("onsetDateTime")), recorded_date=_as_date(res.get("recordedDate")),
    )


def map_medication(res: dict) -> MedicationRecord:
    dose = None
    di = res.get("dosageInstruction") or []
    # OpenEMR emits dosageInstruction as [[]] (inner LIST, not dict) when the dose is
    # the suppressed bare-numeric (F-D.2) — guard the type before .get().
    if di and isinstance(di[0], dict) and di[0].get("text"):
        dose = di[0]["text"]
    return MedicationRecord(
        resource_id=res.get("id") or "", name=_text_or_display(res.get("medicationCodeableConcept")),
        rxnorm=_coding_code(res.get("medicationCodeableConcept")),
        dose_text=dose,                              # rule 6: may be None
        status=res.get("status"), intent=res.get("intent"),
        authored_on=_as_date(res.get("authoredOn")),
    )


def map_observation(res: dict) -> LabObservation:
    vq = res.get("valueQuantity") or {}
    cat = None
    for c in res.get("category", []):
        cat = _coding_code(c) or cat
    return LabObservation(
        resource_id=res.get("id") or "", loinc=_coding_code(res.get("code")),
        display=_text_or_display(res.get("code")),
        value=vq.get("value"), value_string=res.get("valueString"), unit=vq.get("unit"),
        effective=_as_date(res.get("effectiveDateTime")),
        abnormal_flag=_coding_code((res.get("interpretation") or [{}])[0]) if res.get("interpretation") else None,
        category=cat,
    )


def map_encounter(res: dict) -> EncounterRecord:
    period = res.get("period") or {}
    types = res.get("type") or []
    return EncounterRecord(
        resource_id=res.get("id") or "", status=res.get("status"),           # rule 1: non-asserted
        **{"class": (res.get("class") or {}).get("code")},
        type_display=_text_or_display(types[0]) if types else None,
        period_start=_as_date(period.get("start")), period_end=_as_date(period.get("end")),
        reason=_text_or_display((res.get("reasonCode") or [{}])[0]) if res.get("reasonCode") else None,
    )


def map_allergy(res: dict) -> AllergyRecord:
    reaction = None
    rx = res.get("reaction") or []
    if rx and rx[0].get("manifestation"):
        reaction = _text_or_display(rx[0]["manifestation"][0])
    return AllergyRecord(
        resource_id=res.get("id") or "", substance=_text_or_display(res.get("code")),
        criticality=res.get("criticality"),                        # rule 2: untrusted
        clinical_status=_status_code(res.get("clinicalStatus")),
        verification_status=_status_code(res.get("verificationStatus")),
        reaction=reaction, category=(res.get("category") or [None])[0],
    )


# --- generic read + the six tools -----------------------------------------

async def _read(client, name: str, resource: str, params: dict,
                mapper: Callable[[dict], Any], label: str) -> ToolResult:
    try:
        bundle = await client.search(resource, params)
    except Exception as exc:  # FhirCallError (and any unexpected) → named partial failure
        reason = getattr(exc, "reason", type(exc).__name__)
        return ToolResult(tool=name, status=ToolStatus.FAILED, missing_reason=f"{label} unavailable: {reason}")
    records = [mapper(en["resource"]) for en in bundle.get("entry", []) if "resource" in en]
    return ToolResult(tool=name, status=ToolStatus.OK if records else ToolStatus.NO_RECORDS, records=records)


async def get_patient_summary(client, patient_id: str) -> ToolResult:
    return await _read(client, "get_patient_summary", "Patient",
                       {"_id": patient_id}, map_patient, "patient demographics")


async def get_conditions(client, patient_id: str) -> ToolResult:
    # NO clinical-status filter — consume ALL conditions incl. inactive (rule 4 / F-D.6).
    return await _read(client, "get_conditions", "Condition",
                       {"patient": patient_id, "_count": 200}, map_condition, "conditions")


async def get_active_medications(client, patient_id: str) -> ToolResult:
    return await _read(client, "get_active_medications", "MedicationRequest",
                       {"patient": patient_id, "_count": 200}, map_medication, "medications")


async def get_recent_labs(client, patient_id: str) -> ToolResult:
    # F-P.2: explicit category prunes FhirObservationService's 10-way sub-service fan-out.
    return await _read(client, "get_recent_labs", "Observation",
                       {"patient": patient_id, "category": "laboratory", "_count": 200},
                       map_observation, "recent labs")


async def get_encounters(client, patient_id: str) -> ToolResult:
    return await _read(client, "get_encounters", "Encounter",
                       {"patient": patient_id, "_count": 100}, map_encounter, "encounters")


async def get_allergies(client, patient_id: str) -> ToolResult:
    # Tri-state matters most here: NO_RECORDS ≠ NKDA (rule 3, F-D.5) — resolved downstream.
    return await _read(client, "get_allergies", "AllergyIntolerance",
                       {"patient": patient_id, "_count": 200}, map_allergy, "allergies")


_PREVISIT_TOOLS: dict[str, Callable[[Any, str], Awaitable[ToolResult]]] = {
    "get_patient_summary": get_patient_summary,
    "get_conditions": get_conditions,
    "get_active_medications": get_active_medications,
    "get_recent_labs": get_recent_labs,
    "get_encounters": get_encounters,
    "get_allergies": get_allergies,
}


async def run_previsit_fanout(
    client, patient_id: str, *, per_call_timeout: float = 8.0, turn_budget: float = 25.0,
    on_call: Callable[[str, float, ToolResult], None] | None = None,
) -> dict[str, ToolResult]:
    """Run the six independent pre-visit reads concurrently (D10). Per-call timeout
    bounds each; the total turn budget bounds the whole turn. Anything unfinished is a
    FAILED result naming what's missing — the brief is always a partial answer, never
    a silent omission (§6/F3).

    `on_call(name, latency_ms, result)` is invoked once per read (OK / NO_RECORDS / FAILED)
    so the caller can emit a per-FHIR-call accountability span (CXR-05/§7): every PHI read is
    traced with its latency and outcome, including timeouts and budget cancellations. Recording
    is best-effort observability — an exception in the callback never affects the returned
    results (soft dependency, §6)."""
    timings: dict[str, float] = {}

    async def bounded(name: str, fn: Callable[[Any, str], Awaitable[ToolResult]]) -> ToolResult:
        t0 = time.monotonic()
        try:
            return await asyncio.wait_for(fn(client, patient_id), per_call_timeout)
        except asyncio.TimeoutError:
            return ToolResult(tool=name, status=ToolStatus.FAILED,
                              missing_reason=f"{name} unavailable (per-call timeout {per_call_timeout}s)")
        finally:
            timings[name] = (time.monotonic() - t0) * 1000.0

    tasks = {name: asyncio.create_task(bounded(name, fn)) for name, fn in _PREVISIT_TOOLS.items()}
    done, _pending = await asyncio.wait(tasks.values(), timeout=turn_budget)
    results: dict[str, ToolResult] = {}
    for name, task in tasks.items():
        if task in done:
            results[name] = task.result()
        else:
            task.cancel()
            results[name] = ToolResult(tool=name, status=ToolStatus.FAILED,
                                       missing_reason=f"{name} unavailable (turn budget {turn_budget}s exceeded)")
            timings.setdefault(name, turn_budget * 1000.0)
    if on_call is not None:
        for name, res in results.items():
            try:
                on_call(name, timings.get(name, 0.0), res)
            except Exception:
                pass  # observability is a soft dependency — never let it perturb the fan-out
    return results
