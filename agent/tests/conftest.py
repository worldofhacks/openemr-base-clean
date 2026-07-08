"""Shared test fixtures for the agent suite."""

from __future__ import annotations

import pytest

_REQUIRED_ENV = {
    "OPENEMR_FHIR_BASE_URL": "https://openemr.test/apis/default/fhir",
    "OPENEMR_OAUTH_BASE_URL": "https://openemr.test/oauth2/default",
    "SMART_CLIENT_ID": "test-client-id",
    "SMART_CLIENT_SECRET": "test-client-secret",
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "SESSION_STORE_DSN": "postgresql://u:p@localhost:5432/agent",
}


@pytest.fixture
def complete_env(monkeypatch):
    """Set a complete, valid environment and reset the settings cache."""
    for key, value in _REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    from app.config import get_settings

    get_settings.cache_clear()
    yield _REQUIRED_ENV
    get_settings.cache_clear()
