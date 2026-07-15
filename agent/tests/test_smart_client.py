"""E2.1 — SMART/OAuth authorization_code + PKCE(S256) client (§4, §5a, D2, D9, F-A.2, F-S.5, F-S.3, §6/D14).

Unit tests with a mocked token endpoint (no network). The live proof that the
token returns real FHIR data lives in test_smart_live.py (Selenium-driven,
opt-in). These pin: correct S256 PKCE, a SMART-conformant authorize URL, the
auth-code token exchange, the two guardrails (never client_credentials — F-S.5;
never APICSRFTOKEN — F-S.3), launch/patient binding, and an explicit
disabled-client error (§6/D14) instead of a hang.
"""

from __future__ import annotations

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.auth.smart_client import (
    CoPilotNotEnabledError,
    SmartAuthError,
    SmartClient,
    TokenResponse,
    forbid_nondelegated_grant,
    generate_pkce,
)

AUTHZ = "https://openemr.test/oauth2/default/authorize"
TOKEN = "https://openemr.test/oauth2/default/token"
FHIR = "https://openemr.test/apis/default/fhir"
REDIRECT = "https://openemr.test/callback"
SCOPE = "openid offline_access api:fhir user/Patient.read user/Condition.read"


def _client(handler=None) -> SmartClient:
    http = None
    if handler is not None:
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SmartClient(
        client_id="cid-123",
        client_secret="sek-456",
        authorize_endpoint=AUTHZ,
        token_endpoint=TOKEN,
        fhir_base_url=FHIR,
        redirect_uri=REDIRECT,
        http_client=http,
    )


def _jwt_with_scopes(scopes: set[str]) -> str:
    def encode(value: object) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{encode({'alg': 'RS256'})}.{encode({'scopes': sorted(scopes)})}.signature"


# --- PKCE ------------------------------------------------------------------

def test_pkce_s256_challenge_is_correct():
    verifier, challenge, method = generate_pkce()
    assert method == "S256"
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    assert "=" not in challenge and "+" not in challenge and "/" not in challenge  # url-safe, unpadded


# --- authorize URL ---------------------------------------------------------

def test_authorize_url_is_smart_conformant():
    _, challenge, _ = generate_pkce()
    url = _client().build_authorize_url(state="st-1", code_challenge=challenge, scope=SCOPE)
    parts = urlsplit(url)
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert url.startswith(AUTHZ)
    assert q["response_type"] == "code"
    assert q["client_id"] == "cid-123"
    assert q["redirect_uri"] == REDIRECT
    assert q["code_challenge_method"] == "S256"
    assert q["code_challenge"] == challenge
    assert q["state"] == "st-1"
    assert q["aud"] == FHIR  # SMART requires aud = FHIR base
    assert q["scope"] == SCOPE


def test_authorize_url_ehr_launch_adds_launch_param_and_scope():
    _, challenge, _ = generate_pkce()
    url = _client().build_authorize_url(
        state="st", code_challenge=challenge, scope="openid user/Patient.read", launch="launch-tok-9"
    )
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert q["launch"] == "launch-tok-9"
    assert "launch" in q["scope"].split()  # EHR-launch requires the launch scope


# --- token exchange + guardrails ------------------------------------------

@pytest.mark.asyncio
async def test_exchange_code_uses_authorization_code_and_never_apicsrftoken():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = {k.lower(): v for k, v in request.headers.items()}
        captured["body"] = parse_qs(request.content.decode())
        return httpx.Response(200, json={
            "access_token": "AT-real", "token_type": "Bearer", "expires_in": 3600,
            "scope": "openid user/Patient.read", "refresh_token": "RT-1",
        })

    await _client(handler).exchange_code(code="auth-code-1", code_verifier="ver-abc")
    body = captured["body"]
    assert body["grant_type"] == ["authorization_code"]           # F-S.5: delegated grant only
    assert body["code_verifier"] == ["ver-abc"]                    # PKCE completed
    assert body["client_secret"] == ["sek-456"]
    assert body["redirect_uri"] == [REDIRECT]
    assert "apicsrftoken" not in captured["headers"]               # F-S.3: never the local-API shortcut


def test_forbid_nondelegated_grant_rejects_client_credentials():
    # F-S.5 guardrail: client_credentials attributes to the synthetic oe-system user.
    with pytest.raises(SmartAuthError):
        forbid_nondelegated_grant("client_credentials")
    forbid_nondelegated_grant("authorization_code")  # the only allowed grant — no raise


@pytest.mark.asyncio
async def test_exchange_code_parses_token_and_launch_patient_context():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "access_token": "AT-xyz", "token_type": "Bearer", "expires_in": 3600,
            "scope": "openid user/Patient.read user/Condition.read",
            "patient": "a234b786-539a-4f9a-96a0-432293226f02", "refresh_token": "RT",
        })

    tok = await _client(handler).exchange_code(code="c", code_verifier="v")
    assert isinstance(tok, TokenResponse)
    assert tok.access_token.get_secret_value() == "AT-xyz"
    assert tok.patient == "a234b786-539a-4f9a-96a0-432293226f02"   # launch/patient bound
    assert "user/Condition.read" in tok.scopes
    assert "AT-xyz" not in repr(tok)                                # token never leaks via repr


@pytest.mark.asyncio
async def test_exchange_code_persists_openemr_bearer_scope_authority() -> None:
    granted = {"openid", "api:oemr", "user/document.crs"}

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": _jwt_with_scopes(granted),
                "scope": "openid user/document.crs",
            },
        )

    token = await _client(handler).exchange_code(code="c", code_verifier="v")

    assert set(token.scopes) == granted


@pytest.mark.asyncio
async def test_disabled_client_raises_explicit_not_enabled_error():
    # §6 / D14: a disabled client must fail with a clear message, not a hang or a raw 500.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client", "error_description": "Client authentication failed"})

    with pytest.raises(CoPilotNotEnabledError):
        await _client(handler).exchange_code(code="c", code_verifier="v")


@pytest.mark.asyncio
async def test_generic_token_error_raises_smart_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant", "error_description": "code expired"})

    with pytest.raises(SmartAuthError):
        await _client(handler).exchange_code(code="c", code_verifier="v")
