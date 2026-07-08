"""E1.1 — the app boots with a valid environment (ARCHITECTURE.md §2)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_app_boots_and_exposes_root(complete_env):
    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["service"] == "clinical-copilot-agent"
        # No secret should ever appear in a public response.
        assert "test-client-secret" not in resp.text
        assert "sk-ant-test" not in resp.text
