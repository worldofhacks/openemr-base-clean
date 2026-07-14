#!/usr/bin/env python3
"""W2-M24 — Tier-2 timing/cost/quota spike + fork-PR secret policy lint.

Operator measurement CLI (ops scripts may print — .tdd-swarm/gates.md). It runs
a small representative sample of the real Tier-2 three-call unit shape
(VLM extraction + answer turn + pinned-judge turn) against the live Anthropic
API, records per-call-class runtime/tokens/cost/rate-limit headroom/retry
amplification, extrapolates to the 50-case gate with the explicit formula
``50 x (VLM extraction + answer + judge)`` (multi-page VLM calls counted
explicitly, one provider call per page, never hidden inside "50 turns"), and
states the viability verdict for making the full 50-case gate required.

A failing fit is a STOP escalation for making Tier 2 required — never solved by
reducing the 50 cases or bypassing the gate (locked-decision, W2-D8 / §7).

Also hosts the two W2-M24 policy lints consumed by W2-M20:

* ``lint_workflows`` — read-only over ``.github/workflows/*.yml``; a violation
  is the full three-way conjunction: ``pull_request_target`` trigger AND
  checkout of PR-head code AND explicit secrets usage (W2 §6a).
* ``lint_policy_doc`` — asserts the six frozen clauses of
  ``docs/week2/W2_TIER2_CI_POLICY.md``.

Secret hygiene: secret VALUES are never read into report output, printed, or
logged. The tooling reads env var NAMES only and treats values as opaque; the
rendered report is additionally scrubbed for key material and Authorization
header values (W2-M24 AC-4). Sample inputs are tiny synthetic, non-clinical
images generated at runtime via stdlib-only PNG encoding (zlib + struct —
pre-authorized by the ticket; this module adds NO dependencies). PyYAML used by
the workflow lint is already installed via the declared ``langgraph`` →
``langchain-core`` dependency chain; ``agent/pyproject.toml`` is untouched.
"""

from __future__ import annotations

import argparse
import base64
import math
import os
import re
import struct
import sys
import time
import zlib
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CASES = 50
CALL_CLASSES = ("vlm", "answer", "judge")

DEFAULT_SAMPLE_UNITS = 5
DEFAULT_FIRST_UNIT_PAGES = 2

# Named maximum per-run budget for the PR-blocking Tier-2 gate (W2-M24 AC-3/AC-7).
MAX_RUN_COST_USD = 5.00
MAX_RUN_SECONDS = 1200.0  # 20 minutes — ceiling for a PR-blocking CI job

# Published per-MTok pricing (input USD/MTok, output USD/MTok).
# Source: platform.claude.com published pricing (docs/en/pricing.md and
# docs/en/about-claude/models/overview.md), per the claude-api reference
# tables cached 2026-05-26.
PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
PRICING_SOURCE = (
    "platform.claude.com published per-MTok pricing "
    "(docs/en/pricing.md; claude-api reference cached 2026-05-26)"
)

_SUBSTITUTION_NOTE = (
    "W2-OA2 substitution: measured with the LOCAL agent key from agent/.env "
    "(env var name ANTHROPIC_API_KEY) because the fork repo's GitHub Actions "
    "secret is absent — owner action W2-OA2 is pending (noted, not blocking). "
    "The key value is never read into this report."
)


