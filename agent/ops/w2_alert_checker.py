#!/usr/bin/env python3
"""Scheduled evaluator for the Week 2 alert definitions (R05 / AF-P1-04).

The Week-1 checker (``ops/alert_checker.py``) evaluates a four-signal Langfuse set and
never reads ``agent/ops/w2_alerts.json``. This module is the W2 lane: it loads the
committed alert definitions, derives metrics from two PHI-free sources, evaluates every
rule, and delivers aggregate-only notifications that link each alert's response actions
in ``docs/week2/evidence/W2_RUNBOOKS.md``.

Metric sources (both content-free by construction):

* the structured event lane — one JSON line per validated ``LogEventEnvelope``
  (``app/observability/events.py:StructuredLogEventSink``), exported from the platform
  logs; yields extraction failure rate, retrieval p95, ingestion p95, and queue age;
* the committed eval artifacts — ``evals/results-tier1.json`` + ``evals/w2_baseline.json``;
  yield the deterministic/factual scores and the >5-percentage-point regression delta.

Metrics a source cannot honestly produce (worker heartbeat age, breaker open seconds)
are reported as ``no_data`` — they never fire and are never fabricated. Scheduling is a
GitHub Actions cron workflow (``.github/workflows/agent-w2-alerts.yml``); the
``--state-file`` option persists the firing set between stateless scheduled runs so a
sustained breach notifies once and a recovery is reported as resolved.

Like the W1 checker, this module is stdlib-only and independent of the serving app.
No trace ids, patient data, query/document text, credentials, or webhook URLs are
logged or included in notification payloads.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping, Protocol, Sequence, TextIO, TypeGuard, Union

from ops.alert_checker import (
    ConfigError,
    JsonTransport,
    NotificationError,
    UrllibJsonTransport,
    _percentile_nearest_rank,
    _validate_http_url,
    _write_log,
)


DEFAULT_ALERTS_PATH = Path(__file__).resolve().parent / "w2_alerts.json"
DEFAULT_RESULTS_PATH = (
    Path(__file__).resolve().parents[1] / "evals" / "results-tier1.json"
)
DEFAULT_BASELINE_PATH = (
    Path(__file__).resolve().parents[1] / "evals" / "w2_baseline.json"
)

RUNBOOK_PATH = "docs/week2/evidence/W2_RUNBOOKS.md"
# Closed mapping from a rule's `runbook` key to the runbook's section anchor.
RUNBOOK_SECTIONS: dict[str, str] = {
    "extraction": "extraction-or-ingestion",
    "ingestion": "extraction-or-ingestion",
    "retrieval": "retrieval-or-reranker",
    "queue": "queue-or-worker",
    "worker": "queue-or-worker",
    "breaker": "breaker",
    "eval": "evaluation",
}

_DETERMINISTIC_RUBRICS = frozenset(
    {"schema_valid", "citation_present", "safe_refusal", "no_phi_in_logs"}
)
_FACTUAL_RUBRIC = "factually_consistent"
_DEFAULT_VARIABLES: dict[str, float] = {"document_worker_lease_seconds": 60.0}
_EXPRESSION_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*\*\s*([a-z][a-z0-9_]*)\s*$"
)
_OPERATORS = frozenset({">", "<"})


class RuleError(ValueError):
    """The W2 alert definitions are absent or do not match the schema."""


class W2Notifier(Protocol):
    def notify(self, alerts: list["W2Alert"], metrics: dict[str, float]) -> None: ...


@dataclass(frozen=True)
class W2Rule:
    id: str
    metric: str
    operator: str
    threshold: float
    window_seconds: int
    for_seconds: int
    runbook: str


@dataclass(frozen=True)
class W2Alert:
    rule_id: str
    metric: str
    value: float
    threshold: float
    operator: str
    runbook_url: str


@dataclass(frozen=True)
class W2CycleResult:
    metrics: dict[str, float]
    firing: list[W2Alert]
    new_alerts: list[W2Alert]
    resolved: list[str]
    no_data: list[str]


def runbook_link(runbook: str) -> str:
    """Return the repo-relative runbook link for a rule's closed runbook key."""

    try:
        return f"{RUNBOOK_PATH}#{RUNBOOK_SECTIONS[runbook]}"
    except KeyError:
        raise RuleError(f"unknown runbook key: {runbook!r}") from None


