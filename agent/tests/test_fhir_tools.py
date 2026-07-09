"""E3.2 — six read tools, parallel fan-out, partial-failure, allergy tri-state
(ARCHITECTURE.md §2, §3 UC1, D9, D10, F-P.2, §6/F3).

The six independent pre-visit reads fan out concurrently (D10: wall-clock ≈ slowest,
not sum) with a per-call timeout and a total turn budget. A failed/timed-out tool
yields a FAILED ToolResult that NAMES what's missing — a partial answer, never a
silent omission (§6/F3). The labs tool passes an explicit category (F-P.2). The
allergy tool is tri-state: records-found (OK) / no-records (NO_RECORDS — the
"confirm with patient" case, never NKDA) / tool-failed (FAILED).
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from app.tools.contracts import ToolStatus
from app.tools.fhir_client import FhirCallError, FhirClient
from app.tools import fhir_tools as T


# --- a fake client for concurrency / failure / budget tests ---------------

class FakeClient:
    def __init__(self, *, delays=None, errors=None, bundles=None):
        self.delays = delays or {}
        self.errors = errors or {}
        self.bundles = bundles or {}
        self.calls = []

    async def search(self, resource_type, params):
        self.calls.append((resource_type, params))
        await asyncio.sleep(self.delays.get(resource_type, 0))
        if resource_type in self.errors:
            raise FhirCallError(self.errors[resource_type])
        return self.bundles.get(resource_type, {"resourceType": "Bundle", "entry": []})


def _entry(res):
    return {"resource": res}


PID = "a234b786-539a-4f9a-96a0-432293226f02"


# --- parallel fan-out (D10) ------------------------------------------------

@pytest.mark.asyncio
async def test_fanout_runs_concurrently_wallclock_approx_slowest():
    # Each of the 6 reads sleeps 0.1s; concurrent ⇒ ~0.1s total, not ~0.6s.
    client = FakeClient(delays={r: 0.1 for r in
                                ["Patient", "Condition", "MedicationRequest",
                                 "Observation", "Encounter", "AllergyIntolerance"]})
    t0 = time.perf_counter()
    results = await T.run_previsit_fanout(client, PID, per_call_timeout=2.0, turn_budget=2.0)
    elapsed = time.perf_counter() - t0
    assert set(results) == {"get_patient_summary", "get_conditions", "get_active_medications",
                            "get_recent_labs", "get_encounters", "get_allergies"}
    assert elapsed < 0.4, f"fan-out was not concurrent: {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_partial_failure_yields_failed_naming_missing_never_silent():
    # One read fails; the brief still returns, and the failure is NAMED (not dropped).
    client = FakeClient(
        errors={"MedicationRequest": "upstream 503"},
        bundles={"Condition": {"entry": [_entry({"resourceType": "Condition", "id": "c1"})]}},
    )
    results = await T.run_previsit_fanout(client, PID, per_call_timeout=2.0, turn_budget=2.0)
    meds = results["get_active_medications"]
    assert meds.status is ToolStatus.FAILED
    assert meds.missing_reason and "medication" in meds.missing_reason.lower()
    # The failed tool is PRESENT in results (named), not silently omitted.
    assert "get_active_medications" in results
    assert results["get_conditions"].status is ToolStatus.OK


@pytest.mark.asyncio
async def test_turn_budget_exceeded_marks_pending_failed():
    client = FakeClient(delays={"Observation": 5.0})  # labs hang past the budget
    results = await T.run_previsit_fanout(client, PID, per_call_timeout=10.0, turn_budget=0.2)
    labs = results["get_recent_labs"]
    assert labs.status is ToolStatus.FAILED
    assert "budget" in (labs.missing_reason or "").lower()


@pytest.mark.asyncio
async def test_per_call_timeout_fails_that_tool_only():
    client = FakeClient(delays={"Encounter": 5.0})
    results = await T.run_previsit_fanout(client, PID, per_call_timeout=0.15, turn_budget=2.0)
    assert results["get_encounters"].status is ToolStatus.FAILED
    assert results["get_conditions"].status in (ToolStatus.OK, ToolStatus.NO_RECORDS)


# --- labs category (F-P.2) -------------------------------------------------

@pytest.mark.asyncio
async def test_recent_labs_passes_explicit_category_laboratory():
    client = FakeClient()
    await T.get_recent_labs(client, PID)
    obs_calls = [p for (r, p) in client.calls if r == "Observation"]
    assert obs_calls and obs_calls[0].get("category") == "laboratory"  # F-P.2 prune the fan-out


# --- allergy tri-state -----------------------------------------------------

@pytest.mark.asyncio
async def test_allergy_records_found_is_ok():
    client = FakeClient(bundles={"AllergyIntolerance": {"entry": [
        _entry({"resourceType": "AllergyIntolerance", "id": "a1",
                "code": {"text": "penicillin"}})]}})
    r = await T.get_allergies(client, PID)
    assert r.status is ToolStatus.OK and r.records[0].substance == "penicillin"


@pytest.mark.asyncio
async def test_allergy_empty_is_no_records_not_ok_not_nkda():
    client = FakeClient(bundles={"AllergyIntolerance": {"resourceType": "Bundle", "entry": []}})
    r = await T.get_allergies(client, PID)
    # The tri-state middle state — downstream renders "no allergy records returned;
    # confirm with patient", never NKDA. Here it must be NO_RECORDS, not OK-with-empty.
    assert r.status is ToolStatus.NO_RECORDS and r.records == []


@pytest.mark.asyncio
async def test_allergy_tool_failure_is_failed():
    client = FakeClient(errors={"AllergyIntolerance": "timeout"})
    r = await T.get_allergies(client, PID)
    assert r.status is ToolStatus.FAILED and r.missing_reason


# --- real-shape regression: MedicationRequest dosageInstruction = [[]] (F-D.2) ---

@pytest.mark.asyncio
async def test_medication_maps_empty_dosage_instruction_shape():
    # OpenEMR serializes dosageInstruction as [[]] (an inner LIST, not a dict) when the
    # dose is the suppressed bare-numeric (F-D.2). The mapper must not choke on it.
    client = FakeClient(bundles={"MedicationRequest": {"entry": [
        _entry({"resourceType": "MedicationRequest", "id": "rx1", "status": "active",
                "intent": "order", "dosageInstruction": [[]],
                "medicationCodeableConcept": {"coding": [
                    {"system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                     "code": "1014676", "display": "cetirizine 5 MG tablet"}]}})]}})
    r = await T.get_active_medications(client, PID)
    assert r.status is ToolStatus.OK
    med = r.records[0]
    assert med.name == "cetirizine 5 MG tablet" and med.rxnorm == "1014676"
    assert med.dose_text is None  # rule 6: no usable dose → templater says "confirm before dosing"


# --- conditions: consume ALL incl. inactive (rule 4) -----------------------

@pytest.mark.asyncio
async def test_conditions_maps_all_including_inactive():
    client = FakeClient(bundles={"Condition": {"entry": [
        _entry({"resourceType": "Condition", "id": "c1", "code": {"text": "Diabetes"},
                "clinicalStatus": {"coding": [{"code": "active"}]}}),
        _entry({"resourceType": "Condition", "id": "c2", "code": {"text": "Old fracture"},
                "clinicalStatus": {"coding": [{"code": "inactive"}]}}),
    ]}})
    r = await T.get_conditions(client, PID)
    assert r.status is ToolStatus.OK and len(r.records) == 2
    assert {c.clinical_status for c in r.records} == {"active", "inactive"}  # nothing filtered out


# --- FhirClient https pin (F-S.9) ------------------------------------------

def test_fhir_client_rejects_non_https_base():
    with pytest.raises(ValueError):
        FhirClient(base_url="http://openemr.internal/apis/default/fhir", access_token="AT")


@pytest.mark.asyncio
async def test_fhir_client_search_sends_bearer_and_correlation_header():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = {k.lower(): v for k, v in request.headers.items()}
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": []})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FhirClient(base_url="https://openemr.test/apis/default/fhir",
                        access_token="AT-secret", http_client=http)
    await client.search("Condition", {"patient": PID})
    assert captured["headers"]["authorization"] == "Bearer AT-secret"
    assert "x-copilot-request-id" in captured["headers"]  # correlation propagated (D10-rev)
