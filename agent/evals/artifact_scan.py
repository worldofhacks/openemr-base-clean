"""PHI/secret scanner for generated eval artifacts.

Canonical fixtures and the golden manifest are explicitly excluded inputs. Findings never
echo the matched signature or surrounding content.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Sequence
from io import BytesIO
import re
import sys
from pathlib import Path

import pdfplumber
import pytesseract
from PIL import Image, ImageOps

from app.ingestion.reader import read_pdf_bytes_words_and_boxes
from app.ingestion.uploads import validate_upload
from evals.canary import scan_generated_surfaces
from evals.golden_loader import DEFAULT_MANIFEST, load_golden_cases
from evals.w2_models import CaseObservation, GeneratedSurfaces


_SECRET_PATTERNS = (
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:ANTHROPIC_API_KEY|COHERE_API_KEY|RAILWAY_TOKEN)\s*[:=]\s*[^\s\"']+", re.I),
    re.compile(r"\bAuthorization\s*:\s*Bearer\s+[^\s\"']+", re.I),
)
_CANONICAL_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "golden"
_MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
_MAX_EXTRACTED_CHARS = 2_000_000
_OCR_TIMEOUT_SECONDS = 5.0
_PDF_MAGIC = b"%PDF-"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


class ArtifactScanError(RuntimeError):
    """A requested generated surface could not be scanned completely."""


def _canonical_input(path: Path) -> bool:
    resolved = path.resolve()
    if resolved == DEFAULT_MANIFEST.resolve():
        return True
    return resolved.is_relative_to(_CANONICAL_FIXTURE_ROOT.resolve())


def _files(paths: Sequence[Path]) -> Iterator[Path]:
    for requested in paths:
        if not requested.exists():
            raise ArtifactScanError("requested scan path is missing")
        if requested.is_symlink():
            raise ArtifactScanError("symbolic links are not valid scan roots")
        if requested.is_file():
            yield requested
            continue
        if not requested.is_dir():
            raise ArtifactScanError("requested scan path is not a regular file or directory")
        for candidate in sorted(requested.rglob("*")):
            if candidate.is_symlink():
                raise ArtifactScanError("symbolic links are not valid generated artifacts")
            if candidate.is_file():
                yield candidate


def _bounded_text(parts: Sequence[str]) -> str:
    text = "\n".join(parts)
    if len(text) > _MAX_EXTRACTED_CHARS:
        raise ArtifactScanError("generated artifact text exceeds scanner bounds")
    return text


def _pdf_text(raw: bytes) -> str:
    try:
        validate_upload(
            filename="generated.pdf",
            content_type="application/pdf",
            data=raw,
            doc_type="intake_form",
        )
        words = read_pdf_bytes_words_and_boxes(
            raw, per_page_ocr_timeout_s=_OCR_TIMEOUT_SECONDS
        )
        if any(page.unreadable for page in words.pages):
            raise ArtifactScanError("generated PDF OCR was incomplete")
        parts = [
            " ".join(word.text for word in page.words)
            for page in words.pages
        ]
        with pdfplumber.open(BytesIO(raw)) as document:
            parts.extend(
                value
                for value in (document.metadata or {}).values()
                if isinstance(value, str)
            )
        return _bounded_text(parts)
    except ArtifactScanError:
        raise
    except Exception:  # noqa: BLE001 - parser/OCR details must not cross the scanner
        raise ArtifactScanError("generated PDF could not be scanned completely") from None


def _image_text(raw: bytes, *, content_type: str) -> str:
    filename = "generated.png" if content_type == "image/png" else "generated.jpg"
    try:
        validate_upload(
            filename=filename,
            content_type=content_type,
            data=raw,
            doc_type="intake_form",
        )
        with Image.open(BytesIO(raw)) as source:
            metadata = [
                value for value in source.info.values() if isinstance(value, str)
            ]
            image = ImageOps.exif_transpose(source).convert("RGB")
        visible = pytesseract.image_to_string(
            image,
            timeout=_OCR_TIMEOUT_SECONDS,
        )
        return _bounded_text([*metadata, visible])
    except Exception:  # noqa: BLE001 - decoder/OCR details must not cross the scanner
        raise ArtifactScanError("generated image could not be scanned completely") from None


def _scan_text(path: Path, raw: bytes) -> str:
    suffix = path.suffix.casefold()
    declared_format = suffix in {".pdf", ".png", ".jpg", ".jpeg"}
    if raw.startswith(_PDF_MAGIC):
        return _pdf_text(raw)
    if raw.startswith(_PNG_MAGIC):
        return _image_text(raw, content_type="image/png")
    if raw.startswith(_JPEG_MAGIC):
        return _image_text(raw, content_type="image/jpeg")
    if declared_format:
        raise ArtifactScanError("generated media signature does not match its extension")
    return raw.decode("utf-8", "ignore")


def scan_paths(paths: Sequence[Path]) -> tuple[bool, int, int]:
    cases = load_golden_cases()
    scanned = 0
    failing_files = 0
    for path in _files(paths):
        if _canonical_input(path):
            continue
        scanned += 1
        if path.stat().st_size > _MAX_ARTIFACT_BYTES:
            raise ArtifactScanError("generated artifact exceeds scanner byte bound")
        raw = path.read_bytes()
        text = _scan_text(path, raw)
        leaked = any(pattern.search(text) for pattern in _SECRET_PATTERNS)
        if not leaked:
            for case in cases:
                probe = CaseObservation(
                    case_id=case.case_id,
                    fields={},
                    citations=[],
                    verdict="artifact_scan",
                    generated=GeneratedSurfaces(reports=[text]),
                )
                if not scan_generated_surfaces(case, probe).clean:
                    leaked = True
                    break
        if leaked:
            failing_files += 1
    return failing_files == 0, scanned, failing_files


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.artifact_scan")
    parser.add_argument("paths", nargs="+", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        clean, scanned, failures = scan_paths(args.paths)
    except (ArtifactScanError, OSError):
        print(
            "artifact-scan=FAIL scanned=0 failing_files=0 scanner_error=unscannable_path",
            file=sys.stderr,
        )
        return 2
    status = "PASS" if clean else "FAIL"
    stream = sys.stdout if clean else sys.stderr
    print(
        f"artifact-scan={status} scanned={scanned} failing_files={failures}",
        file=stream,
    )
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
