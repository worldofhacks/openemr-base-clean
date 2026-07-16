"""Generated-artifact PHI canary scanner (W2-D7, §7/§7a).

Canonical fixtures and their manifest are never opened by this module.  Signatures
come from the typed case contract, while scan targets are executor-produced output
and generated telemetry/artifacts only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from evals.w2_models import CaseObservation, GeneratedSurfaces, GoldenCase


_CANARY_PATTERN = re.compile(r"\bZZ(?:PHI|MRN)-[A-Za-z0-9_-]+\b", re.IGNORECASE)
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_CHANNELS = (
    "observation",
    "logs",
    "traces",
    "results",
    "reports",
    "recordings",
    "screenshots",
)


class LeakScanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clean: bool
    leak_channels: list[str]
    match_count: int = Field(ge=0)


def _serialize(value: Any) -> str:
    try:
        return json.dumps(value, default=str, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _strings(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _strings(nested)


def _clinical_strings(value: Any) -> Iterable[str]:
    """Walk clinical leaves while excluding approved opaque source-anchor metadata."""

    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, nested in value.items():
            if key == "source_document_id":
                continue
            yield from _clinical_strings(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _clinical_strings(nested)


_DATE_SIG = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$|^\d{1,2}/\d{1,2}/\d{2,4}$")
_NUMERIC_SIG = re.compile(r"^-?\d[\d,]*\.?\d*$")
_UTC_TIMESTAMP_SIG = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)
_OPERATIONAL_TIMESTAMP = "<operational-utc-timestamp>"


def _is_utc_timestamp(value: str) -> bool:
    """Return true only for a real, timezone-qualified instant at UTC offset zero."""

    if not _UTC_TIMESTAMP_SIG.fullmatch(value):
        return False
    try:
        normalized = (
            value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value
        )
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.utcoffset() == timedelta(0)


def _sanitize_trace_metadata(value: Any) -> Any:
    """Remove only typed operational timestamps before clinical signature matching.

    Clock minute/second components are ordinary trace metadata and can equal a short
    clinical number by coincidence.  The exemption is deliberately narrow: it applies only
    to the direct, closed ``utc_timestamp`` field on a top-level trace record and only when
    it contains a real UTC instant.  Nested values, free text, unknown keys, date-only
    values, and malformed or non-UTC timestamps remain scannable.
    """

    if not isinstance(value, list):
        return value
    sanitized: list[Any] = []
    for trace in value:
        if not isinstance(trace, dict):
            sanitized.append(trace)
            continue
        record = dict(trace)
        timestamp = record.get("utc_timestamp")
        if isinstance(timestamp, str) and _is_utc_timestamp(timestamp):
            record["utc_timestamp"] = _OPERATIONAL_TIMESTAMP
        sanitized.append(record)
    return sanitized


def _is_short_phi_value(value: str) -> bool:
    """A short expected-field value distinctive enough to be PHI, not a stray counter.

    Final-review finding (HIGH, W2-D7): the >=12-char phrase rule missed the short-PHI
    classes the invariant names — DOB / collection dates, multi-digit clinical values and
    MRNs, and name/contact identifiers ("John Smith" is 10 chars). Those are caught here.
    Single characters (e.g. a sex code "X"/"M") and bare units are deliberately skipped —
    signature-matching them would fire on ordinary telemetry and make the 100% gate flaky.
    """

    if _DATE_SIG.match(value):
        return True
    if _NUMERIC_SIG.match(value):
        digits = value.replace(",", "").replace(".", "").lstrip("-")
        return len(digits) >= 2  # multi-digit value or MRN; single digit is too noisy
    return (
        len(value) >= 5
        and any(ch.isalnum() for ch in value)
        and (" " in value or any(ch.isdigit() for ch in value))
    )


def _case_signatures(case: GoldenCase) -> tuple[set[str], set[str]]:
    """Safe-to-hold signatures without reading the canonical fixture.

    Returns ``(phrases, tokens)``. ``phrases`` (the canary + long leaf phrases) match as
    substrings; ``tokens`` (short PHI values — dates, numbers, MRNs, name/contact ids)
    match with numeric-safe boundaries so ``92`` does not fire inside ``92ms``.
    """

    phrases = {f"ZZPHI-{case.case_id}".casefold()}
    tokens: set[str] = set()
    for value in _clinical_strings(case.expected_fields):
        normalized = " ".join(value.split()).casefold()
        if not normalized:
            continue
        words = normalized.split()
        if len(normalized) >= 12 and (len(words) >= 3 or "@" in normalized):
            phrases.add(normalized)
            if len(words) >= 4:
                phrases.update(
                    " ".join(words[index : index + 4])
                    for index in range(len(words) - 3)
                )
        elif _is_short_phi_value(normalized):
            tokens.add(normalized)
    return phrases, tokens


def _contains_leak(text: str, phrases: set[str], tokens: set[str]) -> int:
    matches = len(_CANARY_PATTERN.findall(text))
    matches += len(_EMAIL_PATTERN.findall(text))
    folded = text.casefold()
    matches += sum(phrase in folded for phrase in phrases)
    for token in tokens:
        # Boundary that treats a digit/./_ /word char as "inside a token", so a leaked
        # clinical value/date/MRN is caught but a substring of a longer run is not.
        if re.search(rf"(?<![\w.]){re.escape(token)}(?![\w.])", folded):
            matches += 1
    return matches


def scan_generated_surfaces(
    case: GoldenCase, observation: CaseObservation
) -> LeakScanResult:
    """Scan generated output/artifacts while excluding canonical clinical input.

    ``observation`` means the executor's free-form generated output.  The normalized
    typed extraction fields are the authorized in-memory clinical payload and are
    intentionally not reclassified as logs; any copy into a generated artifact is
    caught in that artifact's channel.
    """

    generated = observation.generated
    targets: dict[str, Any] = {
        "observation": observation.output,
        "logs": generated.logs,
        "traces": generated.traces,
        "results": generated.results,
        "reports": generated.reports,
        "recordings": generated.recordings,
        "screenshots": generated.screenshots,
    }
    phrases, tokens = _case_signatures(case)
    leak_channels: list[str] = []
    match_count = 0
    for channel in _CHANNELS:
        target = targets[channel]
        if channel == "traces":
            target = _sanitize_trace_metadata(target)
        channel_matches = _contains_leak(_serialize(target), phrases, tokens)
        if channel_matches:
            leak_channels.append(channel)
            match_count += channel_matches
    return LeakScanResult(
        clean=not leak_channels,
        leak_channels=leak_channels,
        match_count=match_count,
    )


def known_leak_self_test(case: GoldenCase) -> bool:
    """Prove the real scanner trips for every supported generated artifact class."""

    token = f"ZZPHI-{case.case_id}"
    for channel in _CHANNELS:
        generated = GeneratedSurfaces()
        output: Any = None
        if channel == "observation":
            output = token
        else:
            generated = generated.model_copy(update={channel: [token]})
        probe = CaseObservation(
            case_id=case.case_id,
            fields={},
            citations=[],
            verdict="known_leak_probe",
            output=output,
            generated=generated,
        )
        scan = scan_generated_surfaces(case, probe)
        if scan.clean or channel not in scan.leak_channels:
            return False
    return True
