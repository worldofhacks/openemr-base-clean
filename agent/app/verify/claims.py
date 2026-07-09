"""E6.1 — typed claim value objects + verdict/refusal vocabulary (ARCHITECTURE.md §5, D7).

The LLM answers ONLY in typed claims; each claim carries `evidence_ids` back into the
EvidencePacket so the §5 verifier can resolve every assertion against a CITED record. This
module freezes the claim shapes and the verdict/refusal vocabulary the whole §5 pipeline
speaks — the deterministic hand-off boundary between the model and the verifier.

`Verdict` (pass | flagged | blocked | refused(kind)) and `RefusalKind` (the D12 canonical
hard-stops) are str-backed enums so a verdict can be logged/serialized per response. The
claim value objects are Pydantic `BaseModel`s (consistent with `app.tools.contracts`) so
the caller constructs them with keyword args and reads attributes directly.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Verdict(str, Enum):
    """The four serving verdicts (§5 D7). str-backed so it logs/serializes cleanly."""

    PASS = "pass"
    FLAGGED = "flagged"
    BLOCKED = "blocked"
    REFUSED = "refused"


class RefusalKind(str, Enum):
    """The D12 canonical hard-stops (§5/§6). str-backed for per-response logging."""

    DECEASED = "deceased"
    TREATMENT_ADVICE = "treatment_advice"
    WRONG_PATIENT = "wrong_patient"
    AMBIGUOUS = "ambiguous"
    EXPIRED_SESSION = "expired_session"


class Claim(BaseModel):
    """Base claim: `evidence_ids` is intrinsic to every claim (D7).

    An uncited claim is representable (empty list — the verifier BLOCKS it); construction
    never silently fabricates a citation.
    """

    model_config = ConfigDict(extra="forbid")

    evidence_ids: list[str] = Field(default_factory=list)


class MedicationClaim(Claim):
    """A medication assertion (§5 D7). `dose` is optional — silence, not an invented dose (rule 6/F-D.2)."""

    name: str
    dose: str | None = None


class LabValueClaim(Claim):
    """A lab/observation value assertion (§5a)."""

    display: str
    value: str | None = None
    unit: str | None = None


class ConditionClaim(Claim):
    """A condition assertion (§5 rule 4/F-D.6).

    `present` defaults True; `present=False` encodes the "no history of X" negation the
    F-D.6 rule must be able to reject when an inactive/resolved match exists.
    """

    display: str
    present: bool = True


class AllergyClaim(Claim):
    """An allergy assertion (§5 rule 2/F-D.4).

    A substance-only claim is fine. Any non-None `risk` is a criticality-derived claim the
    verifier rejects — criticality is null dataset-wide and never trusted as risk.
    """

    substance: str
    risk: str | None = None


class ImmunizationClaim(Claim):
    """An immunization assertion (§5 rule 1/F-D.1).

    `declined` defaults False; `declined=True` is the inverted-status trap (the stock FHIR
    mapper reports completed vaccines as "not-done"/"patient objection") the verifier blocks.
    """

    vaccine: str
    declined: bool = False


class TextClaim(Claim):
    """Free prose fed to the forbidden-phrasing + treatment-verb screens (§5)."""

    text: str
