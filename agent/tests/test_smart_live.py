"""E2.1 live proof — full authorization_code + PKCE(S256) flow against the LIVE
deployed OpenEMR, driven by Selenium, ending in a REAL FHIR read (§4, D2, D9, F-A.2).

Selenium lives ONLY here in the test harness — it drives the interactive browser
login + consent that a human clinician would perform. The runtime agent
(`app/auth/smart_client.py`) never imports Selenium; it owns only the authorize-URL
construction and the token exchange.

Opt-in (kept out of the fast unit suite): requires RUN_LIVE=1 and the env vars
COPILOT_CLIENT_ID / COPILOT_CLIENT_SECRET / OE_ADMIN_PASS, plus the dev stack's
Selenium container reachable at SELENIUM_URL (default http://localhost:4444/wd/hub).
"""

from __future__ import annotations

import os
import secrets
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.auth.scopes import (
    REQUIRED_FHIR_SCOPES,
    assert_required_scopes_granted,
    requested_scope_string,
)
from app.auth.smart_client import SmartClient, generate_pkce
from app.evidence.packet import build_evidence_packet
from app.tools.contracts import ToolStatus
from app.tools.fhir_client import FhirClient
from app.tools.fhir_tools import run_previsit_fanout

CANONICAL_PATIENT = "a234b786-539a-4f9a-96a0-432293226f02"

pytestmark = pytest.mark.live

BASE = os.environ.get("OPENEMR_BASE_URL", "https://openemr-production-cc95.up.railway.app")
SELENIUM_URL = os.environ.get("SELENIUM_URL", "http://localhost:4444/wd/hub")
# Minimum-necessary scope set (openid + the six FHIR read scopes; NO offline_access).
SCOPE = requested_scope_string()

_skip = pytest.mark.skipif(
    os.environ.get("RUN_LIVE") != "1"
    or not os.environ.get("COPILOT_CLIENT_ID")
    or not os.environ.get("COPILOT_CLIENT_SECRET")
    or not os.environ.get("OE_ADMIN_PASS"),
    reason="live SMART flow: set RUN_LIVE=1 + COPILOT_CLIENT_ID/SECRET + OE_ADMIN_PASS",
)


def _make_client() -> SmartClient:
    return SmartClient(
        client_id=os.environ["COPILOT_CLIENT_ID"],
        client_secret=os.environ["COPILOT_CLIENT_SECRET"],
        authorize_endpoint=f"{BASE}/oauth2/default/authorize",
        token_endpoint=f"{BASE}/oauth2/default/token",
        fhir_base_url=f"{BASE}/apis/default/fhir",
        redirect_uri=f"{BASE}/callback",
    )


