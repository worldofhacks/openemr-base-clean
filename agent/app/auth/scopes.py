"""SMART scope policy — minimum-necessary (ARCHITECTURE.md D9, F-C.5).

The agent requests exactly the scopes its six read tools need, plus `openid` for the
clinician identity that pins the session (D12). Two deliberate exclusions:

- **offline_access — EXCLUDED (D9 addendum 2026-07-09).** The session is bounded by
  MIN(token expiry, idle timeout, turn cap) with re-launch on expiry (§3a/D12), so a
  refresh token buys almost nothing and is a long-lived credential that widens the
  PHI-access surface. Minimum-necessary (F-C.5) says don't request it. The agent does
  not store or use a refresh token; a mid-session token expiry prompts re-launch, not
  silent refresh. (This supersedes ARCHITECTURE §6's "refresh_token grant, else
  re-launch" — flagged for arch-finalize reconcile.)
- **api:oemr / api:fhir — EXCLUDED.** FHIR reads succeed on `user/*.read` alone
  (verified live E2.1), so the broad api:* scopes are not minimum-necessary.

The runtime guard `assert_required_scopes_granted` fails early (at token exchange) if
OpenEMR granted fewer scopes than requested, so the meds/labs/encounter tools never
401 mid-turn — granted ≠ requested, and we check the granted set.
"""

from __future__ import annotations

REQUIRED_FHIR_SCOPES: frozenset[str] = frozenset({
    "user/Patient.read",
    "user/Condition.read",
    "user/MedicationRequest.read",
    "user/AllergyIntolerance.read",
    "user/Observation.read",
    "user/Encounter.read",
})

# openid identifies the clinician (OIDC sub) that pins the session (D12).
OIDC_SCOPES: frozenset[str] = frozenset({"openid"})

REQUESTED_SCOPES: frozenset[str] = OIDC_SCOPES | REQUIRED_FHIR_SCOPES


class ScopeCoverageError(Exception):
    """OpenEMR granted fewer scopes than required — a tool would 401 at runtime."""


def requested_scope_string() -> str:
    """The space-joined scope string for the authorize request (stable order)."""
    return " ".join(sorted(REQUESTED_SCOPES))


def missing_required_scopes(granted: list[str] | set[str]) -> set[str]:
    return set(REQUIRED_FHIR_SCOPES) - set(granted)


def assert_required_scopes_granted(granted: list[str] | set[str]) -> None:
    """Fail closed if any required FHIR read scope was not granted (F-C.5 coverage)."""
    missing = missing_required_scopes(granted)
    if missing:
        raise ScopeCoverageError(
            "OpenEMR did not grant all required FHIR read scopes; the affected tools "
            f"would 401 at runtime. Missing: {sorted(missing)}"
        )