def load_rules(
    path: Path, *, variables: Mapping[str, float] | None = None
) -> tuple[W2Rule, ...]:
    """Load and validate ``w2_alerts.json``; resolve threshold expressions."""

    resolved_variables = dict(_DEFAULT_VARIABLES)
    if variables is not None:
        resolved_variables.update(variables)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise RuleError("W2 alert definitions could not be read") from None
    rules_payload = payload.get("rules") if isinstance(payload, dict) else None
    if not isinstance(rules_payload, list) or not rules_payload:
        raise RuleError("W2 alert definitions omitted rules")

    rules: list[W2Rule] = []
    for row in rules_payload:
        if not isinstance(row, dict):
            raise RuleError("W2 alert rule was not an object")
        rule_id = row.get("id")
        metric = row.get("metric")
        operator = row.get("operator")
        runbook = row.get("runbook")
        if not isinstance(rule_id, str) or not rule_id:
            raise RuleError("W2 alert rule omitted id")
        if not isinstance(metric, str) or not metric:
            raise RuleError(f"W2 alert rule {rule_id} omitted metric")
        if operator not in _OPERATORS:
            raise RuleError(f"W2 alert rule {rule_id} has an unknown operator")
        if not isinstance(runbook, str) or runbook not in RUNBOOK_SECTIONS:
            raise RuleError(f"W2 alert rule {rule_id} has an unknown runbook key")
        threshold = _resolve_threshold(row, rule_id, resolved_variables)
        rules.append(
            W2Rule(
                id=rule_id,
                metric=metric,
                operator=operator,
                threshold=threshold,
                window_seconds=_nonnegative_int(row.get("window_seconds", 0), rule_id),
                for_seconds=_nonnegative_int(row.get("for_seconds", 0), rule_id),
                runbook=runbook,
            )
        )
    return tuple(rules)


def _resolve_threshold(
    row: Mapping[str, object], rule_id: str, variables: Mapping[str, float]
) -> float:
    threshold = row.get("threshold")
    if threshold is not None:
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise RuleError(f"W2 alert rule {rule_id} threshold was not numeric")
        value = float(threshold)
    else:
        expression = row.get("threshold_expression")
        if not isinstance(expression, str):
            raise RuleError(f"W2 alert rule {rule_id} omitted a threshold")
        match = _EXPRESSION_RE.match(expression)
        if match is None:
            raise RuleError(
                f"W2 alert rule {rule_id} threshold expression was not "
                "'<factor> * <variable>'"
            )
        factor, name = float(match.group(1)), match.group(2)
        if name not in variables:
            raise RuleError(
                f"W2 alert rule {rule_id} references unknown variable {name}"
            )
        value = factor * float(variables[name])
    if not math.isfinite(value):
        raise RuleError(f"W2 alert rule {rule_id} threshold was not finite")
    return value


