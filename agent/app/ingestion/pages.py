"""Session-pinned ephemeral page rendering for document bbox overlays (§2a)."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from typing import Awaitable, Callable

import pypdfium2 as pdfium  # type: ignore[import-untyped]
from PIL import Image, ImageOps

from app.ingestion.reader import RENDER_DPI
from app.ingestion.repository import DocumentRecord, DocumentRepository
from app.ingestion.service import DocumentAccessError
from app.session.store import Session


class PageNotFound(LookupError):
    pass


@dataclass(frozen=True)
class RenderedPage:
    content: bytes
    media_type: str = "image/png"


SourceFetcher = Callable[[DocumentRecord], Awaitable[bytes]]


class EphemeralPageRenderer:
    """Bounded short-TTL memory cache; source/page bytes never touch disk."""

    def __init__(
        self,
        repository: DocumentRepository,
        *,
        fetch_source: SourceFetcher,
        ttl_seconds: float = 30.0,
        max_entries: int = 16,
    ) -> None:
        self._repository = repository
        self._fetch_source = fetch_source
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._cache: OrderedDict[tuple[str, int], tuple[float, RenderedPage]] = OrderedDict()

    async def page_png(
        self, session: Session, document_id: str, page_number: int
    ) -> RenderedPage:
        if page_number < 1:
            raise PageNotFound(page_number)
        record = await self._repository.get(document_id)
        if record.patient_id != session.patient_id:
            raise DocumentAccessError(document_id)
        key = (document_id, page_number)
        cached = self._cache.get(key)
        now = time.monotonic()
        if cached is not None and now - cached[0] <= self._ttl:
            self._cache.move_to_end(key)
            return cached[1]
        self._cache.pop(key, None)

        source = await self._fetch_source(record)
        rendered = RenderedPage(
            content=(
                _render_pdf(source, page_number)
                if record.content_type == "application/pdf"
                else _render_image(source, page_number)
            )
        )
        self._cache[key] = (now, rendered)
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)
        return rendered


def _render_pdf(source: bytes, page_number: int) -> bytes:
    document = pdfium.PdfDocument(source)
    try:
        if page_number > len(document):
            raise PageNotFound(page_number)
        page = document[page_number - 1]
        try:
            image = page.render(scale=RENDER_DPI / 72).to_pil().convert("RGB")
        finally:
            page.close()
    finally:
        document.close()
    return _png(image)


def _render_image(source: bytes, page_number: int) -> bytes:
    if page_number != 1:
        raise PageNotFound(page_number)
    with Image.open(BytesIO(source)) as image:
        normalized = ImageOps.exif_transpose(image).convert("RGB")
    return _png(normalized)


def _png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
