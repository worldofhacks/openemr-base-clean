"""E6.1/E6.2 — the §5 verifier: field-level match + the audit's concrete rules (D7).

The load-bearing trust layer. Given a typed `Claim` and the `EvidencePacket` it cites, the
verifier decides `pass | flagged | blocked | refused(kind)` by matching the claim's fields
against the CITED record — **rejecting on CONTRADICTION, never on absence** (10 mg vs 5 mg →
reject; both silent → pass). It runs the D12 deceased pre-flight hard-stop and the six
concrete audit rules (F-D.1/F-D.4/F-D.5/F-D.6/F-D.2) plus the treatment-verb blocklist.

Every check is DETERMINISTIC and packet-driven — no LLM anywhere on this path. The result
carries the resolved provenance (`matched_evidence_ids`) and the VERIFIED fields the
templater re-renders from, so display text is built from evidence, never echoed from prose.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.evidence.packet import EvidencePacket, EvidenceRecord
from app.verify.claims import (
    AllergyClaim,
    Claim,
    ConditionClaim,
    ImmunizationClaim,
    LabValueClaim,
    MedicationClaim,
    RefusalKind,
    TextClaim,
    Verdict,
)
from app.verify.rules import (
    contains_forbidden_phrase,
    contains_treatment_verb,
)

# clinical statuses that mean "not currently present" — an inactive/resolved match (rule 4/F-D.6).
_INACTIVE_STATUSES: frozenset[str] = frozenset({"inactive", "resolved"})

# claim type → the EvidencePacket resource_type its citations must resolve to.
_CLAIM_RESOURCE_TYPE: dict[type[Claim], str] = {
    MedicationClaim: "MedicationRequest",
    LabValueClaim: "Observation",
    ConditionClaim: "Condition",
    AllergyClaim: "AllergyIntolerance",
    ImmunizationClaim: "Immunization",
}


class VerificationResult(BaseModel):
    """The verifier's decision for one claim — verdict + provenance + verified fields.

    `verified` holds ONLY fields proven against the cited record; the templater re-renders
    display text from these (a BLOCKED/REFUSED result carries none, so a contradicted value
    can never reach the render). `rendered_text` is a defensive verified snippet the verdict
    tests read; the normal serving render is `templater.render_from_verified`.
    """

    model_config = ConfigDict(frozen=True)

    verdict: Verdict
    reason: str | None = None
    matched_evidence_ids: list[str] = Field(default_factory=list)
    refusal_kind: RefusalKind | None = None
    verified: dict[str, Any] = Field(default_factory=dict)
    rendered_text: str | None = None


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


class Verifier:
    """Deterministic §5 verifier — field-level match against the cited record (D7)."""

    def preflight(self, packet: EvidencePacket) -> RefusalKind | None:
        """D12 deceased hard-stop: refuse before any summarization iff any Patient record
        has `deceased_datetime` set OR `deceased_boolean is True` (F-S.7)."""
        for record in packet.by_type("Patient"):
            if record.fields.get("deceased_datetime") is not None:
                return RefusalKind.DECEASED
            if record.fields.get("deceased_boolean") is True:
                return RefusalKind.DECEASED
        return None

    def verify(self, claim: Claim, packet: EvidencePacket) -> VerificationResult:
        # A TextClaim is free prose — it never resolves a field, so it is screened, not matched.
        if isinstance(claim, TextClaim):
            return self._verify_text(claim)

        matched = self._resolve(claim, packet)

        # Every claim must cite (D7). No resolvable citation → never PASS.
        if not matched:
            return VerificationResult(
                verdict=Verdict.BLOCKED,
                reason="claim cites no resolvable evidence in the packet",
                matched_evidence_ids=[],
            )

        record = matched[0]
        matched_ids = [r.evidence_id for r in matched]

        if isinstance(claim, MedicationClaim):
            return self._verify_medication(claim, record, matched_ids)
        if isinstance(claim, LabValueClaim):
            return self._verify_lab(claim, record, matched_ids)
        if isinstance(claim, ConditionClaim):
            return self._verify_condition(claim, record, matched_ids)
        if isinstance(claim, AllergyClaim):
            return self._verify_allergy(claim, record, matched_ids)
        if isinstance(claim, ImmunizationClaim):
            return self._verify_immunization(claim, matched_ids)

        return VerificationResult(
            verdict=Verdict.BLOCKED,
            reason="unsupported claim type",
            matched_evidence_ids=matched_ids,
        )

    # --- citation resolution -------------------------------------------------

    def _resolve(self, claim: Claim, packet: EvidencePacket) -> list[EvidenceRecord]:
        expected_type = _CLAIM_RESOURCE_TYPE.get(type(claim))
        resolved: list[EvidenceRecord] = []
        for eid in claim.evidence_ids:
            record = packet.by_id(eid)
            if record is None:
                continue
            if expected_type is not None and record.resource_type != expected_type:
                continue
            resolved.append(record)
        return resolved

    # --- per-claim field-level match ----------------------------------------

    def _verify_medication(
        self, claim: MedicationClaim, record: EvidenceRecord, matched_ids: list[str]
    ) -> VerificationResult:
        record_name = _norm(record.fields.get("name"))
        if record_name and _norm(claim.name) != record_name:
            return VerificationResult(
                verdict=Verdict.BLOCKED,
                reason=(
                    f"medication name contradicts cited record: "
                    f"claim '{claim.name}' vs record '{record.fields.get('name')}'"
                ),
                matched_evidence_ids=matched_ids,
            )

        record_dose = record.fields.get("dose_text")
        # Reject on contradiction, NOT absence: a mismatch only counts when BOTH are present.
        if claim.dose is not None and record_dose is not None and _norm(claim.dose) != _norm(record_dose):
            return VerificationResult(
                verdict=Verdict.BLOCKED,
                reason=(
                    f"dose contradicts cited record: claim '{claim.dose}' vs record '{record_dose}'"
                ),
                matched_evidence_ids=matched_ids,
            )

        # PASS. Verified fields come from the RECORD (never the model's number) — dose is only
        # carried if the record actually has one, so no dose is invented on absence.
        verified: dict[str, Any] = {"name": record.fields.get("name") or claim.name}
        if record_dose is not None:
            verified["dose"] = record_dose
        return VerificationResult(
            verdict=Verdict.PASS,
            matched_evidence_ids=matched_ids,
            verified=verified,
        )

    def _verify_lab(
        self, claim: LabValueClaim, record: EvidenceRecord, matched_ids: list[str]
    ) -> VerificationResult:
        record_display = _norm(record.fields.get("display"))
        if record_display and _norm(claim.display) != record_display:
            return VerificationResult(
                verdict=Verdict.BLOCKED,
                reason=(
                    f"lab display contradicts cited record: "
                    f"claim '{claim.display}' vs record '{record.fields.get('display')}'"
                ),
                matched_evidence_ids=matched_ids,
            )

        record_value = record.fields.get("value")
        record_value_str = record.fields.get("value_string")
        record_value_display = (
            record_value_str
            if record_value_str is not None
            else (_fmt_number(record_value) if record_value is not None else None)
        )
        if (
            claim.value is not None
            and record_value_display is not None
            and _norm(claim.value) != _norm(record_value_display)
        ):
            return VerificationResult(
                verdict=Verdict.BLOCKED,
                reason=(
                    f"lab value contradicts cited record: "
                    f"claim '{claim.value}' vs record '{record_value_display}'"
                ),
                matched_evidence_ids=matched_ids,
            )

        record_unit = record.fields.get("unit")
        verified: dict[str, Any] = {"display": record.fields.get("display") or claim.display}
        if record_value_display is not None:
            verified["value"] = record_value_display
        if record_unit is not None:
            verified["unit"] = record_unit
        return VerificationResult(
            verdict=Verdict.PASS,
            matched_evidence_ids=matched_ids,
            verified=verified,
        )

    def _verify_condition(
        self, claim: ConditionClaim, record: EvidenceRecord, matched_ids: list[str]
    ) -> VerificationResult:
        record_display = _norm(record.fields.get("display"))
        if record_display and _norm(claim.display) != record_display:
            return VerificationResult(
                verdict=Verdict.BLOCKED,
                reason=(
                    f"condition contradicts cited record: "
                    f"claim '{claim.display}' vs record '{record.fields.get('display')}'"
                ),
                matched_evidence_ids=matched_ids,
            )

        # F-D.6: "no history of X" (present=False) is FALSE when a matching record exists —
        # including an inactive/resolved one (the packet consumes all conditions).
        if claim.present is False:
            return VerificationResult(
                verdict=Verdict.BLOCKED,
                reason=(
                    f"'no history of {claim.display}' contradicted: a matching "
                    f"{record.fields.get('clinical_status') or 'recorded'} condition exists"
                ),
                matched_evidence_ids=matched_ids,
            )

        return VerificationResult(
            verdict=Verdict.PASS,
            matched_evidence_ids=matched_ids,
            verified={
                "display": record.fields.get("display") or claim.display,
                "clinical_status": record.fields.get("clinical_status"),
            },
        )

    def _verify_allergy(
        self, claim: AllergyClaim, record: EvidenceRecord, matched_ids: list[str]
    ) -> VerificationResult:
        # F-D.4: any non-None risk is a criticality-derived claim — criticality is never a risk claim.
        if claim.risk is not None:
            return VerificationResult(
                verdict=Verdict.BLOCKED,
                reason="allergy risk/criticality is never a trusted claim (criticality is null dataset-wide)",
                matched_evidence_ids=matched_ids,
            )

        record_substance = _norm(record.fields.get("substance"))
        if record_substance and _norm(claim.substance) != record_substance:
            return VerificationResult(
                verdict=Verdict.BLOCKED,
                reason=(
                    f"allergy substance contradicts cited record: "
                    f"claim '{claim.substance}' vs record '{record.fields.get('substance')}'"
                ),
                matched_evidence_ids=matched_ids,
            )

        # F-D.4: criticality is NEVER surfaced — only the substance is a verified field.
        return VerificationResult(
            verdict=Verdict.PASS,
            matched_evidence_ids=matched_ids,
            verified={"substance": record.fields.get("substance") or claim.substance},
        )

    def _verify_immunization(
        self, claim: ImmunizationClaim, matched_ids: list[str]
    ) -> VerificationResult:
        # F-D.1: FHIR status is never trusted — a "declined" assertion is the inversion trap.
        if claim.declined:
            return VerificationResult(
                verdict=Verdict.BLOCKED,
                reason="immunization 'declined' status is never trusted (F-D.1 inversion trap)",
                matched_evidence_ids=matched_ids,
            )
        return VerificationResult(
            verdict=Verdict.PASS,
            matched_evidence_ids=matched_ids,
            verified={"vaccine": claim.vaccine},
        )

    # --- free-prose screens --------------------------------------------------

    def _verify_text(self, claim: TextClaim) -> VerificationResult:
        # Treatment-verb blocklist → REFUSED(TREATMENT_ADVICE). Checked first: an "advise to
        # act" claim is refused regardless of citation.
        if contains_treatment_verb(claim.text):
            return VerificationResult(
                verdict=Verdict.REFUSED,
                reason="text asserts a treatment action; the agent is read-only",
                refusal_kind=RefusalKind.TREATMENT_ADVICE,
            )

        # F-D.1: status-inversion trap phrasing ("declined"/"refused"/"patient objection")
        # is blocked and never carried into rendered output.
        if contains_forbidden_phrase(claim.text):
            return VerificationResult(
                verdict=Verdict.BLOCKED,
                reason="text carries FHIR status-inversion phrasing that is never trusted (F-D.1)",
            )

        # A descriptive TextClaim carries no verified field of its own (prose cannot phrase
        # past verification); it is not rejected, but nothing renders from it either.
        if not claim.evidence_ids:
            return VerificationResult(
                verdict=Verdict.BLOCKED,
                reason="text claim cites no evidence",
            )
        return VerificationResult(verdict=Verdict.FLAGGED, reason="free-prose claim not field-verifiable")


def _fmt_number(value: Any) -> str:
    """Render a numeric field without a trailing ``.0`` so '7.8'/'12' match cited text."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