def _nonnegative_int(value: object, rule_id: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuleError(f"W2 alert rule {rule_id} has an invalid window/for value")
    return value


# --- metric collection --------------------------------------------------------


def collect_event_metrics(
    lines: Iterable[str], *, now: datetime, rules: Sequence[W2Rule]
) -> dict[str, float]:
    """Derive event-lane metrics from structured event JSON lines.

    Only well-formed ``w2.event`` lines within each metric's rule window count.
    Non-event lines (ordinary logs, malformed rows) are skipped, never fatal.
    Every produced value is an aggregate; no attribute text is retained.
    """

    if now.tzinfo is None:
        raise ValueError("metric collection clock must be timezone-aware")
    windows = {rule.metric: rule.window_seconds for rule in rules}
    extraction_total = 0
    extraction_failed = 0
    retrieval_latencies: list[float] = []
    ingestion_totals: list[float] = []
    queue_ages_seconds: list[float] = []

    for line in lines:
        event = _parse_event_line(line)
        if event is None:
            continue
        event_type, occurred_at, component, severity, attributes = event

        if event_type == "encounter.summary" and component == "worker":
            if _in_window(occurred_at, now, windows.get("extraction.failure_rate")):
                extraction_total += 1
                extraction_failed += int(severity == "error")
            if _in_window(occurred_at, now, windows.get("ingestion.p95_ms")):
                latencies = attributes.get("step_latencies_ms")
                if isinstance(latencies, list):
                    total = sum(
                        value
                        for value in latencies
                        if _finite_nonnegative(value)
                    )
                    ingestion_totals.append(float(total))
        elif event_type == "retrieval.completed":
            if _in_window(occurred_at, now, windows.get("retrieval.p95_ms")):
                latency = attributes.get("latency_ms")
                if _finite_nonnegative(latency):
                    retrieval_latencies.append(float(latency))
        elif event_type == "queue.state":
            if _in_window(occurred_at, now, windows.get("queue.oldest_age_seconds")):
                age_ms = attributes.get("queue_age_ms")
                if _finite_nonnegative(age_ms):
                    queue_ages_seconds.append(float(age_ms) / 1000.0)

    metrics: dict[str, float] = {}
    if extraction_total:
        metrics["extraction.failure_rate"] = extraction_failed / extraction_total
    retrieval_p95 = _percentile_nearest_rank(sorted(retrieval_latencies), 0.95)
    if retrieval_p95 is not None:
        metrics["retrieval.p95_ms"] = retrieval_p95
    ingestion_p95 = _percentile_nearest_rank(sorted(ingestion_totals), 0.95)
    if ingestion_p95 is not None:
        metrics["ingestion.p95_ms"] = ingestion_p95
    if queue_ages_seconds:
        metrics["queue.oldest_age_seconds"] = max(queue_ages_seconds)
    return metrics


def _parse_event_line(
    line: str,
) -> tuple[str, datetime, str, str, dict] | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    event_type = payload.get("event_type")
    occurred_at_raw = payload.get("occurred_at")
    attributes = payload.get("attributes")
    if not isinstance(event_type, str) or not isinstance(occurred_at_raw, str):
        return None
    if not isinstance(attributes, dict):
        return None
    try:
        occurred_at = datetime.fromisoformat(occurred_at_raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if occurred_at.tzinfo is None:
        return None
    component = payload.get("component")
    severity = payload.get("severity")
    return (
        event_type,
        occurred_at.astimezone(timezone.utc),
        component if isinstance(component, str) else "",
        severity if isinstance(severity, str) else "",
        attributes,
    )


def _in_window(occurred_at: datetime, now: datetime, window_seconds: int | None) -> bool:
    if window_seconds is None:
        return False
    if occurred_at > now:
        return False
    if window_seconds == 0:
        return True
    return now - occurred_at <= timedelta(seconds=window_seconds)


def _finite_nonnegative(value: object) -> TypeGuard[Union[int, float]]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value)) and float(value) >= 0


def collect_eval_metrics(results_path: Path, baseline_path: Path) -> dict[str, float]:
    """Derive eval metrics from the committed gate artifacts (aggregate scores only)."""

    metrics: dict[str, float] = {}
    current = _category_scores(results_path, score_key="current_score")
    baseline = _category_scores(baseline_path, score_key="score")

    deterministic = [
        score
        for rubric, score in current.items()
        if rubric in _DETERMINISTIC_RUBRICS
    ]
    if deterministic:
        metrics["eval.deterministic_score"] = min(deterministic)
    factual = current.get(_FACTUAL_RUBRIC)
    if factual is not None:
        metrics["eval.factual_score"] = factual
        factual_baseline = baseline.get(_FACTUAL_RUBRIC)
        if factual_baseline is not None:
            metrics["eval.factual_baseline_minus_current_points"] = (
                factual_baseline - factual
            ) * 100.0
    return metrics


