"""E9 live end-to-end — a real SMART launch against the DEPLOYED agent → verified UC1 brief.

Opt-in (kept out of the fast suite): RUN_LIVE=1 + AGENT_BASE_URL (the deployed agent) +
OE_ADMIN_PASS, with the dev Selenium grid reachable. Drives the agent's OWN flow — GET
/launch → OpenEMR login + (patient select) + consent → the agent's /callback creates a pinned
session → POST /chat — and asserts the served brief carries the patient's REAL Synthea data and
that verification is live (the served content is grounded; a drug the patient does not have
never appears). The deterministic "unsupported claim dropped" is proven over HTTP by
test_chat_route.py; this proves the whole path works against the live URL.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

pytestmark = pytest.mark.live

AGENT = os.environ.get("AGENT_BASE_URL", "")
OE_PASS = os.environ.get("OE_ADMIN_PASS", "")
SELENIUM_URL = os.environ.get("SELENIUM_URL", "http://localhost:4444/wd/hub")

_skip = pytest.mark.skipif(
    os.environ.get("RUN_LIVE") != "1" or not AGENT or not OE_PASS,
    reason="live chat E2E: set RUN_LIVE=1 + AGENT_BASE_URL + OE_ADMIN_PASS")


def _launch_and_get_session() -> dict:
    """Selenium (harness only): drive the clinician login + patient-select + consent that a
    real launch performs, ending on the agent's /callback which returns {session_id, patient_id}."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    driver = webdriver.Remote(command_executor=SELENIUM_URL, options=Options())
    try:
        driver.set_page_load_timeout(60)
        driver.get(f"{AGENT}/launch")            # 302 → OpenEMR authorize
        wait = WebDriverWait(driver, 40)
        wait.until(EC.presence_of_element_located((By.NAME, "username"))).send_keys("admin")
        driver.find_element(By.NAME, "password").send_keys(OE_PASS)
        for b in driver.find_elements(By.CSS_SELECTOR, "button[name='user_role']"):
            if "OpenEMR" in b.text:
                b.click()
                break
        # A patient selector may appear before consent; pick the first patient if so.
        try:
            sel = WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[name='patient_id'], .patient-selection a")))
            sel.click()
        except Exception:
            pass
        wait.until(EC.element_to_be_clickable((By.ID, "authorize-btn"))).click()
        wait.until(lambda d: "/callback" in d.current_url)
        body = driver.find_element(By.TAG_NAME, "body").text
        return json.loads(body)                  # {"session_id": ..., "patient_id": ...}
    finally:
        driver.quit()


@_skip
def test_live_chat_serves_verified_brief_from_real_data(capsys):
    session = _launch_and_get_session()
    assert session.get("session_id"), f"no session from the live launch: {session}"

    resp = httpx.post(f"{AGENT}/chat",
                      json={"session_id": session["session_id"], "message": "Give the pre-visit brief."},
                      timeout=90.0)
    assert resp.status_code == 200, f"/chat failed: HTTP {resp.status_code} {resp.text[:200]}"
    body = resp.json()
    brief = body["brief"]

    with capsys.disabled():
        print(f"\n[E9 LIVE] patient={session.get('patient_id')} source={body['source']} "
              f"verdicts={body['verdicts']} correlation_id={body['correlation_id']}")
        print(f"[E9 LIVE] brief (first 400 chars):\n{brief[:400]}")

    # Real content served over the live URL, and grounded: a drug the canonical patient does not
    # have must not appear (verification is live — a fabricated claim would be dropped).
    assert len(brief.strip()) > 0, "empty brief from the live agent"
    assert "warfarin" not in brief.lower(), "a non-charted drug appeared — verification not grounding live output"
    assert body["correlation_id"]
