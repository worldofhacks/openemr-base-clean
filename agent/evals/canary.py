"""Generated-artifact PHI canary scanner (W2-D7, §7/§7a).

Canonical fixtures and their manifest are never opened by this module.  Signatures
come from the typed case contract, while scan targets are executor-produced output
and generated telemetry/artifacts only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
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


def _case_signatures(case: GoldenCase) -> set[str]:
    """Build safe-to-hold signatures without reading the canonical fixture.

    The unique canary is always included.  Long expected leaf phrases act as the
    fixture n-gram inventory available in the manifest; short numbers/units are
    omitted to avoid matching ordinary telemetry counters.
    """

    signatures = {f"ZZPHI-{case.case_id}".casefold()}
    for value in _strings(case.expected_fields):
        normalized = " ".join(value.split()).casefold()
        words = normalized.split()
        if len(normalized) >= 12 and (len(words) >= 3 or "@" in normalized):
            signatures.add(normalized)
            if len(words) >= 4:
                signatures.update(
                    " ".join(words[index : index + 4])
                    for index in range(len(words) - 3)
                )
    return signatures


def _contains_leak(text: str, signatures: set[str]) -> int:
    matches = len(_CANARY_PATTERN.findall(text))
    matches += len(_EMAIL_PATTERN.findall(text))
    folded = text.casefold()
    matches += sum(signature in folded for signature in signatures)
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
    signatures = _case_signatures(case)
    leak_channels: list[str] = []
    match_count = 0
    for channel in _CHANNELS:
        channel_matches = _contains_leak(_serialize(targets[channel]), signatures)
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