def _category_scores(path: Path, *, score_key: str) -> dict[str, float]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    categories = payload.get("categories") if isinstance(payload, dict) else None
    if not isinstance(categories, list):
        return {}
    scores: dict[str, float] = {}
    for row in categories:
        if not isinstance(row, dict):
            continue
        rubric = row.get("rubric")
        score = row.get(score_key)
        if not isinstance(rubric, str):
            continue
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            continue
        if math.isfinite(float(score)):
            scores[rubric] = float(score)
    return scores


# --- evaluation ---------------------------------------------------------------


def evaluate_rules(
    rules: Sequence[W2Rule], metrics: Mapping[str, float]
) -> tuple[list[W2Alert], list[str]]:
    """Return (firing alerts in definition order, rule ids with no usable data)."""

    firing: list[W2Alert] = []
    no_data: list[str] = []
    for rule in rules:
        value = metrics.get(rule.metric)
        if value is None:
            no_data.append(rule.id)
            continue
        breached = value > rule.threshold if rule.operator == ">" else value < rule.threshold
        if breached:
            firing.append(
                W2Alert(
                    rule_id=rule.id,
                    metric=rule.metric,
                    value=float(value),
                    threshold=rule.threshold,
                    operator=rule.operator,
                    runbook_url=runbook_link(rule.runbook),
                )
            )
    return firing, no_data


class W2AlertChecker:
    """Evaluate one metrics snapshot; notify newly firing alerts; report resolutions."""

    def __init__(
        self,
        rules: Sequence[W2Rule],
        *,
        notifier: W2Notifier,
        state: set[str] | None = None,
    ):
        self._rules = tuple(rules)
        self._notifier = notifier
        self._active: set[str] = set(state or set())

    @property
    def active(self) -> set[str]:
        return set(self._active)

    def run_once(self, metrics: Mapping[str, float]) -> W2CycleResult:
        firing, no_data = evaluate_rules(self._rules, metrics)
        current = {alert.rule_id for alert in firing}
        new_alerts = [alert for alert in firing if alert.rule_id not in self._active]
        # A rule with no data is neither firing nor resolved: keep its prior state.
        resolved = sorted(self._active - current - set(no_data))
        if new_alerts:
            self._notifier.notify(new_alerts, dict(metrics))
        self._active = (self._active & set(no_data)) | current
        return W2CycleResult(
            metrics=dict(metrics),
            firing=firing,
            new_alerts=new_alerts,
            resolved=resolved,
            no_data=no_data,
        )


# --- delivery -----------------------------------------------------------------


class W2WebhookNotifier:
    """Send an aggregate-only JSON payload with runbook links to a webhook."""

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float,
        transport: JsonTransport | None = None,
    ):
        self._url = url
        self._timeout_seconds = timeout_seconds
        self._transport = transport or UrllibJsonTransport()

    def notify(self, alerts: list[W2Alert], metrics: dict[str, float]) -> None:
        payload = _webhook_payload(alerts, metrics)
        try:
            self._transport.post_json(self._url, payload, timeout=self._timeout_seconds)
        except NotificationError:
            raise
        except Exception:
            raise NotificationError("W2 alert webhook delivery failed") from None


class W2DryRunNotifier:
    """Explicit no-delivery mode for drills, configuration checks, and CI."""

    def notify(self, alerts: list[W2Alert], metrics: dict[str, float]) -> None:
        return None


def _webhook_payload(alerts: list[W2Alert], metrics: dict[str, float]) -> dict:
    summary = "; ".join(
        f"{alert.rule_id}={alert.value:.4g}{alert.operator}{alert.threshold:.4g} "
        f"→ {alert.runbook_url}"
        for alert in alerts
    )
    return {
        "text": f"Clinical Co-Pilot W2 alert: {summary}",
        "source": "clinical-copilot-w2-alert-checker",
        "alerts": [
            {
                "id": alert.rule_id,
                "metric": alert.metric,
                "value": round(alert.value, 6),
                "threshold": round(alert.threshold, 6),
                "operator": alert.operator,
                "runbook": alert.runbook_url,
            }
            for alert in alerts
        ],
        "metrics": {name: round(value, 6) for name, value in sorted(metrics.items())},
    }


