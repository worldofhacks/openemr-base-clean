"""Fail-fast document-runtime configuration (W2-D1/D9; §2/§3/§6)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


_BASE = {
    "openemr_fhir_base_url": "https://openemr.test/apis/default/fhir",
    "openemr_oauth_base_url": "https://openemr.test/oauth2/default",
    "smart_client_id": "synthetic-client",
    "smart_client_secret": "synthetic-secret",
    "anthropic_api_key": "synthetic-provider-key",
    "session_store_dsn": "postgresql://u:p@localhost:5432/agent",
    "agent_callback_url": "https://agent.test/callback",
}

_W2 = {
    **_BASE,
    "w2_document_runtime_enabled": True,
    "openemr_rest_base_url": "https://openemr.test/apis/default",
    "source_document_category_id": "source-category-synthetic",
    "source_document_category_acl": "patients|docs",
    "artifact_document_category_id": "artifact-category-synthetic",
    "artifact_document_category_acl": "patients|docs",
    "openemr_binary_readback_safe": True,
    "document_credential_key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
}


def test_w2_runtime_config_is_typed_and_path_pinned() -> None:
    settings = Settings(**_W2)

    assert settings.w2_document_runtime_enabled is True
    assert settings.source_document_path == "/AI-Source-Documents"
    assert settings.artifact_document_path == "/AI-Extractions"
    assert settings.document_worker_lease_seconds > 0
    assert settings.document_worker_poll_seconds > 0


@pytest.mark.parametrize(
    "missing",
    (
        "openemr_rest_base_url",
        "agent_callback_url",
        "source_document_category_id",
        "source_document_category_acl",
        "artifact_document_category_id",
        "artifact_document_category_acl",
        "openemr_binary_readback_safe",
        "document_credential_key",
    ),
)
def test_enabled_w2_runtime_fails_fast_without_attested_write_config(
    missing: str,
) -> None:
    values = {key: value for key, value in _W2.items() if key != missing}
    if missing == "openemr_binary_readback_safe":
        values[missing] = False

    with pytest.raises(ValidationError):
        Settings(**values)


def test_w2_credential_key_is_validated_and_masked() -> None:
    settings = Settings(**_W2)

    assert settings.document_credential_key is not None
    assert "AAAAAAAA" not in repr(settings)
    with pytest.raises(ValidationError):
        Settings(**{**_W2, "document_credential_key": "not-a-fernet-key"})


def test_w2_rest_url_rejects_plaintext() -> None:
    with pytest.raises(ValidationError):
        Settings(**{**_W2, "openemr_rest_base_url": "http://openemr.test/apis/default"})


def test_enabled_runtime_does_not_require_one_global_patient_or_encounter() -> None:
    settings = Settings(
        **{
            **_W2,
            # Retired Railway values cannot influence the new resolver even during
            # rolling cleanup because unknown environment keys are ignored.
            "openemr_legacy_patient_uuid": "not-a-uuid",
            "openemr_legacy_patient_id": "0",
            "openemr_legacy_encounter_uuid": "not-a-uuid",
            "openemr_legacy_encounter_id": "0",
        }
    )

    assert settings.w2_document_runtime_enabled is True
    assert not hasattr(settings, "openemr_legacy_patient_uuid")


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("agent_callback_url", "http://localhost:8000/callback"),
        ("agent_callback_url", "https://agent.test/not-callback"),
        ("source_document_path", "/Medical-Record"),
        ("artifact_document_path", "/Medical-Record"),
        ("source_document_category_acl", "patients|demo"),
        ("artifact_document_category_acl", "patients|demo"),
    ),
)
def test_w2_activation_rejects_callback_path_or_acl_drift(
    field: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        Settings(**{**_W2, field: value})
