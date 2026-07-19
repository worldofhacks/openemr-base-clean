"""PHI/secret scanner for generated eval artifacts.

Canonical fixtures and the golden manifest are explicitly excluded inputs. Findings never
echo the matched signature or surrounding content. Generic artifact scanning is deliberately
literal. The narrower numeric-collision exemption is available only through the explicit
``--eval-result`` boundary, after a result has passed a closed structural validation.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Sequence
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, TypeGuard

import pdfplumber
import pytesseract
from PIL import Image, ImageOps

from app.ingestion.reader import read_pdf_bytes_words_and_boxes
from app.ingestion.uploads import validate_upload
from evals.canary import scan_generated_surfaces
from evals.golden_loader import DEFAULT_MANIFEST, load_golden_cases
from evals.w2_models import (
    CaseObservation,
    GeneratedSurfaces,
    GoldenCase,
    Rubric,
    RunStatus,
)


_SECRET_PATTERNS = (
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"\b(?:ANTHROPIC_API_KEY|COHERE_API_KEY|RAILWAY_TOKEN)\s*[:=]\s*[^\s\"']+",
        re.I,
    ),
    re.compile(r"\bAuthorization\s*:\s*Bearer\s+[^\s\"']+", re.I),
)
_CANONICAL_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "golden"
_DEFAULT_RECORDINGS = Path(__file__).parent / "recordings" / "index.json"
_MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
_MAX_EXTRACTED_CHARS = 2_000_000
_OCR_TIMEOUT_SECONDS = 5.0
_PDF_MAGIC = b"%PDF-"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"

_OPERATIONAL_NUMBER = "<validated-operational-number>"
_CANONICAL_CASE_COUNT = 50
_MAX_LIVE_SUBSET_CASES = 20
_MAX_LIMIT_COST_USD = 1_000_000.0
_MAX_LIMIT_SECONDS = 86_400.0
_MAX_ELAPSED_SECONDS = 86_400.0
_MAX_LATENCY_MS = 86_400_000.0
_MAX_TOKEN_COUNT = 1_000_000_000
_MAX_COST_USD = 1_000_000.0
_MAX_RETRIES = 10_000

_RESULT_TOP_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "tier",
        "source_sha",
        "manifest_sha256",
        "recordings_sha256",
        "retrieval",
        "case_count",
        "executor_call_count",
        "inconclusive_reason",
        "limits",
        "categories",
        "cases",
        "metrics",
    }
)
_RETRIEVAL_KEYS = frozenset(
    {
        "corpus_version",
        "corpus_manifest_sha256",
        "embedder",
        "reranker",
        "retrieval_recordings_sha256",
    }
)
_RUNNER_ERROR_KEYS = frozenset(
    {"schema_version", "status", "tier", "source_sha", "error_type"}
)
_LIMIT_KEYS = frozenset({"max_cost_usd", "max_seconds"})
_CATEGORY_KEYS = frozenset(
    {
        "rubric",
        "numerator",
        "denominator",
        "inconclusive",
        "current_score",
        "baseline_score",
        "percentage_point_delta",
        "threshold",
        "passed",
        "trigger",
    }
)
_CATEGORY_NUMBER_KEYS = frozenset(
    {
        "numerator",
        "denominator",
        "inconclusive",
        "current_score",
        "baseline_score",
        "percentage_point_delta",
        "threshold",
    }
)
_CASE_KEYS = frozenset({"case_id", "status", "rubrics"})
_METRIC_KEYS = frozenset(
    {
        "elapsed_seconds",
        "p50_ms",
        "p95_ms",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "retries",
        "retrieval_hit_count",
        "extraction_grounding_rate",
    }
)
_RUBRIC_ORDER = tuple(rubric.value for rubric in Rubric)
_RUBRIC_SET = frozenset(_RUBRIC_ORDER)
_UNIVERSAL_RUBRICS = frozenset(
    {
        Rubric.SCHEMA_VALID.value,
        Rubric.CITATION_PRESENT.value,
        Rubric.NO_PHI_IN_LOGS.value,
    }
)
_THRESHOLDS = {
    Rubric.SCHEMA_VALID.value: 1.0,
    Rubric.CITATION_PRESENT.value: 1.0,
    Rubric.FACTUALLY_CONSISTENT.value: 0.9,
    Rubric.SAFE_REFUSAL.value: 1.0,
    Rubric.NO_PHI_IN_LOGS.value: 1.0,
}
_RUN_STATUSES = frozenset(status.value for status in RunStatus)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SOURCE_SHA = re.compile(r"[0-9a-f]{40}")
_ERROR_TYPE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")


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
            raise ArtifactScanError(
                "requested scan path is not a regular file or directory"
            )
        for candidate in sorted(requested.rglob("*")):
            if candidate.is_symlink():
                raise ArtifactScanError(
                    "symbolic links are not valid generated artifacts"
                )
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
        parts = [" ".join(word.text for word in page.words) for page in words.pages]
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
        raise ArtifactScanError(
            "generated PDF could not be scanned completely"
        ) from None


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
        raise ArtifactScanError(
            "generated image could not be scanned completely"
        ) from None


def _scan_text(path: Path, raw: bytes) -> str:
    """Extract generic artifact text without any numeric exemption."""

    suffix = path.suffix.casefold()
    declared_format = suffix in {".pdf", ".png", ".jpg", ".jpeg"}
    if raw.startswith(_PDF_MAGIC):
        return _pdf_text(raw)
    if raw.startswith(_PNG_MAGIC):
        return _image_text(raw, content_type="image/png")
    if raw.startswith(_JPEG_MAGIC):
        return _image_text(raw, content_type="image/jpeg")
    if declared_format:
        raise ArtifactScanError(
            "generated media signature does not match its extension"
        )
    return raw.decode("utf-8", "ignore")


def _text_contains_leak(text: str, cases: Sequence[GoldenCase]) -> bool:
    if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
        return True
    for case in cases:
        probe = CaseObservation(
            case_id=case.case_id,
            fields={},
            citations=[],
            verdict="artifact_scan",
            generated=GeneratedSurfaces(reports=[text]),
        )
        if not scan_generated_surfaces(case, probe).clean:
            return True
    return False


def scan_paths(paths: Sequence[Path]) -> tuple[bool, int, int]:
    """Scan arbitrary generated artifacts literally, including every numeric token."""

    cases = load_golden_cases()
    scanned = 0
    failing_files = 0
    for path in _files(paths):
        if _canonical_input(path):
            continue
        scanned += 1
        if path.stat().st_size > _MAX_ARTIFACT_BYTES:
            raise ArtifactScanError("generated artifact exceeds scanner byte bound")
        text = _scan_text(path, path.read_bytes())
        if _text_contains_leak(text, cases):
            failing_files += 1
    return failing_files == 0, scanned, failing_files


def _schema_error() -> ArtifactScanError:
    return ArtifactScanError("eval result failed closed-schema validation")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ArtifactScanError("eval result contains a duplicate JSON key")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> None:
    raise ArtifactScanError("eval result contains a non-finite JSON constant")


def _load_strict_json(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ArtifactScanError("eval result is not strict UTF-8") from None
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except ArtifactScanError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ArtifactScanError("eval result is not strict JSON") from None
    if not isinstance(value, dict):
        raise _schema_error()
    return value


def _exact_keys(value: object, expected: frozenset[str]) -> bool:
    return isinstance(value, dict) and set(value) == expected


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _bounded_number(value: object, *, low: float, high: float) -> bool:
    return _is_number(value) and low <= float(value) <= high


def _bounded_optional_number(value: object, *, low: float, high: float) -> bool:
    return value is None or _bounded_number(value, low=low, high=high)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _applicable_rubrics(case: GoldenCase) -> frozenset[str]:
    return _UNIVERSAL_RUBRICS | {case.maps_to.value}


def _validate_retrieval(value: object) -> None:
    """The result must pin the exact committed retrieval corpus/model configuration."""

    from evals.retrieval_adapters import retrieval_provenance

    if not _exact_keys(value, _RETRIEVAL_KEYS):
        raise _schema_error()
    if value != retrieval_provenance():
        raise _schema_error()


def _validate_limits(tier: str, value: object) -> None:
    if tier == "recorded":
        if value is not None:
            raise _schema_error()
        return
    if not _exact_keys(value, _LIMIT_KEYS):
        raise _schema_error()
    assert isinstance(value, dict)
    if not _bounded_number(
        value["max_cost_usd"], low=0.000001, high=_MAX_LIMIT_COST_USD
    ) or not _bounded_number(
        value["max_seconds"], low=0.000001, high=_MAX_LIMIT_SECONDS
    ):
        raise _schema_error()


def _validate_case_rows(
    *,
    tier: str,
    status: str,
    case_count: int,
    raw_rows: object,
    canonical_cases: Sequence[GoldenCase],
) -> list[dict[str, Any]]:
    if not isinstance(raw_rows, list):
        raise _schema_error()
    if tier == "recorded":
        if raw_rows:
            raise _schema_error()
        return []
    if not raw_rows:
        if status != RunStatus.INCONCLUSIVE.value:
            raise _schema_error()
        return []
    if len(raw_rows) != case_count:
        raise _schema_error()

    by_id = {case.case_id: case for case in canonical_cases}
    canonical_order = {
        case.case_id: index for index, case in enumerate(canonical_cases)
    }
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_rows:
        if not _exact_keys(raw, _CASE_KEYS):
            raise _schema_error()
        assert isinstance(raw, dict)
        case_id = raw["case_id"]
        row_status = raw["status"]
        rubrics = raw["rubrics"]
        if (
            not isinstance(case_id, str)
            or case_id not in by_id
            or case_id in seen
            or row_status not in _RUN_STATUSES
            or not isinstance(rubrics, dict)
            or set(rubrics) != _applicable_rubrics(by_id[case_id])
            or any(
                value is not None and not isinstance(value, bool)
                for value in rubrics.values()
            )
        ):
            raise _schema_error()
        seen.add(case_id)

        values = list(rubrics.values())
        expected_status = (
            RunStatus.FAIL.value
            if any(value is False for value in values)
            else RunStatus.INCONCLUSIVE.value
            if any(value is None for value in values)
            else RunStatus.PASS.value
        )
        if row_status != expected_status:
            raise _schema_error()
        rows.append(raw)

    case_ids = [row["case_id"] for row in rows]
    if tier == "live":
        if case_ids != [case.case_id for case in canonical_cases]:
            raise _schema_error()
    elif case_ids != sorted(case_ids, key=canonical_order.__getitem__):
        raise _schema_error()
    return rows


def _validate_categories(
    *,
    tier: str,
    status: str,
    raw_categories: object,
    rows: Sequence[dict[str, Any]],
    canonical_cases: Sequence[GoldenCase],
) -> list[dict[str, Any]]:
    if not isinstance(raw_categories, list):
        raise _schema_error()
    if not raw_categories:
        if status != RunStatus.INCONCLUSIVE.value or rows:
            raise _schema_error()
        return []
    if tier in {"live", "live_subset"} and not rows:
        raise _schema_error()

    by_id = {case.case_id: case for case in canonical_cases}
    selected_cases = (
        list(canonical_cases)
        if tier == "recorded"
        else [by_id[row["case_id"]] for row in rows]
    )
    expected_rubrics = [
        rubric
        for rubric in _RUBRIC_ORDER
        if any(rubric in _applicable_rubrics(case) for case in selected_cases)
    ]
    if len(raw_categories) != len(expected_rubrics):
        raise _schema_error()

    categories: list[dict[str, Any]] = []
    for raw, expected_rubric in zip(raw_categories, expected_rubrics, strict=True):
        if not _exact_keys(raw, _CATEGORY_KEYS):
            raise _schema_error()
        assert isinstance(raw, dict)
        if raw["rubric"] != expected_rubric or raw["rubric"] not in _RUBRIC_SET:
            raise _schema_error()
        if not all(
            _is_int(raw[key]) for key in ("numerator", "denominator", "inconclusive")
        ):
            raise _schema_error()
        numerator = raw["numerator"]
        denominator = raw["denominator"]
        inconclusive = raw["inconclusive"]
        expected_count = sum(
            expected_rubric in _applicable_rubrics(case) for case in selected_cases
        )
        if (
            numerator < 0
            or denominator < 0
            or inconclusive < 0
            or numerator > denominator
            or denominator + inconclusive != expected_count
            or (tier == "recorded" and inconclusive != 0)
        ):
            raise _schema_error()

        if rows:
            values = [
                row["rubrics"][expected_rubric]
                for row in rows
                if expected_rubric in row["rubrics"]
            ]
            if (
                numerator != sum(value is True for value in values)
                or denominator != sum(value is not None for value in values)
                or inconclusive != sum(value is None for value in values)
            ):
                raise _schema_error()

        current_score = raw["current_score"]
        expected_score = numerator / denominator if denominator else 0.0
        if not _bounded_number(current_score, low=0.0, high=1.0) or not math.isclose(
            float(current_score), expected_score, rel_tol=0.0, abs_tol=1e-12
        ):
            raise _schema_error()

        baseline_score = raw["baseline_score"]
        delta = raw["percentage_point_delta"]
        if tier == "live_subset" and baseline_score is not None:
            raise _schema_error()
        # The recorded (PR) tier must load the committed baseline so the ">5
        # percentage-point regression" rule binds at PR time (R02 point 7).
        if tier == "recorded" and baseline_score is None:
            raise _schema_error()
        if baseline_score is None:
            if delta is not None:
                raise _schema_error()
        elif (
            not _bounded_number(baseline_score, low=0.0, high=1.0)
            or not _bounded_number(delta, low=-100.0, high=100.0)
            or not math.isclose(
                float(delta),
                (float(current_score) - float(baseline_score)) * 100.0,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ):
            raise _schema_error()

        threshold = raw["threshold"]
        if (
            not _is_number(threshold)
            or float(threshold) != _THRESHOLDS[expected_rubric]
        ):
            raise _schema_error()
        if not isinstance(raw["passed"], bool):
            raise _schema_error()
        if not isinstance(raw["trigger"], str) or not raw["trigger"].strip():
            raise _schema_error()

        if expected_rubric == Rubric.FACTUALLY_CONSISTENT.value:
            threshold_ok = expected_score >= 0.9
            delta_ok = delta is None or -float(delta) <= 5.0 + 1e-12
            expected_passed = (
                denominator > 0 and inconclusive == 0 and threshold_ok and delta_ok
            )
        else:
            expected_passed = (
                denominator > 0 and inconclusive == 0 and numerator == denominator
            )
        if raw["passed"] is not expected_passed:
            raise _schema_error()
        categories.append(raw)
    return categories


def _validate_metrics(value: object, *, case_count: int) -> None:
    if not _exact_keys(value, _METRIC_KEYS):
        raise _schema_error()
    assert isinstance(value, dict)
    if not _bounded_number(
        value["elapsed_seconds"], low=0.0, high=_MAX_ELAPSED_SECONDS
    ):
        raise _schema_error()
    if not _bounded_optional_number(value["p50_ms"], low=0.0, high=_MAX_LATENCY_MS):
        raise _schema_error()
    if not _bounded_optional_number(value["p95_ms"], low=0.0, high=_MAX_LATENCY_MS):
        raise _schema_error()
    if (
        value["p50_ms"] is not None
        and value["p95_ms"] is not None
        and float(value["p50_ms"]) > float(value["p95_ms"])
    ):
        raise _schema_error()
    for key in ("input_tokens", "output_tokens"):
        if not _is_int(value[key]) or not 0 <= value[key] <= _MAX_TOKEN_COUNT:
            raise _schema_error()
    if not _bounded_number(value["cost_usd"], low=0.0, high=_MAX_COST_USD):
        raise _schema_error()
    if not _is_int(value["retries"]) or not 0 <= value["retries"] <= _MAX_RETRIES:
        raise _schema_error()
    if (
        not _is_int(value["retrieval_hit_count"])
        or not 0 <= value["retrieval_hit_count"] <= case_count * 5
    ):
        raise _schema_error()
    if not _bounded_optional_number(
        value["extraction_grounding_rate"], low=0.0, high=1.0
    ):
        raise _schema_error()


def _expected_run_status(
    rows: Sequence[dict[str, Any]], categories: Sequence[dict[str, Any]]
) -> str:
    if any(row["status"] == RunStatus.FAIL.value for row in rows) or any(
        not category["passed"] and category["inconclusive"] == 0
        for category in categories
    ):
        return RunStatus.FAIL.value
    if any(row["status"] == RunStatus.INCONCLUSIVE.value for row in rows) or any(
        category["inconclusive"] > 0 for category in categories
    ):
        return RunStatus.INCONCLUSIVE.value
    if categories and all(category["passed"] for category in categories):
        return RunStatus.PASS.value
    return RunStatus.INCONCLUSIVE.value


def _validate_runner_error(value: dict[str, Any]) -> dict[str, Any]:
    if not _exact_keys(value, _RUNNER_ERROR_KEYS):
        raise _schema_error()
    if not _is_int(value["schema_version"]) or value["schema_version"] != 1:
        raise _schema_error()
    if value["status"] != RunStatus.FAIL.value:
        raise _schema_error()
    if value["tier"] not in {"recorded", "live", "live_subset"}:
        raise _schema_error()
    if not isinstance(value["source_sha"], str) or not (
        value["source_sha"] == "local-uncommitted"
        or _SOURCE_SHA.fullmatch(value["source_sha"])
    ):
        raise _schema_error()
    if (
        not isinstance(value["error_type"], str)
        or _ERROR_TYPE.fullmatch(value["error_type"]) is None
    ):
        raise _schema_error()
    return value


def _validate_eval_result(value: dict[str, Any]) -> dict[str, Any]:
    if _exact_keys(value, _RUNNER_ERROR_KEYS):
        return _validate_runner_error(value)
    if not _exact_keys(value, _RESULT_TOP_KEYS):
        raise _schema_error()
    if not _is_int(value["schema_version"]) or value["schema_version"] != 1:
        raise _schema_error()
    tier = value["tier"]
    status = value["status"]
    if tier not in {"recorded", "live", "live_subset"} or status not in _RUN_STATUSES:
        raise _schema_error()
    if not isinstance(value["source_sha"], str) or not (
        value["source_sha"] == "local-uncommitted"
        or _SOURCE_SHA.fullmatch(value["source_sha"])
    ):
        raise _schema_error()

    canonical_cases = load_golden_cases()
    if len(canonical_cases) != _CANONICAL_CASE_COUNT:
        raise _schema_error()
    if not isinstance(value["manifest_sha256"], str) or value[
        "manifest_sha256"
    ] != _sha256(DEFAULT_MANIFEST):
        raise _schema_error()

    case_count = value["case_count"]
    executor_call_count = value["executor_call_count"]
    if not _is_int(case_count) or not _is_int(executor_call_count):
        raise _schema_error()
    if tier in {"recorded", "live"}:
        if case_count != _CANONICAL_CASE_COUNT:
            raise _schema_error()
    elif not 1 <= case_count <= _MAX_LIVE_SUBSET_CASES:
        raise _schema_error()
    if not 0 <= executor_call_count <= case_count:
        raise _schema_error()

    recordings_sha = value["recordings_sha256"]
    if tier == "recorded":
        if (
            not isinstance(recordings_sha, str)
            or not _SHA256.fullmatch(recordings_sha)
            or recordings_sha != _sha256(_DEFAULT_RECORDINGS)
        ):
            raise _schema_error()
    elif recordings_sha is not None:
        raise _schema_error()

    _validate_retrieval(value["retrieval"])
    _validate_limits(tier, value["limits"])
    rows = _validate_case_rows(
        tier=tier,
        status=status,
        case_count=case_count,
        raw_rows=value["cases"],
        canonical_cases=canonical_cases,
    )
    if rows and executor_call_count != case_count:
        raise _schema_error()
    if status != RunStatus.INCONCLUSIVE.value and executor_call_count != case_count:
        raise _schema_error()
    categories = _validate_categories(
        tier=tier,
        status=status,
        raw_categories=value["categories"],
        rows=rows,
        canonical_cases=canonical_cases,
    )
    if status != _expected_run_status(rows, categories):
        raise _schema_error()

    reason = value["inconclusive_reason"]
    if status == RunStatus.INCONCLUSIVE.value:
        if not isinstance(reason, str) or not reason.strip():
            raise _schema_error()
    elif reason is not None:
        raise _schema_error()

    _validate_metrics(value["metrics"], case_count=case_count)
    return value


def _sanitize_validated_numbers(value: dict[str, Any]) -> str:
    """Replace only numeric primitives whose closed operational schema was validated."""

    sanitized = deepcopy(value)

    def replace(mapping: dict[str, Any], key: str) -> None:
        if _is_number(mapping[key]):
            mapping[key] = _OPERATIONAL_NUMBER

    replace(sanitized, "schema_version")
    if _exact_keys(sanitized, _RUNNER_ERROR_KEYS):
        return json.dumps(
            sanitized,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )

    for key in ("case_count", "executor_call_count"):
        replace(sanitized, key)
    limits = sanitized["limits"]
    if isinstance(limits, dict):
        for key in _LIMIT_KEYS:
            replace(limits, key)
    for category in sanitized["categories"]:
        for key in _CATEGORY_NUMBER_KEYS:
            replace(category, key)
    for key in _METRIC_KEYS:
        replace(sanitized["metrics"], key)
    return json.dumps(
        sanitized,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def scan_eval_result_paths(paths: Sequence[Path]) -> tuple[bool, int, int]:
    """Scan closed eval-result JSON while exempting validated operational numbers only."""

    cases = load_golden_cases()
    scanned = 0
    failing_files = 0
    for path in paths:
        if not path.exists():
            raise ArtifactScanError("requested eval-result path is missing")
        if path.is_symlink() or not path.is_file():
            raise ArtifactScanError("eval-result scan roots must be regular files")
        if path.stat().st_size > _MAX_ARTIFACT_BYTES:
            raise ArtifactScanError("generated artifact exceeds scanner byte bound")
        value = _validate_eval_result(_load_strict_json(path.read_bytes()))
        text = _sanitize_validated_numbers(value)
        scanned += 1
        if _text_contains_leak(text, cases):
            failing_files += 1
    return failing_files == 0, scanned, failing_files


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.artifact_scan")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument(
        "--eval-result",
        action="append",
        default=[],
        type=Path,
        help="scan one closed eval-result JSON file with numeric collision handling",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    eval_result_paths = args.eval_result
    if not args.paths and not eval_result_paths:
        _parser().error("at least one path or --eval-result path is required")
    try:
        clean = True
        scanned = 0
        failures = 0
        if args.paths:
            generic_clean, generic_scanned, generic_failures = scan_paths(args.paths)
            clean = clean and generic_clean
            scanned += generic_scanned
            failures += generic_failures
        if eval_result_paths:
            result_clean, result_scanned, result_failures = scan_eval_result_paths(
                eval_result_paths
            )
            clean = clean and result_clean
            scanned += result_scanned
            failures += result_failures
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