# --- persisted firing state (stateless scheduled runs) ------------------------


def load_state(path: Path) -> set[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    active = payload.get("active") if isinstance(payload, dict) else None
    if not isinstance(active, list):
        return set()
    return {item for item in active if isinstance(item, str)}


def save_state(path: Path, active: set[str]) -> None:
    path.write_text(
        json.dumps({"active": sorted(active)}, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


# --- CLI ----------------------------------------------------------------------


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    transport: JsonTransport | None = None,
) -> int:
    import os

    out = stdout or sys.stdout
    err = stderr or sys.stderr
    env = os.environ if environ is None else environ

    parser = argparse.ArgumentParser(
        description="Clinical Co-Pilot Week 2 alert checker (agent/ops/w2_alerts.json)"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="evaluate one window and exit (the scheduled mode)",
    )
    parser.add_argument("--alerts", default=str(DEFAULT_ALERTS_PATH))
    parser.add_argument(
        "--events-file", default=None,
        help="structured event JSON lines exported from the platform logs",
    )
    parser.add_argument("--results", default=str(DEFAULT_RESULTS_PATH))
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE_PATH))
    parser.add_argument("--state-file", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    dry_run = args.dry_run or env.get("COPILOT_ALERT_DRY_RUN", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    webhook_url = (env.get("COPILOT_ALERT_WEBHOOK_URL") or "").strip() or None

    try:
        if not dry_run and webhook_url is None:
            raise ConfigError(
                "COPILOT_ALERT_WEBHOOK_URL is required unless --dry-run is set"
            )
        if webhook_url is not None:
            webhook_url = _validate_http_url(
                webhook_url, "COPILOT_ALERT_WEBHOOK_URL", allow_path=True
            )
        variables = dict(_DEFAULT_VARIABLES)
        lease = (env.get("DOCUMENT_WORKER_LEASE_SECONDS") or "").strip()
        if lease:
            try:
                variables["document_worker_lease_seconds"] = float(lease)
            except ValueError:
                raise ConfigError(
                    "DOCUMENT_WORKER_LEASE_SECONDS must be numeric"
                ) from None
        rules = load_rules(Path(args.alerts), variables=variables)
    except (ConfigError, RuleError) as exc:
        _write_log(err, "configuration_error", message=str(exc))
        return 2

    metrics: dict[str, float] = {}
    events_file_available = False
    if args.events_file is not None:
        events_path = Path(args.events_file)
        if events_path.exists():
            events_file_available = True
            with events_path.open(encoding="utf-8") as handle:
                metrics.update(
                    collect_event_metrics(
                        handle, now=datetime.now(timezone.utc), rules=rules
                    )
                )
    metrics.update(collect_eval_metrics(Path(args.results), Path(args.baseline)))

    notifier: W2Notifier
    if dry_run or webhook_url is None:
        notifier = W2DryRunNotifier()
    else:
        notifier = W2WebhookNotifier(
            webhook_url, timeout_seconds=10.0, transport=transport
        )

    state_path = Path(args.state_file) if args.state_file else None
    state = load_state(state_path) if state_path is not None else set()
    checker = W2AlertChecker(rules, notifier=notifier, state=state)
    try:
        result = checker.run_once(metrics)
    except NotificationError:
        _write_log(err, "notification_error", message="W2 alert delivery failed")
        return 4
    except Exception:
        _write_log(err, "checker_error", message="unexpected W2 checker failure")
        return 5
    if state_path is not None:
        save_state(state_path, checker.active)

    _write_log(
        out,
        "w2_window_evaluated",
        metrics={name: round(value, 6) for name, value in sorted(result.metrics.items())},
        firing=[alert.rule_id for alert in result.firing],
        notified=[alert.rule_id for alert in result.new_alerts],
        resolved=result.resolved,
        no_data=result.no_data,
        events_file_available=events_file_available,
        dry_run=dry_run or webhook_url is None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
