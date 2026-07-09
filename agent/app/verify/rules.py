"""E6.2 — the §5 screening rule tables (ARCHITECTURE.md §5 rules 1–6, D7-rev, D12).

Deterministic word-level screens the verifier runs over claim prose and rendered text:

  - **Treatment-verb blocklist** — a claim that tells the clinician to *act* on a
    medication (start / prescribe / increase / titrate / discontinue / …) is
    REFUSED(TREATMENT_ADVICE): the read-only agent narrates, never advises. The blocklist
    matches whole words only so descriptive verbs ("taking", "has") never false-positive.
  - **Forbidden phrasing (F-D.1)** — "declined" / "refused" / "patient objection" are the
    Immunization status-inversion trap; they must never survive into rendered output.
  - **Criticality-as-risk phrasing (F-D.4)** — "high risk" / "high-risk" must never be
    surfaced from allergy criticality.

These tables are demo-depth (the extension path is documented in §5). All matching is
case-insensitive and whole-word so the screens are auditable invariants, not fuzzy filters.
"""

from __future__ import annotations

import re

# Verbs that turn a description into a treatment instruction (§5 treatment-verb blocklist).
# Read-only agent → any of these in claim prose is REFUSED(TREATMENT_ADVICE).
TREATMENT_VERBS: frozenset[str] = frozenset({
    "start",
    "started",
    "starting",
    "initiate",
    "initiated",
    "prescribe",
    "prescribed",
    "prescribing",
    "increase",
    "increased",
    "increasing",
    "decrease",
    "decreased",
    "decreasing",
    "titrate",
    "titrated",
    "titrating",
    "taper",
    "tapered",
    "tapering",
    "discontinue",
    "discontinued",
    "discontinuing",
    "stop",
    "stopped",
    "stopping",
    "hold",
    "held",
    "administer",
    "administered",
    "adjust",
    "adjusted",
    "switch",
    "switched",
    "recommend",
    "recommended",
    "advise",
    "advised",
})

# F-D.1: the Immunization status-inversion trap phrasing — never rendered verbatim.
FORBIDDEN_PHRASES: tuple[str, ...] = (
    "declined",
    "refused",
    "patient objection",
)

# F-D.4: criticality is null dataset-wide and never surfaced as risk.
CRITICALITY_RISK_PHRASES: tuple[str, ...] = (
    "high risk",
    "high-risk",
)


def _words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[a-zA-Z']+", text)}


def contains_treatment_verb(text: str) -> bool:
    """True iff `text` contains a whole-word treatment verb (case-insensitive)."""
    return bool(_words(text) & TREATMENT_VERBS)


def contains_forbidden_phrase(text: str) -> bool:
    """True iff `text` contains an F-D.1 status-inversion trap phrase (case-insensitive)."""
    low = text.lower()
    return any(phrase in low for phrase in FORBIDDEN_PHRASES)


def contains_criticality_risk_phrase(text: str) -> bool:
    """True iff `text` surfaces allergy criticality as risk (F-D.4)."""
    low = text.lower()
    return any(phrase in low for phrase in CRITICALITY_RISK_PHRASES)
