"""E3 gate — minimum-necessary SMART scope policy (D9 addendum 2026-07-09, F-C.5).

The agent requests exactly the scopes the six read tools need, plus openid (for the
clinician identity that pins the session). offline_access is DELIBERATELY excluded:
the session is bounded by MIN(token, idle, turn cap) with re-launch on expiry, so a
refresh token is not minimum-necessary. These tests freeze that policy and the
runtime guard that fails early if a required scope wasn't granted (so meds/labs/
encounter tools never 401 mid-turn).
"""

from __future__ import annotations

import pytest

from app.auth.scopes import (
    REQUESTED_SCOPES,
    REQUIRED_FHIR_SCOPES,
    ScopeCoverageError,
    assert_required_scopes_granted,
    requested_scope_string,
)

SIX = {
    "user/Patient.read", "user/Condition.read", "user/MedicationRequest.read",
    "user/AllergyIntolerance.read", "user/Observation.read", "user/Encounter.read",
}


def test_required_scopes_are_exactly_the_six_read_tools():
    assert REQUIRED_FHIR_SCOPES == SIX


def test_requested_set_is_minimum_necessary_openid_plus_six_no_offline_access():
    assert REQUESTED_SCOPES == {"openid"} | SIX
    assert "offline_access" not in REQUESTED_SCOPES  # D9 addendum: no refresh token
    # No broad api:* scopes either — FHIR reads work via user/*.read alone.
    assert not any(s.startswith("api:") for s in REQUESTED_SCOPES)


def test_requested_scope_string_is_stable_and_space_joined():
    s = requested_scope_string()
    assert set(s.split()) == REQUESTED_SCOPES
    assert "offline_access" not in s


def test_assert_granted_passes_when_all_six_present():
    granted = ["openid", *SIX]
    assert_required_scopes_granted(granted)  # no raise


def test_assert_granted_raises_naming_the_missing_scopes():
    # The exact gap the user flagged: meds/labs/encounter dropped ⇒ tools would 401.
    granted = ["openid", "user/Patient.read", "user/Condition.read", "user/AllergyIntolerance.read"]
    with pytest.raises(ScopeCoverageError) as ei:
        assert_required_scopes_granted(granted)
    msg = str(ei.value)
    assert "user/MedicationRequest.read" in msg
    assert "user/Observation.read" in msg
    assert "user/Encounter.read" in msg
