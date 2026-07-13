"""E1.1 — typed config must fail fast on missing required env (ARCHITECTURE.md §2, D3).

The architecture requires configuration wired in, not in business logic, and
fail-fast at startup rather than a 500 at request time. These tests pin that the
Settings object refuses to construct when a required env var is absent, loads
cleanly (no hardcoded secrets) when the env is complete, and rejects a non-https
OpenEMR URL (F-S.9).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

REQUIRED_ENV = {
    "OPENEMR_FHIR_BASE_URL": "https://openemr-production-cc95.up.railway.app/apis/default/fhir",
    "OPENEMR_OAUTH_BASE_URL": "https://openemr-production-cc95.up.railway.app/oauth2/default",
    "SMART_CLIENT_ID": "test-client-id",
    "SMART_CLIENT_SECRET": "test-client-secret",
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "SESSION_STORE_DSN": "postgresql://u:p@localhost:5432/agent",
}

# Optional vars must be cleared too so the host env can't leak into a test.
_ALL_KEYS = list(REQUIRED_ENV) + [
    "LANGFUSE_HOST",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_LOG_CONTENT",
]


def _load_from_env(monkeypatch, env: dict[str, str]):
    for key in _ALL_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from app.config import Settings

    return Settings()


def test_config_loads_when_all_required_present(monkeypatch):
    settings = _load_from_env(monkeypatch, REQUIRED_ENV)
    assert str(settings.openemr_fhir_base_url).startswith("https://")
    assert settings.smart_client_id == "test-client-id"
    # Secrets must never leak via repr/str (no hardcoded secrets, D-secrets).
    assert "test-client-secret" not in repr(settings)
    assert "sk-ant-test" not in repr(settings)
    assert settings.langfuse_log_content is False  # D16: production-safe default preserves D5


def test_langfuse_content_logging_requires_explicit_opt_in(monkeypatch):
    settings = _load_from_env(monkeypatch, {**REQUIRED_ENV, "LANGFUSE_LOG_CONTENT": "true"})
    assert settings.langfuse_log_content is True


@pytest.mark.parametrize("missing", sorted(REQUIRED_ENV.keys()))
def test_config_missing_required_env_fails_fast(monkeypatch, missing: str):
    env = {k: v for k, v in REQUIRED_ENV.items() if k != missing}
    with pytest.raises(ValidationError):
        _load_from_env(monkeypatch, env)


def test_config_rejects_non_https_openemr_url(monkeypatch):
    # F-S.9: TLS is edge-only; the agent must pin https:// and reject downgrade.
    env = {**REQUIRED_ENV, "OPENEMR_FHIR_BASE_URL": "http://openemr.internal/apis/default/fhir"}
    with pytest.raises(ValidationError):
        _load_from_env(monkeypatch, env)
