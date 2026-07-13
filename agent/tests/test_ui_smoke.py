"""Pure-frontend /app UI smoke test (graded: UI smoke tests) — Playwright against a mocked /chat.

Serves ONLY the `ui` router on a loopback port and intercepts POST /chat with a canned verified
brief, so the test is deterministic and touches no serving/verification code. It asserts the
practitioner UI actually works in a real browser: the patient header + sectioned brief render,
the trust badge and "Review before entering" panel appear, citation chips display and open their
popover, and the follow-up input drives a second turn.

Opt-in (not in the default install): `pip install -e ".[ui]" && playwright install chromium`,
then `pytest -m ui`. Without Playwright installed the module self-skips, so the default suite
(browser-free CI) is unaffected.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time

import pytest

pytest.importorskip("playwright.sync_api")  # self-skip when the [ui] extra isn't installed

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from app.routes.ui import router as ui_router  # noqa: E402

pytestmark = [pytest.mark.ui, pytest.mark.filterwarnings("ignore::DeprecationWarning")]

# A canned verified brief with a mix of sections, a confirm-with-patient allergy line, dropped
# verdicts (drives the "Review before entering" panel), and citation ids.
MOCK_CHAT = {
    "brief": (
        "Verified summary (each line re-rendered from cited evidence):\n"
        "- Prediabetes (finding) [active]\n"
        "- Anemia (disorder) [active]\n"
        "- Sertraline 100 MG Oral Tablet — dose not specified — confirm before dosing\n"
        "- Glucose [Mass/volume] in Blood: 89 mg/dL\n"
        "⚠ Allergies: no allergy records returned — confirm with patient (not evidence of no allergies)."
    ),
    "source": "llm",
    "degraded": False,
    "verdicts": ["pass", "pass", "pass", "flagged", "blocked", "blocked", "blocked"],
    "citations": ["Condition:uuid-1:9c8486b4", "MedicationRequest:uuid-2:6ddf2ac1",
                  "Observation:uuid-3:2a8f5c72"],
    "patient": {"name": "José3 Oquendo599", "gender": "male", "birth_date": "1959-12-20"},
    "correlation_id": "test-corr-1",
}

LONG_MOCK_CHAT = {
    **MOCK_CHAT,
    "brief": MOCK_CHAT["brief"] + "\n" + "\n".join(
        f"- Synthetic problem {index} (finding) [active]" for index in range(1, 36)
    ),
}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:  # not on the main thread → no signal handlers
        pass


@contextlib.contextmanager
def _serve():
    app = FastAPI()
    app.include_router(ui_router)   # ONLY /app — /chat is mocked in the browser, never hits here
    port = _free_port()
    server = _Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.parametrize("viewport", [{"width": 1280, "height": 720}, {"width": 390, "height": 844}])
def test_app_ui_smoke(viewport):
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import sync_playwright

    with _serve() as base, sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except PWError as exc:
            pytest.skip(f"Playwright chromium not installed ({exc}); run `playwright install chromium`")
        try:
            page = browser.new_page(viewport=viewport)
            # mock /chat BEFORE navigating (the page auto-fires the brief on load)
            page.route("**/chat", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(LONG_MOCK_CHAT)))
            page.goto(base + "/app?sid=test-sid")

            # 1) the panel loads + the brief renders into sections
            page.wait_for_selector(".section .sh", timeout=15000)
            assert "José3 Oquendo599" in (page.text_content("#pname") or "")
            section_titles = " ".join(page.locator(".section .sh").all_text_contents())
            assert "Problems" in section_titles and "Medications" in section_titles \
                and "Labs" in section_titles and "Allergies" in section_titles

            # The conversation is the sole scrolling flex child. The composer stays pinned while
            # a long brief can reach its final line at desktop and phone widths (Final UI gate).
            scroll_metrics = page.locator("#log").evaluate(
                "el => ({clientHeight: el.clientHeight, scrollHeight: el.scrollHeight})"
            )
            assert scroll_metrics["scrollHeight"] > scroll_metrics["clientHeight"]
            composer_before = page.locator(".composer").bounding_box()
            assert composer_before is not None
            page.locator("#log").evaluate("el => { el.scrollTop = el.scrollHeight; }")
            page.wait_for_timeout(50)
            assert page.locator("#log").evaluate(
                "el => Math.ceil(el.scrollTop + el.clientHeight) >= el.scrollHeight"
            )
            assert page.get_by_text("Synthetic problem 35", exact=False).is_visible()
            composer_after = page.locator(".composer").bounding_box()
            assert composer_after is not None
            assert composer_after["y"] == pytest.approx(composer_before["y"], abs=1)

            # 2) trust badge (verified / dropped) + attention panel
            badges = " ".join(page.locator(".badge").all_text_contents())
            assert "verified" in badges and "dropped" in badges
            assert page.locator(".attention h4").count() >= 1
            assert page.locator(".attention li").count() >= 1        # withheld-claims / allergy flags
            assert page.locator(".item.amber").count() >= 1          # confirm-with-patient allergy line

            # 3) citation chips display AND open their popover
            chips = page.locator(".cites .chip")
            assert chips.count() >= 1
            chips.first.click()
            page.wait_for_selector(".pop", timeout=3000)
            assert "chart record" in (page.text_content(".pop") or "")

            # 4) the follow-up input drives a second turn
            n_attention = page.locator(".attention").count()
            page.fill("#msg", "What are the patient's active problems?")
            page.click("button.send")
            page.wait_for_function(
                f"document.querySelectorAll('.attention').length > {n_attention}", timeout=15000)
            # the typed question shows as a user bubble
            assert "active problems" in " ".join(page.locator(".row.user .bubble").all_text_contents())
        finally:
            browser.close()
