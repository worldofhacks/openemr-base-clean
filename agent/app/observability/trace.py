"""Request-trace value objects + PHI minimization (ARCHITECTURE.md §7, §3.1, D5-rev, F-C.1).

`RequestTrace` is the accountability record for one request: who (client_id + hashed user),
what scopes they exercised, which patient (hashed), the correlation id / url / timestamp, the
ordered steps with per-step latency, tokens + cost, the verification verdicts, and the E5
degradation class (so fallback-rate is alertable). It is the system-of-record because
OpenEMR's api_log omits client_id and scopes (F-C.1) and cannot be reliably joined (F-C.2).

D5 PHI minimization: patient and user identifiers are stored as one-way hashes, never raw —
`client_id` and `exercised_scopes` are accountability metadata, not PHI, and are kept in the
clear so the trace can actually answer "which client, acting as whom, touched this record".
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


def hash_identifier(value: str | None) -> str:
    """One-way short hash for a PHI identifier (D5). Empty/None → "" (nothing to minimize)."""
    if not value:
        return ""
    return hashlib.sha256(value.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class AccountabilityContext:
    """The who/what/where of a request, captured at the trust boundary and carried into the
    trace. `user_id`/`patient_id` are raw here (boundary input) and hashed at trace-build time."""

    correlation_id: str
    client_id: str
    exercised_scopes: tuple[str, ...]
    request_url: str
    user_id: str
    patient_id: str
    utc_timestamp: str  # ISO-8601, stamped at the request boundary


@dataclass(frozen=True)
class TraceStep:
    order: int
    name: str            # e.g. "llm.complete", "tool.get_conditions", "verify"
    latency_ms: float
    detail: dict         # tokens/cache/status/verdict — step-specific


@dataclass(frozen=True)
class RequestTrace:
    # --- accountability (F-C.1 / D5 system-of-record) ---
    correlation_id: str
    client_id: str
    exercised_scopes: tuple[str, ...]
    request_url: str
    user_hash: str       # hashed (D5) — never the raw clinician id
    patient_hash: str    # hashed (D5) — never the raw patient id
    utc_timestamp: str
    # --- execution ---
    steps: tuple[TraceStep, ...]
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost_usd: float
    verdicts: tuple[str, ...]        # verification verdicts (per §5); empty until verify is in-loop
    # --- E5 degradation taxonomy (fallback-rate alertable) ---
    source: str                      # "llm" | "deterministic_fallback"
    degraded: bool
    fallback_kind: str | None = None
    metadata: dict = field(default_factory=dict)
