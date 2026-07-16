"""Week 1 renders only CitationV2 objects through safe DOM APIs."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_week1_renderer_uses_citation_v2_without_legacy_string_parsing() -> None:
    from app.routes.ui import router

    app = FastAPI()
    app.include_router(router)
    page = TestClient(app).get("/app?sid=synthetic-session")

    assert page.status_code == 200
    assert "validCitation(citation)" in page.text
    assert "citation.source_type" in page.text
    assert "citation.source_id" in page.text
    assert "citation.page_or_section" in page.text
    assert "citation.field_or_chunk_id" in page.text
    assert "citation.quote_or_value" in page.text
    assert "String(cid)" not in page.text
    assert "String(c).split" not in page.text
    assert "innerHTML" not in page.text
    assert "replaceChildren()" in page.text
    assert "textContent" in page.text