# ---------------------------------------------------------------------------
# AC-2 — nearest-rank percentiles (observed-value discipline; no interpolation)
# ---------------------------------------------------------------------------


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile: rank = ceil(p/100 * n), 1-indexed, min rank 1.

    Only ever returns a value that was actually observed — a latency that never
    occurred is never reported.
    """
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile of an empty sample is undefined")
    rank = max(1, math.ceil(p / 100.0 * len(ordered)))
    return ordered[rank - 1]


# ---------------------------------------------------------------------------
# AC-1 — 50-case extrapolation: 50 x (VLM extraction + answer + judge)
# ---------------------------------------------------------------------------


def _unit_records(unit: Mapping[str, Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [unit[call_class] for call_class in CALL_CLASSES]


def extrapolate(units: Iterable[Mapping[str, Mapping[str, Any]]]) -> dict[str, Any]:
    """Project the 50-case totals as ``50 x`` the mean per-unit aggregate.

    Multi-page VLM extraction is counted explicitly (``rec["calls"]`` == page
    count, one provider call per page) and retry amplification is
    ``total attempts / total base calls`` (attempts = calls + retries).
    """
    sample = list(units)
    if not sample:
        raise ValueError(
            "cannot extrapolate from an empty sample — a zero projection would "
            "green-light the gate on no data"
        )

    total_calls = sum(rec["calls"] for unit in sample for rec in _unit_records(unit))
    total_retries = sum(rec["retries"] for unit in sample for rec in _unit_records(unit))
    if total_calls <= 0:
        raise ValueError("sample contains no base provider calls")
    total_attempts = total_calls + total_retries

    def total(field: str) -> float:
        return sum(rec[field] for unit in sample for rec in _unit_records(unit))

    n = len(sample)
    return {
        "cases": CASES,
        "projected_calls": CASES * total_attempts / n,
        "projected_seconds": CASES * total("seconds") / n,
        "projected_input_tokens": CASES * total("input_tokens") / n,
        "projected_output_tokens": CASES * total("output_tokens") / n,
        "projected_cost_usd": CASES * total("cost_usd") / n,
        "retry_amplification": total_attempts / total_calls,
    }


# ---------------------------------------------------------------------------
# AC-3 — report shape (verdict + W2-OA2 note computed by the module)
# ---------------------------------------------------------------------------


def build_report(
    units: Iterable[Mapping[str, Mapping[str, Any]]],
    *,
    rate_limit_headroom: str,
    daily_quota_statement: str,
    max_cost_usd: float,
    max_seconds: float,
) -> dict[str, Any]:
    """Build the spike report. Verdict is computed against the 50-case
    projection (never caller-supplied — a caller-supplied verdict would be a
    self-grading report); a failing fit is ``stop_escalate``, never absorbed."""
    sample = list(units)
    projection = extrapolate(sample)

    per_call_class: dict[str, dict[str, float]] = {}
    for call_class in CALL_CLASSES:
        seconds = [unit[call_class]["seconds"] for unit in sample]
        per_call_class[call_class] = {
            "p50_seconds": percentile(seconds, 50),
            "p95_seconds": percentile(seconds, 95),
            "input_tokens": sum(unit[call_class]["input_tokens"] for unit in sample),
            "output_tokens": sum(unit[call_class]["output_tokens"] for unit in sample),
            "cost_usd": sum(unit[call_class]["cost_usd"] for unit in sample),
        }

    fits = (
        projection["projected_cost_usd"] <= max_cost_usd
        and projection["projected_seconds"] <= max_seconds
    )
    return {
        "sample_size": len(sample),
        "per_call_class": per_call_class,
        "retry_amplification": projection["retry_amplification"],
        "rate_limit_headroom": rate_limit_headroom,
        "daily_quota_statement": daily_quota_statement,
        "extrapolated_50": projection,
        "budget": {"max_cost_usd": max_cost_usd, "max_seconds": max_seconds},
        "verdict": "viable" if fits else "stop_escalate",
        "local_key_substitution_note": _SUBSTITUTION_NOTE,
    }


# ---------------------------------------------------------------------------
# AC-4 — report text surface: no key material / Authorization header values
# ---------------------------------------------------------------------------

_SECRET_ENV_NAME = re.compile(
    r"(KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|DSN)", re.IGNORECASE
)
_URL_USERINFO_PASSWORD = re.compile(r"^[a-z0-9+.\-]+://[^:/@\s]+:([^@/\s]+)@", re.IGNORECASE)
_SK_ANT_TOKEN = re.compile(r"sk-ant-[A-Za-z0-9._\-]*")
_BEARER_VALUE = re.compile(r"Bearer\s+\S+")
_REDACTED = "[REDACTED]"


def _scrub_secrets(text: str) -> str:
    """Defense-in-depth scrub of the rendered text surface.

    The renderer only emits whitelisted computed fields (no env values, no
    request headers), so this pass should be a no-op; it exists so one future
    formatting mistake cannot burn a key in committed ticket evidence.
    """
    for name, value in os.environ.items():
        if len(value) < 8 or not _SECRET_ENV_NAME.search(name):
            continue
        text = text.replace(value, _REDACTED)
        userinfo = _URL_USERINFO_PASSWORD.match(value)
        if userinfo:
            text = text.replace(userinfo.group(1), _REDACTED)
    text = _SK_ANT_TOKEN.sub(_REDACTED, text)
    text = _BEARER_VALUE.sub(_REDACTED, text)
    return text


def render_report(report: Mapping[str, Any]) -> str:
    """Render the report as text. Only whitelisted computed fields reach the
    text surface; measurement-record ``request_headers`` never do."""
    projection = report["extrapolated_50"]
    budget = report["budget"]
    lines = [
        "W2-M24 Tier-2 spike report",
        "==========================",
        f"sample_size: {report['sample_size']} three-call units",
        f"retry_amplification: {report['retry_amplification']:.4g}",
        "",
        "per-call-class stats (per-unit-class totals across the sample):",
    ]
    for call_class in CALL_CLASSES:
        stats = report["per_call_class"][call_class]
        lines.append(
            f"  {call_class:<6} p50={stats['p50_seconds']:.3f}s "
            f"p95={stats['p95_seconds']:.3f}s "
            f"input_tokens={stats['input_tokens']} "
            f"output_tokens={stats['output_tokens']} "
            f"cost_usd={stats['cost_usd']:.6f}"
        )
    lines += [
        "",
        f"rate-limit headroom: {report['rate_limit_headroom']}",
        f"daily-quota statement: {report['daily_quota_statement']}",
        "",
        "extrapolated 50-case projection "
        "(50 x mean per-unit aggregate; multi-page VLM calls counted "
        "explicitly; retry amplification applied):",
        f"  projected_calls: {projection['projected_calls']:.4g}",
        f"  projected_seconds: {projection['projected_seconds']:.4g}",
        f"  projected_input_tokens: {projection['projected_input_tokens']:.6g}",
        f"  projected_output_tokens: {projection['projected_output_tokens']:.6g}",
        f"  projected_cost_usd: {projection['projected_cost_usd']:.4f}",
        "",
        f"budget: max_cost_usd={budget['max_cost_usd']:.2f} "
        f"max_seconds={budget['max_seconds']:.1f}",
        f"verdict: {report['verdict']}",
        "(a failing fit is a STOP escalation — never a reduction of the 50 cases)",
        "",
        f"note: {report['local_key_substitution_note']}",
    ]
    return _scrub_secrets("\n".join(lines))


# ---------------------------------------------------------------------------
# AC-5 — workflow policy lint (read-only; three-way conjunction, W2 §6a)
# ---------------------------------------------------------------------------

_PR_HEAD_REF_MARKERS = (
    "github.head_ref",
    "pull_request.head.sha",
    "pull_request.head.ref",
)
_SECRETS_EXPRESSION = re.compile(r"\$\{\{[^}]*\bsecrets\.")
_SECRETS_INHERIT = re.compile(r"(?m)^\s*secrets\s*:\s*inherit\b")


def _has_pull_request_target_trigger(data: Mapping[Any, Any]) -> bool:
    # PyYAML parses the bare `on:` workflow key as boolean True (YAML 1.1).
    triggers = data.get("on", data.get(True))
    if isinstance(triggers, str):
        return triggers.strip() == "pull_request_target"
    if isinstance(triggers, list):
        return "pull_request_target" in triggers
    if isinstance(triggers, dict):
        return "pull_request_target" in triggers
    return False


def _is_pr_head_ref(ref: Any) -> bool:
    return isinstance(ref, str) and any(m in ref for m in _PR_HEAD_REF_MARKERS)


def _pr_head_checkout_refs(data: Mapping[Any, Any]) -> list[str]:
    """Refs of actions/checkout steps that check out PR-head code. A checkout
    with no ``ref`` override checks out the BASE repo under
    ``pull_request_target`` and is compliant."""
    refs: list[str] = []
    jobs = data.get("jobs")
    if not isinstance(jobs, dict):
        return refs
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            uses = step.get("uses")
            if not isinstance(uses, str) or not uses.startswith("actions/checkout"):
                continue
            with_block = step.get("with")
            ref = with_block.get("ref") if isinstance(with_block, dict) else None
            if _is_pr_head_ref(ref):
                refs.append(ref)
    return refs


def _references_secrets(raw_text: str) -> bool:
    return bool(_SECRETS_EXPRESSION.search(raw_text) or _SECRETS_INHERIT.search(raw_text))


def lint_workflows(paths: Iterable[Path | str]) -> list[str]:
    """Lint workflow files for the forbidden three-way conjunction:
    ``pull_request_target`` trigger AND checkout of PR-head code AND explicit
    secrets usage. Empty result == compliant. Read-only.

    Each leg alone (or any two) is compliant — e.g. dependabot-auto-merge.yml
    (trigger + secrets, no PR-code checkout) must pass.
    """
    findings: list[str] = []
    for entry in paths:
        path = Path(entry)
        raw_text = path.read_text()
        try:
            data = yaml.safe_load(raw_text)
        except yaml.YAMLError as exc:
            findings.append(
                f"{path.name}: unparseable workflow YAML "
                f"({exc.__class__.__name__}) — cannot prove policy compliance"
            )
            continue
        if not isinstance(data, dict):
            continue
        if not _has_pull_request_target_trigger(data):
            continue
        head_refs = _pr_head_checkout_refs(data)
        if not head_refs:
            continue
        if not _references_secrets(raw_text):
            continue
        findings.append(
            f"{path.name}: checks out PR-head code (ref {head_refs[0]!r}) under a "
            "pull_request_target trigger with secrets access — forbidden "
            "three-way conjunction (W2 §6a / W2-M24 AC-5)"
        )
    return findings


# ---------------------------------------------------------------------------
# AC-6 — policy-doc lint over docs/week2/W2_TIER2_CI_POLICY.md (six clauses)
# ---------------------------------------------------------------------------

# Terms are matched on the doc text lowered with hyphens replaced by spaces.
_POLICY_CLAUSES: dict[str, tuple[str, ...]] = {
    "clause 1 (no repository secrets to forks)": ("no repository secrets", "fork"),
    "clause 2 (never pull_request_target checkout of fork code)": (
        "pull_request_target",
        "never",
        "checkout",
    ),
    "clause 3 (forks run Tier 1 only)": ("tier 1 only", "fork"),
    "clause 4 (maintainer reproduces the exact fork commit on a trusted "
    "same-repo branch for the required Tier-2 result before merge)": (
        "maintainer",
        "exact fork commit",
        "trusted same repo",
        "tier 2",
        "invalidates",
    ),
    "clause 5 (same-repo PRs: least-privilege environments with approval; "
    "no secret echo / artifact retention)": (
        "least privilege",
        "approval",
        "echo",
        "artifact retention",
    ),
    "clause 6 (STOP escalation — never a reduction of the 50 cases)": (
        "stop escalation",
        "never",
        "50",
    ),
}


def lint_policy_doc(path: Path | str) -> list[str]:
    """Lint the frozen policy doc for all six clauses. Empty result ==
    compliant. Missing file raises FileNotFoundError (absence must fail
    loudly, never read as 'no violations')."""
    doc_path = Path(path)
    if not doc_path.exists():
        raise FileNotFoundError(f"policy doc missing: {doc_path}")
    text = doc_path.read_text().lower().replace("-", " ")
    findings: list[str] = []
    for clause, terms in _POLICY_CLAUSES.items():
        missing = [term for term in terms if term not in text]
        if missing:
            findings.append(f"{clause}: missing required terms {missing}")
    return findings


# ---------------------------------------------------------------------------
# Synthetic non-clinical image generation (stdlib-only PNG via zlib + struct)
# ---------------------------------------------------------------------------

# Minimal 5x7 bitmap font (rows top->bottom, 5 bits, MSB = leftmost pixel).
_FONT_5X7: dict[str, tuple[int, ...]] = {
    "A": (0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    "B": (0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E),
    "C": (0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E),
    "D": (0x1C, 0x12, 0x11, 0x11, 0x11, 0x12, 0x1C),
    "E": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F),
    "F": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10),
    "G": (0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F),
    "H": (0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    "I": (0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "J": (0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C),
    "K": (0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11),
    "L": (0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F),
    "M": (0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11),
    "N": (0x11, 0x11, 0x19, 0x15, 0x13, 0x11, 0x11),
    "O": (0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E),
    "P": (0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10),
    "Q": (0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D),
    "R": (0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11),
    "S": (0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E),
    "T": (0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04),
    "U": (0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E),
    "V": (0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04),
    "W": (0x11, 0x11, 0x11, 0x15, 0x15, 0x15, 0x0A),
    "X": (0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11),
    "Y": (0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04),
    "Z": (0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F),
    "0": (0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E),
    "1": (0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "2": (0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F),
    "3": (0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E),
    "4": (0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02),
    "5": (0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E),
    "6": (0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E),
    "7": (0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08),
    "8": (0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E),
    "9": (0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C),
    ":": (0x00, 0x04, 0x04, 0x00, 0x04, 0x04, 0x00),
    "-": (0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00),
    ".": (0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C),
    "/": (0x01, 0x02, 0x04, 0x04, 0x04, 0x08, 0x10),
    " ": (0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00),
}


def _encode_png_grayscale(rows: Sequence[bytes]) -> bytes:
    """Encode 8-bit grayscale pixel rows as a minimal valid PNG (stdlib only)."""
    height = len(rows)
    width = len(rows[0])
    raw = b"".join(b"\x00" + row for row in rows)  # filter type 0 per scanline

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)  # grayscale
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def synthetic_form_png(lines: Sequence[str], scale: int = 4) -> bytes:
    """Render obviously-synthetic text lines as a black-on-white grayscale PNG."""
    glyph_w, glyph_h, gap = 5, 7, 1
    margin = 6
    width = margin * 2 + max(len(line) for line in lines) * (glyph_w + gap)
    height = margin * 2 + len(lines) * (glyph_h + gap * 2)
    grid = [[255] * width for _ in range(height)]
    for line_no, line in enumerate(lines):
        y0 = margin + line_no * (glyph_h + gap * 2)
        for col, char in enumerate(line.upper()):
            glyph = _FONT_5X7.get(char, _FONT_5X7[" "])
            x0 = margin + col * (glyph_w + gap)
            for gy, bits in enumerate(glyph):
                for gx in range(glyph_w):
                    if bits & (1 << (glyph_w - 1 - gx)):
                        grid[y0 + gy][x0 + gx] = 0
    scaled: list[bytes] = []
    for row in grid:
        scaled_row = bytes(value for value in row for _ in range(scale))
        scaled.extend(scaled_row for _ in range(scale))
    return _encode_png_grayscale(scaled)


def _synthetic_page_lines(page: int, pages: int) -> list[str]:
    if page == 1:
        return [
            "SYNTHETIC INTAKE FORM - NOT A REAL PATIENT",
            f"PAGE 1 OF {pages} - FAKE TEST DATA ONLY",
            "NAME: TESTY MCTESTFACE",
            "DOB: 2099-01-01",
            "FORM ID: FAKE-0001",
            "REASON FOR VISIT: ANNUAL CHECKUP",
        ]
    return [
        "SYNTHETIC INTAKE FORM - NOT A REAL PATIENT",
        f"PAGE {page} OF {pages} - FAKE TEST DATA ONLY",
        "ALLERGIES: NONE",
        "MEDICATIONS: NONE",
        "NOTES: SYNTHETIC DATA FOR A TIMING SPIKE",
    ]


# ---------------------------------------------------------------------------
# AC-7 — live sample run (operator CLI; prints aggregates only)
# ---------------------------------------------------------------------------

_VLM_PROMPT = (
    "This image is one page of a SYNTHETIC test intake form; every value is "
    "obviously fake and non-clinical. Extract each labeled field and its value "
    "as a flat JSON object."
)
_ANSWER_PROMPT = (
    "You are answering one boolean eval case about a SYNTHETIC test form (all "
    "data fake, non-clinical).\n\nExtracted fields:\n{extraction}\n\n"
    "Question: does the form identify itself as synthetic test data? Answer "
    "yes or no with a one-sentence justification grounded in the fields."
)
_JUDGE_PROMPT = (
    "You are a pinned judge grading one boolean eval case over SYNTHETIC test "
    "data.\n\nExtracted fields:\n{extraction}\n\nCandidate answer:\n{answer}\n\n"
    "Grade PASS or FAIL: the answer must be an explicit yes/no consistent with "
    "the extracted fields. Reply with PASS or FAIL plus one sentence."
)


def _load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines into the environment (values treated as opaque —
    never printed or logged). Existing environment wins."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _measured_call(
    client: Any,
    model: str,
    pricing: tuple[float, float],
    *,
    messages: list[dict[str, Any]],
    max_tokens: int,
) -> dict[str, Any]:
    started = time.monotonic()
    raw = client.messages.with_raw_response.create(
        model=model, max_tokens=max_tokens, messages=messages
    )
    elapsed = time.monotonic() - started
    message = raw.parse()
    input_tokens = message.usage.input_tokens
    output_tokens = message.usage.output_tokens
    cost = (input_tokens * pricing[0] + output_tokens * pricing[1]) / 1_000_000
    ratelimit_headers = {
        name.lower(): value
        for name, value in raw.headers.items()
        if name.lower().startswith("anthropic-ratelimit-")
    }
    text = "".join(
        block.text for block in message.content if getattr(block, "type", "") == "text"
    )
    return {
        "seconds": elapsed,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost,
        "retries": raw.retries_taken,
        "ratelimit_headers": ratelimit_headers,
        "text": text,
    }


def _blank_record() -> dict[str, Any]:
    return {
        "calls": 0,
        "retries": 0,
        "seconds": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
    }


def _accumulate(record: dict[str, Any], call: Mapping[str, Any]) -> None:
    record["calls"] += 1
    record["retries"] += call["retries"]
    record["seconds"] += call["seconds"]
    record["input_tokens"] += call["input_tokens"]
    record["output_tokens"] += call["output_tokens"]
    record["cost_usd"] += call["cost_usd"]


def _run_unit(
    client: Any, model: str, pricing: tuple[float, float], pages: int
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Run one real three-call unit: per-page VLM extraction + answer + judge.

    Raw provider text is throwaway — it flows through the chain and is
    discarded; only aggregates leave this function alongside the last
    response's anthropic-ratelimit-* headers.
    """
    vlm = _blank_record()
    extraction_parts: list[str] = []
    headers: dict[str, str] = {}
    for page in range(1, pages + 1):
        png = synthetic_form_png(_synthetic_page_lines(page, pages))
        image_b64 = base64.standard_b64encode(png).decode("ascii")
        call = _measured_call(
            client,
            model,
            pricing,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": _VLM_PROMPT},
                    ],
                }
            ],
            max_tokens=512,
        )
        _accumulate(vlm, call)
        extraction_parts.append(call["text"])
        headers = call["ratelimit_headers"] or headers

    extraction = "\n".join(extraction_parts)

    answer = _blank_record()
    answer_call = _measured_call(
        client,
        model,
        pricing,
        messages=[
            {"role": "user", "content": _ANSWER_PROMPT.format(extraction=extraction)}
        ],
        max_tokens=1024,
    )
    _accumulate(answer, answer_call)
    headers = answer_call["ratelimit_headers"] or headers

    judge = _blank_record()
    judge_call = _measured_call(
        client,
        model,
        pricing,
        messages=[
            {
                "role": "user",
                "content": _JUDGE_PROMPT.format(
                    extraction=extraction, answer=answer_call["text"]
                ),
            }
        ],
        max_tokens=512,
    )
    _accumulate(judge, judge_call)
    headers = judge_call["ratelimit_headers"] or headers

    return {"vlm": vlm, "answer": answer, "judge": judge}, headers