def _drive_browser_for_code(authorize_url: str, expected_state: str) -> str:
    """Selenium (harness only): perform the clinician's login + consent, return the
    authorization code from the redirect. Validates the returned state (CSRF)."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    driver = webdriver.Remote(command_executor=SELENIUM_URL, options=Options())
    try:
        driver.set_page_load_timeout(45)
        driver.get(authorize_url)
        wait = WebDriverWait(driver, 30)
        # 1) login
        wait.until(EC.presence_of_element_located((By.NAME, "username"))).send_keys("admin")
        driver.find_element(By.NAME, "password").send_keys(os.environ["OE_ADMIN_PASS"])
        for b in driver.find_elements(By.CSS_SELECTOR, "button[name='user_role']"):
            if "OpenEMR" in b.text:
                b.click()
                break
        # 2) consent — scopes are pre-selected; click Authorize
        wait.until(EC.element_to_be_clickable((By.ID, "authorize-btn"))).click()
        # 3) capture the redirect to /callback?code=...&state=...
        wait.until(lambda d: "code=" in d.current_url and "/callback" in d.current_url)
        q = {k: v[0] for k, v in parse_qs(urlsplit(driver.current_url).query).items()}
        assert q.get("state") == expected_state, "state mismatch — possible CSRF"
        assert "code" in q, f"no code in redirect: {driver.current_url}"
        return q["code"]
    finally:
        driver.quit()


@_skip
@pytest.mark.asyncio
async def test_live_auth_code_flow_returns_real_fhir_data(capsys):
    client = _make_client()
    verifier, challenge, method = generate_pkce()
    assert method == "S256"
    state = secrets.token_hex(12)
    authorize_url = client.build_authorize_url(state=state, code_challenge=challenge, scope=SCOPE)

    code = _drive_browser_for_code(authorize_url, state)

    # Exchange the code for a delegated token (PKCE completed).
    token = await client.exchange_code(code=code, code_verifier=verifier)
    assert token.access_token.get_secret_value(), "no access_token returned"

    # GATE: all six FHIR read scopes must come back granted, or the meds/labs/
    # encounter tools would 401 at runtime (the coverage gap being closed here).
    assert_required_scopes_granted(token.scopes)
    assert REQUIRED_FHIR_SCOPES <= set(token.scopes)
    # offline_access was not requested → no refresh token (D9 addendum, minimum-necessary).
    assert token.refresh_token is None, "unexpected refresh token — offline_access should be dropped"

    # The whole point of E2 "done": the token must return REAL FHIR data.
    async with httpx.AsyncClient(timeout=20.0) as http:
        pt = await http.get(
            f"{BASE}/apis/default/fhir/Patient",
            params={"_count": 3},
            headers={**token.auth_header(), "Accept": "application/fhir+json"},
        )
        assert pt.status_code == 200, f"FHIR Patient search failed: HTTP {pt.status_code}"
        bundle = pt.json()
        assert bundle.get("resourceType") == "Bundle"
        assert bundle.get("total", 0) >= 1 and bundle.get("entry"), "no real patients returned"
        first_id = bundle["entry"][0]["resource"]["id"]

        # A second, patient-scoped read to prove it's not a fluke.
        cond = await http.get(
            f"{BASE}/apis/default/fhir/Condition",
            params={"patient": "a234b786-539a-4f9a-96a0-432293226f02", "_count": 100},
            headers={**token.auth_header(), "Accept": "application/fhir+json"},
        )
        assert cond.status_code == 200

    # E3.2 GATE: run the six-tool fan-out with the delegated token and prove EVERY
    # tool returns real data — especially meds/labs/encounter (the scope gap closed).
    fclient = FhirClient(base_url=f"{BASE}/apis/default/fhir",
                         access_token=token.access_token.get_secret_value())
    fanout = await run_previsit_fanout(fclient, CANONICAL_PATIENT,
                                       per_call_timeout=12.0, turn_budget=25.0)
    failed = {n: r.missing_reason for n, r in fanout.items() if r.status is ToolStatus.FAILED}
    assert not failed, f"tools failed against live OpenEMR: {failed}"
    for name in ("get_active_medications", "get_recent_labs", "get_encounters"):
        assert fanout[name].status is ToolStatus.OK, f"{name} returned {fanout[name].status}, not OK"

    # E4 GATE: build the EvidencePacket from the live fan-out; all evidence ids must
    # be unique (the E6 verifier resolves every claim against them).
    packet = build_evidence_packet(CANONICAL_PATIENT, fanout)
    eids = [r.evidence_id for r in packet.records]
    assert eids, "empty evidence packet from live data"
    assert len(eids) == len(set(eids)), "duplicate evidence ids in live packet"

    with capsys.disabled():
        print(f"\n[E2 LIVE] token OK — granted scopes ({len(token.scopes)}): {sorted(token.scopes)}")
        print(f"[E4 LIVE] packet: {len(packet.records)} evidence records, "
              f"all ids unique={len(eids) == len(set(eids))}, notices={len(packet.notices)}")
        print(f"[E2 LIVE] all six required FHIR scopes granted: {REQUIRED_FHIR_SCOPES <= set(token.scopes)}")
        print(f"[E2 LIVE] refresh_token present: {token.refresh_token is not None} (offline_access dropped)")
        print(f"[E2 LIVE] FHIR Patient bundle total={bundle['total']} first_id={first_id}")
        print("[E3.2 LIVE] six-tool fan-out against live OpenEMR:")
        for name, r in fanout.items():
            print(f"    {name:24s} {r.status.value:10s} records={len(r.records)}")
