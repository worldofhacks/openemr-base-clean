"""Real-browser coverage for the Week 2 click-to-source page preview."""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import socket
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("playwright.sync_api")

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from PIL import Image  # noqa: E402
from playwright.sync_api import Error as PlaywrightError  # noqa: E402
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from app.routes.week2_ui import router as week2_router  # noqa: E402
from app.session.store import Session  # noqa: E402

pytestmark = [pytest.mark.ui, pytest.mark.filterwarnings("ignore::DeprecationWarning")]


class _Services:
    settings = SimpleNamespace(w2_document_runtime_enabled=True)

    async def resolve_session(self, session_id: str) -> Session:
        assert session_id == "session-synthetic"
        now = datetime.now(timezone.utc)
        return Session(
            session_id=session_id,
            clinician_sub="Practitioner/clinician-synthetic",
            patient_id="patient-synthetic",
            created_at=now,
            last_activity_at=now,
            token_expires_at=now + timedelta(hours=1),
            idle_timeout_s=1800,
            turn_cap=20,
        )

    async def resolve_document_route_context(self, session: Session):
        assert session.patient_id == "patient-synthetic"
        return True, None


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:
        pass


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextlib.contextmanager
def _serve():
    app = FastAPI()
    app.state.services = _Services()
    app.include_router(week2_router)
    port = _free_port()
    server = _Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _attempt in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _png() -> bytes:
    image = Image.new("RGB", (24, 16), "white")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _trend_payload() -> dict[str, object]:
    citation = {
        "source_type": "uploaded_document",
        "source_id": "doc-preview",
        "page_or_section": "2",
        "field_or_chunk_id": "results[0].value",
        "quote_or_value": "1.6 mg/dL",
    }
    return {
        "series": [
            {
                "test_name": "Magnesium",
                "unit": "mg/dL",
                "points": [
                    {
                        "document_id": "doc-preview",
                        "result_index": 0,
                        "collection_date": "2026-07-15",
                        "value": "1.6",
                        "display_value": "1.6",
                        "citation": citation,
                        "date_citation": {
                            **citation,
                            "field_or_chunk_id": "results[0].collection_date",
                            "quote_or_value": "07/15/2026",
                        },
                        "page": 2,
                        "bbox": {"x0": 0.2, "y0": 0.3, "x1": 0.6, "y1": 0.4},
                    }
                ],
            }
        ]
    }


