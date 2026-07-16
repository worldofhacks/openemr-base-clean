"""Format-aware PHI scanning for compressed generated PDF and PNG artifacts."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import zlib

from PIL import Image, ImageDraw, ImageFont
import pytest

from evals.artifact_scan import ArtifactScanError, scan_paths


_CANARY = "ZZPHI-COMPRESSED-ARTIFACT"


def _pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = (
        "BT\n/F1 28 Tf\n1 0 0 1 54 700 Tm\n"
        f"(GENERATED REPORT {escaped}) Tj\nET\n"
    ).encode("ascii")
    compressed = zlib.compress(content, 9)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        (
            b"<< /Filter /FlateDecode /Length "
            + str(len(compressed)).encode("ascii")
            + b" >>\nstream\n"
            + compressed
            + b"\nendstream"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


def _png(text: str) -> bytes:
    image = Image.new("RGB", (1800, 320), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=72)
    draw.text((35, 95), text, fill="black", font=font)
    output = BytesIO()
    image.save(output, format="PNG", compress_level=9)
    return output.getvalue()


@pytest.mark.parametrize(("suffix", "factory"), [(".pdf", _pdf), (".png", _png)])
def test_scanner_detects_visible_canary_hidden_by_media_compression(
    tmp_path: Path, suffix: str, factory
) -> None:
    raw = factory(_CANARY)
    assert _CANARY.encode("ascii") not in raw
    artifact = tmp_path / ("generated" + suffix)
    artifact.write_bytes(raw)

    assert scan_paths([artifact]) == (False, 1, 1)


def test_scanner_rejects_unparseable_declared_media(tmp_path: Path) -> None:
    artifact = tmp_path / "generated.png"
    artifact.write_bytes(b"not-a-png")

    with pytest.raises(ArtifactScanError, match="signature"):
        scan_paths([artifact])
