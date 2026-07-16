"""SMART callback failures stay explicit, sanitized, and fail-closed (W2-D9)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.auth.scopes import ScopeCoverageError


class _ScopeRejectingServices:
    async def complete_callback(self, *, code: str, state: str):
        assert code == "authorization-code-must-not-render"
        assert state == "oauth-state-must-not-render"
        raise ScopeCoverageError(
            "internal marker; Missing: ['user/Observation.rs']; Unexpected: []"
        )


class _RateLimitedServices:
    def begin_launch(self, **_kwargs):
        from app.service import LaunchRateLimited

        raise LaunchRateLimited("internal detail must not render")


def test_callback_returns_sanitized_403_for_incomplete_scope_grant(complete_env):
    from app.main import create_app

    with TestClient(
        create_app(services=_ScopeRejectingServices(), readiness_checks=[]),
        raise_server_exceptions=False,
    ) as client:
        response = client.get(
            "/callback",
            params={
                "code": "authorization-code-must-not-render",
                "state": "oauth-state-must-not-render",
            },
        )

    assert response.status_code == 403
    assert response.json() == {
        "detail": (
            "SMART authorization did not grant the exact required scopes; "
            "correct the client permissions and launch again"
        )
    }
    rendered = response.text
    assert "internal marker" not in rendered
    assert "Observation.rs" not in rendered
    assert "authorization-code-must-not-render" not in rendered
    assert "oauth-state-must-not-render" not in rendered


def test_launch_routes_return_content_free_rate_limit(complete_env):
    from app.main import create_app

    with TestClient(
        create_app(services=_RateLimitedServices(), readiness_checks=[]),
        raise_server_exceptions=False,
    ) as client:
        week1 = client.get("/launch", follow_redirects=False)
        week2 = client.get("/week2/launch", follow_redirects=False)

    for response in (week1, week2):
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "60"
        assert response.json() == {"detail": "SMART launch rate limit exceeded"}
        assert "internal detail" not in response.text
