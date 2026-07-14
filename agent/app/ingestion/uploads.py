"""Fail-closed upload validation before queueing or native OpenEMR calls (§2a)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath
from typing import Literal

import pypdfium2 as pdfium  # type: ignore[import-untyped]
from PIL import Image

from app.schemas.documents import FailureReason

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 20

_MAGIC = {
    "application/pdf": b"%PDF-",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
}
_ALLOWED = {
    "lab_pdf": frozenset({"application/pdf"}),
    "intake_form": frozenset({"application/pdf", "image/png", "image/jpeg"}),
}


class UploadValidationError(ValueError):
    def __init__(self, reason: FailureReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    content_type: str
    data: bytes
    doc_type: Literal["lab_pdf", "intake_form"]
    content_hash: str
    page_count: int


def _reject(reason: FailureReason, message: str) -> None:
    raise UploadValidationError(reason, message)


def _safe_filename(filename: str) -> bool:
    if not filename or len(filename) > 255 or filename in {".", ".."}:
        return False
    if PurePath(filename).name != filename or "/" in filename or "\\" in filename:
        return False
    return not any(ord(character) < 32 or ord(character) == 127 for character in filename)


def _pdf_page_count(data: bytes) -> int:
    try:
        document = pdfium.PdfDocument(data)
        try:
            return len(document)
        finally:
            document.close()
    except Exception as exc:  # noqa: BLE001 - parser failure is a controlled 4xx
        raise UploadValidationError(
            FailureReason.UPLOAD_REJECTED, "PDF could not be parsed"
        ) from exc


def _validate_image(data: bytes) -> None:
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
    except Exception as exc:  # noqa: BLE001 - controlled boundary rejection
        raise UploadValidationError(
            FailureReason.UPLOAD_REJECTED, "image could not be decoded"
        ) from exc


def validate_upload(
    *,
    filename: str,
    content_type: str,
    data: bytes,
    doc_type: str,
    claimed_content_hash: str | None = None,
) -> ValidatedUpload:
    """Validate bytes and metadata before any DB or remote side effect."""

    if not _safe_filename(filename):
        _reject(FailureReason.UPLOAD_REJECTED, "unsafe or missing filename")
    if not data:
        _reject(FailureReason.UPLOAD_REJECTED, "empty upload")
    if len(data) > MAX_UPLOAD_BYTES:
        _reject(FailureReason.SIZE_OR_PAGE_CAP_EXCEEDED, "upload exceeds 10 MB")
    if doc_type not in _ALLOWED:
        _reject(FailureReason.UNSUPPORTED_MEDIA_TYPE, "unsupported document type")
    if content_type not in _ALLOWED[doc_type]:
        _reject(
            FailureReason.UNSUPPORTED_MEDIA_TYPE,
            "media type is not permitted for document type",
        )
    magic = _MAGIC[content_type]
    if not data.startswith(magic):
        _reject(
            FailureReason.UNSUPPORTED_MEDIA_TYPE,
            "declared media type does not match file signature",
        )

    if content_type == "application/pdf":
        page_count = _pdf_page_count(data)
    else:
        _validate_image(data)
        page_count = 1
    if page_count > MAX_PDF_PAGES:
        _reject(
            FailureReason.SIZE_OR_PAGE_CAP_EXCEEDED,
            "document exceeds 20 pages",
        )
    digest = hashlib.sha256(data).hexdigest()
    if claimed_content_hash is not None and claimed_content_hash != digest:
        _reject(FailureReason.UPLOAD_REJECTED, "content hash does not match upload")
    return ValidatedUpload(
        filename=filename,
        content_type=content_type,
        data=data,
        doc_type=doc_type,  # type: ignore[arg-type]
        content_hash=digest,
        page_count=page_count,
    )