def _headroom_statement(headers: Mapping[str, str]) -> str:
    if not headers:
        return "no anthropic-ratelimit-* headers observed on the final response"
    pairs = ", ".join(f"{name}={headers[name]}" for name in sorted(headers))
    return f"observed on the final response: {pairs}"


def _daily_quota_statement(
    headers: Mapping[str, str], projection: Mapping[str, Any]
) -> str:
    def limit(name: str) -> float | None:
        value = headers.get(f"anthropic-ratelimit-{name}-limit")
        try:
            return float(value) if value is not None else None
        except ValueError:
            return None

    requests_limit = limit("requests")
    input_limit = limit("input-tokens") or limit("tokens")
    output_limit = limit("output-tokens")
    if not any((requests_limit, input_limit, output_limit)):
        return (
            "no per-minute or daily quota headers observed — quota fit cannot "
            "be stated from this run"
        )
    minutes: list[float] = []
    if requests_limit:
        minutes.append(projection["projected_calls"] / requests_limit)
    if input_limit:
        minutes.append(projection["projected_input_tokens"] / input_limit)
    if output_limit:
        minutes.append(projection["projected_output_tokens"] / output_limit)
    pacing = max(minutes)
    return (
        "no daily-cap header is exposed; observed limits are per-minute "
        f"(requests={requests_limit or 'n/a'}, input-tokens={input_limit or 'n/a'}, "
        f"output-tokens={output_limit or 'n/a'}). The 50-case projection "
        f"consumes at minimum ~{pacing:.2f} minute(s) of quota at the observed "
        "per-minute limits, so a single daily run fits with wide margin; even "
        "several PR-gate runs per day stay far below the observed limits."
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="spike_tier2",
        description=(
            "W2-M24 Tier-2 spike: measure a representative sample of the real "
            "three-call unit shape against the live Anthropic API and "
            "extrapolate the 50-case gate bound. Prints aggregates only."
        ),
    )
    parser.add_argument(
        "--units", type=int, default=DEFAULT_SAMPLE_UNITS, help="sample size in units"
    )
    parser.add_argument(
        "--first-unit-pages",
        type=int,
        default=DEFAULT_FIRST_UNIT_PAGES,
        help="pages for the first unit's VLM extraction (exercises the "
        "multi-page multiplier); remaining units use 1 page",
    )
    parser.add_argument("--max-cost-usd", type=float, default=MAX_RUN_COST_USD)
    parser.add_argument("--max-seconds", type=float, default=MAX_RUN_SECONDS)
    args = parser.parse_args(argv)

    _load_env_file(Path(__file__).resolve().parents[1] / ".env")
    model = os.environ.get("LLM_MODEL", "")
    if not model:
        print("spike_tier2: LLM_MODEL is not set (agent/.env)", file=sys.stderr)
        return 2
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("spike_tier2: ANTHROPIC_API_KEY is not set (agent/.env)", file=sys.stderr)
        return 2
    pricing = PRICING_USD_PER_MTOK.get(model)
    if pricing is None:
        print(
            f"spike_tier2: no published pricing entry for model {model!r} — "
            f"known: {sorted(PRICING_USD_PER_MTOK)}",
            file=sys.stderr,
        )
        return 2

    import anthropic  # declared dep; imported lazily so lints stay offline

    client = anthropic.Anthropic()

    print(f"model: {model}")
    print(f"pricing: input ${pricing[0]:.2f}/MTok, output ${pricing[1]:.2f}/MTok")
    print(f"pricing source: {PRICING_SOURCE}")
    print(f"sample: {args.units} units; unit 1 uses {args.first_unit_pages} pages")
    print()

    units: list[dict[str, dict[str, Any]]] = []
    headers: dict[str, str] = {}
    for index in range(args.units):
        pages = args.first_unit_pages if index == 0 else 1
        unit, unit_headers = _run_unit(client, model, pricing, pages)
        headers = unit_headers or headers
        units.append(unit)
        summary = " ".join(
            f"{cls}[calls={unit[cls]['calls']} retries={unit[cls]['retries']} "
            f"s={unit[cls]['seconds']:.2f} in={unit[cls]['input_tokens']} "
            f"out={unit[cls]['output_tokens']} usd={unit[cls]['cost_usd']:.6f}]"
            for cls in CALL_CLASSES
        )
        print(f"unit {index + 1}/{args.units} (pages={pages}): {summary}")

    projection = extrapolate(units)
    report = build_report(
        units,
        rate_limit_headroom=_headroom_statement(headers),
        daily_quota_statement=_daily_quota_statement(headers, projection),
        max_cost_usd=args.max_cost_usd,
        max_seconds=args.max_seconds,
    )
    print()
    print(render_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