def test_click_fetches_page_png_and_sets_image_overlay_and_visible_error():
    page_requests: list[dict[str, object]] = []
    page_failure = {"enabled": False}
    png = _png()

    def handle_documents(route, request) -> None:
        parsed = urlsplit(request.url)
        path = parsed.path
        if path == "/documents" and request.method == "POST":
            route.fulfill(
                status=202,
                content_type="application/json",
                body=json.dumps(
                    {
                        "job_id": "job-preview",
                        "document_id": "doc-preview",
                        "state": "queued",
                        "status_url": "/documents/doc-preview/status",
                        "correlation_id": "corr.preview",
                    }
                ),
            )
            return
        if path == "/documents/doc-preview/status":
            route.fulfill(
                content_type="application/json",
                body=json.dumps(
                    {
                        "document_id": "doc-preview",
                        "state": "complete",
                        "reason": None,
                        "correlation_id": "corr.preview",
                        "updated_ts": "2026-07-16T12:00:00+00:00",
                        "fields_grounded": 1,
                        "fields_unsupported": 0,
                        "attempt_count": 1,
                        "next_retry_at": None,
                    }
                ),
            )
            return
        if path == "/documents/doc-preview/extraction-report":
            route.fulfill(
                content_type="application/json",
                body=json.dumps(
                    {
                        "document_id": "doc-preview",
                        "doc_type": "lab_pdf",
                        "state": "complete",
                        "fields_grounded": 1,
                        "fields_unsupported": 0,
                        "fields": [
                            {
                                "field_path": "results.0.value",
                                "verdict": "grounded",
                                "display_value": "1.6",
                                "page": 2,
                                "bbox": {
                                    "x0": 0.1,
                                    "y0": 0.1,
                                    "x1": 0.3,
                                    "y1": 0.2,
                                },
                                "citation": {
                                    "source_type": "uploaded_document",
                                    "source_id": "doc-preview",
                                    "page_or_section": "2",
                                    "field_or_chunk_id": "results[0].value",
                                    "quote_or_value": "1.6 mg/dL",
                                },
                            }
                        ],
                    }
                ),
            )
            return
        if path == "/documents/doc-preview/readback-verification":
            digest = "a" * 64
            verified = {
                "algorithm": "sha256",
                "expected_hash": digest,
                "observed_hash": digest,
                "verified": True,
            }
            route.fulfill(
                content_type="application/json",
                body=json.dumps(
                    {
                        "document_id": "doc-preview",
                        "source": verified,
                        "artifact": verified,
                    }
                ),
            )
            return
        if path == "/documents/lab-trends":
            route.fulfill(
                content_type="application/json",
                body=json.dumps(_trend_payload()),
            )
            return
        if path == "/documents/doc-preview/pages/2":
            page_requests.append(
                {
                    "query": parse_qs(parsed.query),
                    "correlation": request.headers.get("x-copilot-request-id"),
                    "accept": request.headers.get("accept"),
                }
            )
            if page_failure["enabled"]:
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps(
                        {"detail": "source document is unavailable for rendering"}
                    ),
                )
            else:
                route.fulfill(status=200, content_type="image/png", body=png)
            return
        route.abort()

    with _serve() as base, sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as exc:
            pytest.skip(
                f"Playwright chromium not installed ({exc}); run `playwright install chromium`"
            )
        try:
            page = browser.new_page()
            page.add_init_script(
                """
                window.__revokedPreviewUrls = [];
                const originalRevoke = URL.revokeObjectURL.bind(URL);
                URL.revokeObjectURL = function (value) {
                  window.__revokedPreviewUrls.push(value);
                  return originalRevoke(value);
                };
                """
            )
            page.route("**/documents**", handle_documents)
            page.goto(f"{base}/week2?sid=session-synthetic")
            page.set_input_files(
                "#file",
                {
                    "name": "synthetic.pdf",
                    "mimeType": "application/pdf",
                    "buffer": b"%PDF-1.7 synthetic",
                },
            )
            page.click("#upload")
            expect(page.locator("#documentRef")).to_contain_text("corr.preview")

            citation_button = page.get_by_role(
                "button", name="uploaded document · page 2", exact=True
            )
            expect(citation_button).to_be_visible()
            citation_button.click()

            expect(page.locator("#modal")).to_have_class("modal open")
            expect(page.locator("#pageWrap")).to_be_visible()
            expect(page.locator("#viewerError")).to_be_hidden()
            page.wait_for_function(
                "document.querySelector('#pageImage').src.startsWith('blob:')"
            )
            citation_image_src = page.locator("#pageImage").get_attribute("src")
            assert citation_image_src is not None and citation_image_src.startswith(
                "blob:"
            )
            assert page.locator("#overlaySvg").evaluate("node => node.hidden") is False
            overlay = page.locator("#overlay")
            assert overlay.get_attribute("class") == "box"
            assert overlay.get_attribute("x") == "0.1"
            assert overlay.get_attribute("y") == "0.1"
            assert float(overlay.get_attribute("width") or "nan") == pytest.approx(0.2)
            assert float(overlay.get_attribute("height") or "nan") == pytest.approx(0.1)
            assert page_requests == [
                {
                    "query": {"session_id": ["session-synthetic"]},
                    "correlation": "corr.preview",
                    "accept": "image/png",
                }
            ]

            page.click("#closeViewer")
            expect(page.locator("#modal")).not_to_have_class("modal open")
            assert page.locator("#pageImage").get_attribute("src") is None
            assert page.evaluate("window.__revokedPreviewUrls") == [citation_image_src]

            source_button = page.get_by_role("button", name="Open page 2").first
            expect(source_button).to_be_visible()
            source_button.click()

            expect(page.locator("#modal")).to_have_class("modal open")
            expect(page.locator("#pageWrap")).to_be_visible()
            expect(page.locator("#viewerError")).to_be_hidden()
            page.wait_for_function(
                "document.querySelector('#pageImage').src.startsWith('blob:')"
            )
            image_src = page.locator("#pageImage").get_attribute("src")
            assert image_src is not None and image_src.startswith("blob:")
            assert page.locator("#overlaySvg").evaluate("node => node.hidden") is False
            overlay = page.locator("#overlay")
            assert overlay.get_attribute("class") == "box"
            assert overlay.get_attribute("x") == "0.2"
            assert overlay.get_attribute("y") == "0.3"
            assert float(overlay.get_attribute("width") or "nan") == pytest.approx(0.4)
            assert float(overlay.get_attribute("height") or "nan") == pytest.approx(0.1)
            assert len(page_requests) == 2
            assert page_requests[1]["query"] == {"session_id": ["session-synthetic"]}
            assert page_requests[1]["correlation"] == "corr.preview"
            assert page_requests[1]["accept"] == "image/png"

            page.click("#closeViewer")
            expect(page.locator("#modal")).not_to_have_class("modal open")
            assert page.locator("#pageImage").get_attribute("src") is None
            assert page.evaluate("window.__revokedPreviewUrls") == [
                citation_image_src,
                image_src,
            ]

            page_failure["enabled"] = True
            source_button.click()
            expect(page.locator("#viewerError")).to_be_visible()
            expect(page.locator("#viewerError")).to_have_text(
                "source document is unavailable for rendering"
            )
            expect(page.locator("#pageWrap")).to_be_hidden()
            assert page.locator("#overlaySvg").evaluate("node => node.hidden") is True
            assert len(page_requests) == 3
            assert page_requests[2]["correlation"] == "corr.preview"

            page.click("#closeViewer")
            page_failure["enabled"] = False
            source_button.click()
            expect(page.locator("#modal")).to_have_class("modal open")
            page.click("#closeViewer")
            page.wait_for_timeout(100)
            expect(page.locator("#modal")).not_to_have_class("modal open")
            assert page.locator("#pageImage").get_attribute("src") is None
            assert page.locator("#overlaySvg").evaluate("node => node.hidden") is True
        finally:
            browser.close()
