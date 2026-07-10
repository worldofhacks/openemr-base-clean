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


# Minimum length for a token/label to be treated as a significant identity signal. Guards the
# short-substring false positive ("in", 2 chars, must never match "insulin") while still
# admitting genuine short clinical labels like "a1c" (3 chars) — the boundary sits at 3 (T-E6b (1)).
_MIN_SIGNIFICANT_LEN = 3

# Tokens too generic to distinguish one entity from another — never a shared-token match on
# these alone (e.g. two records both "… tablet" are not thereby the same drug).
_STOPWORD_TOKENS: frozenset[str] = frozenset({
    "mg", "ml", "oral", "tablet", "capsule", "solution", "injection", "unit", "units",
    "finding", "disorder", "the", "and", "for", "with",
})


def _significant_tokens(label: str) -> set[str]:
    """Word tokens of a normalized label worth treating as identity signals — long enough and
    not a generic stopword."""
    tokens = set()
    for tok in label.replace("-", " ").replace("/", " ").split():
        cleaned = "".join(ch for ch in tok if ch.isalnum())
        if len(cleaned) >= _MIN_SIGNIFICANT_LEN and cleaned not in _STOPWORD_TOKENS:
            tokens.add(cleaned)
    return tokens


def _labels_match(claim_label: str, record_label: str) -> bool:
    """LENIENT entity-identity check (T-E6b (1)): the claim and record name the SAME clinical
    entity when they are exact-equal, when one normalized label CONTAINS the other, or when
    they share at least one significant token.

    Guards short-substring false positives ("in" must NOT match "insulin"): a containment
    match requires the shorter label be long enough to be significant, and a token match only
    counts long, non-generic tokens (see `_significant_tokens`). A genuinely different entity
    (warfarin vs metformin) shares no significant token and neither contains the other → no
    match → the caller still BLOCKS.
    """
    a, b = _norm(claim_label), _norm(record_label)
    if not a or not b:
        # An absent label is not a contradiction (reject on contradiction, not absence).
        return True
    if a == b:
        return True

    # Containment: one label is a token-boundary substring of the other, and the shorter side
    # is long enough to be a significant signal (rejects "in" ⊂ "insulin").
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= _MIN_SIGNIFICANT_LEN and _contains_at_boundary(longer, shorter):
        return True

    # Token-subset check: the SHORTER label's significant tokens must be a full SUBSET of
    # the LONGER label's significant tokens. A mere intersection (one shared token) is not
    # enough — that would collapse genuinely distinct entities that share a class word
    # ("insulin glargine" vs "insulin lispro", "total cholesterol" vs "HDL cholesterol").
    # Reordered paraphrases still PASS because ALL shorter-side tokens appear in the longer
    # label ("Type 2 diabetes" → {"type","diabetes"} ⊆ {"diabetes","mellitus","type"}).
    st_a, st_b = _significant_tokens(a), _significant_tokens(b)
    if len(st_a) <= len(st_b):
        st_short, st_long = st_a, st_b
    else:
        st_short, st_long = st_b, st_a
    return bool(st_short) and st_short <= st_long


def _contains_at_boundary(haystack: str, needle: str) -> bool:
    """True if `needle` appears in `haystack` on word boundaries — not mid-word. Prevents a
    short fragment from matching inside an unrelated longer word."""
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx == -1:
            return False
        before_ok = idx == 0 or not haystack[idx - 1].isalnum()
        end = idx + len(needle)
        after_ok = end == len(haystack) or not haystack[end].isalnum()
        if before_ok and after_ok:
            return True
        start = idx + 1


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
            return self._verify_text(claim, packet)

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
            record = packet.resolve_citation(eid)
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
        record_name = record.fields.get("name")
        # LENIENT label identity (T-E6b (1)): a paraphrasing LLM ("metformin" for a record
        # "Metformin 500 MG Oral Tablet") names the same drug. Only a genuinely different
        # entity (no shared significant token, neither contains the other) BLOCKS.
        if record_name and not _labels_match(claim.name, record_name):
            return VerificationResult(
                verdict=Verdict.BLOCKED,
                reason=(
                    f"medication name contradicts cited record: "
                    f"claim '{claim.name}' vs record '{record_name}'"
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
        record_display = record.fields.get("display")
        # LENIENT label identity (T-E6b (1)): "A1c" cites "Hemoglobin A1c" — same analyte. The
        # VALUE check below stays STRICT so a wrong number still BLOCKS.
        if record_display and not _labels_match(claim.display, record_display):
            return VerificationResult(
                verdict=Verdict.BLOCKED,
                reason=(
                    f"lab display contradicts cited record: "
                    f"claim '{claim.display}' vs record '{record_display}'"
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
        record_display = record.fields.get("display")
        # LENIENT label identity (T-E6b (1)): "Obesity" cites "Body mass index 30+ - obesity
        # (finding)" — same problem (shared significant token). A different condition still
        # BLOCKS (no shared significant token, neither contains the other).
        if record_display and not _labels_match(claim.display, record_display):
            return VerificationResult(
                verdict=Verdict.BLOCKED,
                reason=(
                    f"condition contradicts cited record: "
                    f"claim '{claim.display}' vs record '{record_display}'"
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

    def _verify_text(self, claim: TextClaim, packet: EvidencePacket | None = None) -> VerificationResult:
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

        # D7 fail-closed citation resolution: if evidence_ids are provided but NONE resolve
        # in the packet, the citation is fabricated provenance — BLOCKED, not merely FLAGGED.
        if packet is not None:
            resolved_ids = [eid for eid in claim.evidence_ids if packet.resolve_citation(eid) is not None]
            if not resolved_ids:
                unresolvable = claim.evidence_ids[0]
                return VerificationResult(
                    verdict=Verdict.BLOCKED,
                    reason=f"text claim cites unresolvable evidence: '{unresolvable}' not found in packet",
                    matched_evidence_ids=[],
                )

        return VerificationResult(verdict=Verdict.FLAGGED, reason="free-prose claim not field-verifiable")


def _fmt_number(value: Any) -> str:
    """Render a numeric field without a trailing ``.0`` so '7.8'/'12' match cited text."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
