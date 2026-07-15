"""Replacement delegated-client scope contract (W2-D1/D9; §2/§3)."""

from __future__ import annotations

import pytest

from app.auth.scopes import (
    REQUIRED_FHIR_SCOPES,
    ScopeCoverageError,
    W2_DOCUMENT_SCOPES,
    W2_REQUESTED_SCOPES,
    assert_w2_scopes_granted,
    requested_w2_scope_string,
)


def test_w2_scope_manifest_is_exact_delegated_union() -> None:
    expected_write = {
        "api:oemr",
        "user/document.crs",
        "user/DocumentReference.rs",
        "user/Binary.read",
        "user/vital.crus",
        "user/Observation.rs",
    }
    assert W2_DOCUMENT_SCOPES == expected_write
    assert W2_REQUESTED_SCOPES == {
        "openid",
        "offline_access",
        "launch",
        "launch/patient",
        *REQUIRED_FHIR_SCOPES,
        *expected_write,
    }
    assert "client_credentials" not in W2_REQUESTED_SCOPES
    assert set(requested_w2_scope_string().split()) == W2_REQUESTED_SCOPES


def test_w2_scope_attestation_fails_closed_on_missing_write_scope() -> None:
    granted = set(W2_REQUESTED_SCOPES) - {"user/document.crs"}

    with pytest.raises(ScopeCoverageError, match="user/document.crs"):
        assert_w2_scopes_granted(granted)

    assert_w2_scopes_granted(set(W2_REQUESTED_SCOPES))
