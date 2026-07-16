"""Request-trace value objects + PHI minimization (ARCHITECTURE.md §7, §3.1, D5-rev, F-C.1).

`RequestTrace` is the accountability record for one request: who (client_id + hashed user),
what scopes they exercised, which patient (hashed), the correlation id / url / timestamp, the
ordered steps with per-step latency, tokens + cost, the verification verdicts, and the E5
degradation class (so fallback-rate is alertable). It is the system-of-record because
OpenEMR's api_log omits client_id and scopes (F-C.1) and cannot be reliably joined (F-C.2).

D5 PHI minimization: patient and user accountability identifiers are stored as one-way hashes,
never raw. Trace construction stores only closed operational counts/types; prompts, transcripts,
provider payloads, claims, tool/FHIR content, tokens, and served output never enter this value
object. The Langfuse client mask is an additional fail-closed export boundary. `client_id` and
`exercised_scopes` are accountability metadata, not PHI, and stay clear so the trace can answer
which client exercised which scopes.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# A patient id can ride in a URL path segment (…/patients/<uuid>/…) or a query param
# (?mrn=…). Redact both so the trace URL is a PHI-safe route template (D5).
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_LONGNUM_RE = re.compile(r"\d{4,}")


def hash_identifier(value: str | None) -> str:
    """One-way short hash for a PHI identifier (D5). Empty/None → "" (nothing to minimize)."""
    if not value:
        return ""
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def sanitize_request_url(url: str | None) -> str:
    """Reduce a request URL to a PHI-safe route template (D5): drop the query string and
    fragment (params carry patient/mrn) and replace UUID-shaped and long-numeric path segments
    with `:id`. A patient identifier in the path or query therefore never reaches the trace."""
    if not url:
        return ""
    base = url.split("?", 1)[0].split("#", 1)[0]  # drop query + fragment
    base = _UUID_RE.sub(":id", base)
    return _LONGNUM_RE.sub(":id", base)


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
    # Retained for wire/backward compatibility only; TraceBuilder always stores None.
    served_output: str | None = None
    metadata: dict = field(default_factory=dict)
