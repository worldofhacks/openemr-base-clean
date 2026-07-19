"""R05 / AF-P1-04 — the scheduled evaluator for ``agent/ops/w2_alerts.json``.

The Week-1 checker (``ops/alert_checker.py``) evaluated a four-signal set and never
read the Week-2 alert definitions. These tests pin the W2 lane: the definitions load,
every rule links a real runbook section in ``docs/week2/evidence/W2_RUNBOOKS.md``,
metrics derive from the PHI-free structured event lane plus the committed eval
artifacts, and the three PDF-required alerts (extraction failure rate, retrieval p95
latency, eval regression > 5 percentage points) demonstrably fire and clear on safe
synthetic conditions.
"""

from __future__ import annotations

import io
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ops import w2_alert_checker as w2


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNBOOKS = REPO_ROOT / "docs" / "week2" / "evidence" / "W2_RUNBOOKS.md"
NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)


def _event_line(
    event_type: str,
    attributes: dict,
    *,
    occurred_at: datetime,
    component: str = "worker",
    severity: str = "info",
    correlation_id: str = "corr-synthetic",
) -> str:
    return json.dumps(
        {
            "log_type": "w2.event",
            "schema_version": 1,
            "event_id": uuid.uuid4().hex,
            "event_type": event_type,
            "occurred_at": occurred_at.isoformat(),
            "component": component,
            "severity": severity,
            "correlation_id": correlation_id,
            "attributes": attributes,
        },
        separators=(",", ":"),
    )


def _summary_line(*, severity: str, occurred_at: datetime, total_ms: float = 900.0) -> str:
    return _event_line(
        "encounter.summary",
        {
            "ordered_steps": ["ocr", "vlm"],
            "step_latencies_ms": [total_ms / 2, total_ms / 2],
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_usd": 0.01,
            "retrieval_hit_count": 0,
            "extraction_grounding_rate": 1.0,
            "verification_outcomes": ["complete" if severity == "info" else "failed"],
        },
        occurred_at=occurred_at,
        component="worker",
        severity=severity,
    )


def _retrieval_line(latency_ms: float, occurred_at: datetime) -> str:
    return _event_line(
        "retrieval.completed",
        {
            "hit_count": 3,
            "latency_ms": latency_ms,
            "degraded": False,
            "reranker_mode": "local",
        },
        occurred_at=occurred_at,
        component="retrieval",
    )


def _results_payload(*, factual: float, deterministic: float = 1.0) -> dict:
    categories = [
        {"rubric": "schema_valid", "current_score": deterministic},
        {"rubric": "citation_present", "current_score": deterministic},
        {"rubric": "factually_consistent", "current_score": factual},
        {"rubric": "safe_refusal", "current_score": deterministic},
        {"rubric": "no_phi_in_logs", "current_score": deterministic},
    ]
    return {"case_count": 50, "categories": categories}


def _baseline_payload(*, factual: float = 1.0) -> dict:
    return {
        "status": "PASS",
        "categories": [
            {"rubric": "schema_valid", "score": 1.0},
            {"rubric": "factually_consistent", "score": factual},
        ],
    }


def _eval_files(tmp_path: Path, *, factual: float, baseline_factual: float = 1.0):
    results = tmp_path / "results.json"
    baseline = tmp_path / "baseline.json"
    results.write_text(json.dumps(_results_payload(factual=factual)), encoding="utf-8")
    baseline.write_text(
        json.dumps(_baseline_payload(factual=baseline_factual)), encoding="utf-8"
    )
    return results, baseline


class _CapturingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[list, dict]] = []

    def notify(self, alerts: list, metrics: dict) -> None:
        self.calls.append((list(alerts), dict(metrics)))


# --- rule loading over the real definitions ----------------------------------


def test_rules_load_every_w2_alert_definition():
    rules = w2.load_rules(w2.DEFAULT_ALERTS_PATH)
    by_id = {rule.id: rule for rule in rules}
    assert set(by_id) == {
        "extraction-failure-rate",
        "retrieval-p95",
        "ingestion-p95",
        "queue-oldest",
        "worker-heartbeat",
        "breaker-open",
        "deterministic-eval",
        "factual-eval-threshold",
        "factual-eval-delta",
    }
    assert by_id["extraction-failure-rate"].threshold == 0.2
    assert by_id["retrieval-p95"].threshold == 2000
    assert by_id["factual-eval-delta"].operator == ">"
    assert by_id["factual-eval-delta"].threshold == 5.0
    # threshold_expression "2 * document_worker_lease_seconds" resolves with defaults.
    assert by_id["worker-heartbeat"].threshold == 120.0


def test_rule_expression_uses_supplied_variables():
    rules = w2.load_rules(
        w2.DEFAULT_ALERTS_PATH,
        variables={"document_worker_lease_seconds": 45.0},
    )
    by_id = {rule.id: rule for rule in rules}
    assert by_id["worker-heartbeat"].threshold == 90.0


# --- every alert links a real runbook section --------------------------------


def _runbook_anchors() -> set[str]:
    anchors = set()
    for line in RUNBOOKS.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            heading = line[3:].strip().casefold()
            anchors.add(re.sub(r"[^a-z0-9]+", "-", heading).strip("-"))
    return anchors


def test_every_rule_links_a_real_runbook_section():
    anchors = _runbook_anchors()
    for rule in w2.load_rules(w2.DEFAULT_ALERTS_PATH):
        link = w2.runbook_link(rule.runbook)
        assert link.startswith("docs/week2/evidence/W2_RUNBOOKS.md#")
        assert link.rsplit("#", 1)[1] in anchors


def test_runbooks_file_carries_the_alert_mapping_lines():
    text = RUNBOOKS.read_text(encoding="utf-8")
    for rule in w2.load_rules(w2.DEFAULT_ALERTS_PATH):
        assert f"`{rule.id}`" in text, f"missing alert mapping line for {rule.id}"


# --- metric collection from the structured event lane ------------------------


def test_event_metrics_derive_from_the_structured_event_lane():
    fresh = NOW - timedelta(minutes=5)
    lines = [
        _summary_line(severity="info", occurred_at=fresh),
        _summary_line(severity="error", occurred_at=fresh),
        _summary_line(severity="error", occurred_at=fresh),
        _retrieval_line(100.0, fresh),
        _retrieval_line(2500.0, fresh),
        _event_line(
            "queue.state",
            {"state": "queued", "attempt_count": 0, "queue_age_ms": 45_000.0},
            occurred_at=fresh,
        ),
        "not json at all",
        json.dumps({"level": "INFO", "message": "ordinary log line"}),
    ]
    rules = w2.load_rules(w2.DEFAULT_ALERTS_PATH)
    metrics = w2.collect_event_metrics(lines, now=NOW, rules=rules)
    assert metrics["extraction.failure_rate"] == pytest.approx(2 / 3)
    assert metrics["retrieval.p95_ms"] == 2500.0
    assert metrics["ingestion.p95_ms"] == 900.0
    assert metrics["queue.oldest_age_seconds"] == 45.0
    # Not derivable from the event lane; must be reported no_data, never fabricated.
    assert "worker.heartbeat_age_seconds" not in metrics
    assert "breaker.open_seconds" not in metrics


def test_event_metrics_respect_each_rule_window():
    stale = NOW - timedelta(hours=3)
    lines = [
        _summary_line(severity="error", occurred_at=stale),
        _retrieval_line(9_000.0, stale),
    ]
    rules = w2.load_rules(w2.DEFAULT_ALERTS_PATH)
    metrics = w2.collect_event_metrics(lines, now=NOW, rules=rules)
    assert "extraction.failure_rate" not in metrics  # outside the 1 h window
    assert "retrieval.p95_ms" not in metrics  # outside the 15 min window


# --- eval metrics from the committed artifacts --------------------------------


def test_eval_metrics_read_results_and_baseline(tmp_path):
    results, baseline = _eval_files(tmp_path, factual=0.9, baseline_factual=0.96)
    metrics = w2.collect_eval_metrics(results, baseline)
    assert metrics["eval.deterministic_score"] == 1.0
    assert metrics["eval.factual_score"] == 0.9
    assert metrics["eval.factual_baseline_minus_current_points"] == pytest.approx(6.0)


def test_eval_metrics_missing_files_are_no_data(tmp_path):
    metrics = w2.collect_eval_metrics(
        tmp_path / "absent.json", tmp_path / "absent-baseline.json"
    )
    assert metrics == {}


# --- evaluation + no-data honesty ---------------------------------------------


def test_missing_metrics_report_no_data_and_never_fire():
    rules = w2.load_rules(w2.DEFAULT_ALERTS_PATH)
    firing, no_data = w2.evaluate_rules(rules, {})
    assert firing == []
    assert set(no_data) == {rule.id for rule in rules}


# --- the three required alert drills: fire, then clear ------------------------


def _checker(notifier: _CapturingNotifier) -> w2.W2AlertChecker:
    return w2.W2AlertChecker(
        w2.load_rules(w2.DEFAULT_ALERTS_PATH), notifier=notifier
    )


def test_drill_extraction_failure_rate_fires_and_clears():
    notifier = _CapturingNotifier()
    checker = _checker(notifier)
    rules = w2.load_rules(w2.DEFAULT_ALERTS_PATH)

    fresh = NOW - timedelta(minutes=5)
    breach_lines = [
        _summary_line(severity="error", occurred_at=fresh),
        _summary_line(severity="error", occurred_at=fresh),
        _summary_line(severity="info", occurred_at=fresh),
    ]
    breach = checker.run_once(
        w2.collect_event_metrics(breach_lines, now=NOW, rules=rules)
    )
    fired = {alert.rule_id for alert in breach.new_alerts}
    assert "extraction-failure-rate" in fired
    [alert] = [a for a in breach.new_alerts if a.rule_id == "extraction-failure-rate"]
    assert alert.value == pytest.approx(2 / 3)
    assert alert.runbook_url.endswith("#extraction-or-ingestion")
    assert notifier.calls  # delivery attempted for the newly firing alert

    healthy_lines = [_summary_line(severity="info", occurred_at=fresh)] * 5
    clear = checker.run_once(
        w2.collect_event_metrics(healthy_lines, now=NOW, rules=rules)
    )
    assert "extraction-failure-rate" in clear.resolved
    assert all(a.rule_id != "extraction-failure-rate" for a in clear.firing)


def test_drill_retrieval_p95_fires_and_clears():
    notifier = _CapturingNotifier()
    checker = _checker(notifier)
    rules = w2.load_rules(w2.DEFAULT_ALERTS_PATH)
    fresh = NOW - timedelta(minutes=5)

    breach_lines = [_retrieval_line(2_400.0, fresh) for _ in range(5)]
    breach = checker.run_once(
        w2.collect_event_metrics(breach_lines, now=NOW, rules=rules)
    )
    [alert] = [a for a in breach.new_alerts if a.rule_id == "retrieval-p95"]
    assert alert.value == 2_400.0
    assert alert.runbook_url.endswith("#retrieval-or-reranker")

    healthy_lines = [_retrieval_line(300.0, fresh) for _ in range(5)]
    clear = checker.run_once(
        w2.collect_event_metrics(healthy_lines, now=NOW, rules=rules)
    )
    assert "retrieval-p95" in clear.resolved
    assert all(a.rule_id != "retrieval-p95" for a in clear.firing)


def test_drill_eval_regression_over_five_points_fires_and_clears(tmp_path):
    notifier = _CapturingNotifier()
    checker = _checker(notifier)

    results, baseline = _eval_files(tmp_path, factual=0.9, baseline_factual=0.96)
    breach = checker.run_once(w2.collect_eval_metrics(results, baseline))
    [alert] = [a for a in breach.new_alerts if a.rule_id == "factual-eval-delta"]
    assert alert.value == pytest.approx(6.0)
    assert alert.runbook_url.endswith("#evaluation")

    results, baseline = _eval_files(tmp_path, factual=1.0, baseline_factual=1.0)
    clear = checker.run_once(w2.collect_eval_metrics(results, baseline))
    assert "factual-eval-delta" in clear.resolved
    assert all(a.rule_id != "factual-eval-delta" for a in clear.firing)


def test_repeated_breach_notifies_once_until_resolved():
    notifier = _CapturingNotifier()
    checker = _checker(notifier)
    rules = w2.load_rules(w2.DEFAULT_ALERTS_PATH)
    fresh = NOW - timedelta(minutes=5)
    breach_lines = [_retrieval_line(2_400.0, fresh)]

    checker.run_once(w2.collect_event_metrics(breach_lines, now=NOW, rules=rules))
    second = checker.run_once(
        w2.collect_event_metrics(breach_lines, now=NOW, rules=rules)
    )
    assert second.new_alerts == []
    assert len(notifier.calls) == 1


# --- delivery payload stays aggregate-only ------------------------------------


def test_webhook_payload_contains_only_aggregates_and_runbook_links():
    sent: list[tuple[str, dict]] = []

    class _Transport:
        def get_json(self, url, *, headers, timeout):  # pragma: no cover - unused
            raise AssertionError("no reads expected")

        def post_json(self, url, payload, *, timeout):
            sent.append((url, payload))

    notifier = w2.W2WebhookNotifier(
        "https://hooks.example/w2", timeout_seconds=5.0, transport=_Transport()
    )
    alert = w2.W2Alert(
        rule_id="retrieval-p95",
        metric="retrieval.p95_ms",
        value=2400.0,
        threshold=2000.0,
        operator=">",
        runbook_url=w2.runbook_link("retrieval"),
    )
    notifier.notify([alert], {"retrieval.p95_ms": 2400.0})

    [(url, payload)] = sent
    assert url == "https://hooks.example/w2"
    assert payload["source"] == "clinical-copilot-w2-alert-checker"
    assert payload["alerts"][0]["runbook"].endswith("#retrieval-or-reranker")
    blob = json.dumps(payload).casefold()
    for forbidden in (
        "patient", "prompt", "transcript", "query_text", "document_text",
        "access_token", "authorization",
    ):
        assert forbidden not in blob


# --- state file + CLI ---------------------------------------------------------


def test_state_file_dedupes_across_scheduled_runs(tmp_path):
    state_file = tmp_path / "state.json"
    rules = w2.load_rules(w2.DEFAULT_ALERTS_PATH)
    fresh = NOW - timedelta(minutes=5)
    breach_lines = [_retrieval_line(2_400.0, fresh)]
    metrics = w2.collect_event_metrics(breach_lines, now=NOW, rules=rules)

    first = _CapturingNotifier()
    checker = w2.W2AlertChecker(
        rules, notifier=first, state=w2.load_state(state_file)
    )
    checker.run_once(metrics)
    w2.save_state(state_file, checker.active)
    assert len(first.calls) == 1

    second = _CapturingNotifier()
    resumed = w2.W2AlertChecker(
        rules, notifier=second, state=w2.load_state(state_file)
    )
    resumed.run_once(metrics)
    assert second.calls == []  # still firing, already notified in a previous run


def test_main_dry_run_evaluates_once_and_logs_aggregates(tmp_path):
    results, baseline = _eval_files(tmp_path, factual=0.9, baseline_factual=0.96)
    events_file = tmp_path / "events.jsonl"
    fresh = datetime.now(timezone.utc) - timedelta(minutes=2)
    events_file.write_text(
        "\n".join(
            [
                _summary_line(severity="info", occurred_at=fresh),
                _retrieval_line(300.0, fresh),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out = io.StringIO()
    err = io.StringIO()
    code = w2.main(
        [
            "--once",
            "--dry-run",
            "--events-file", str(events_file),
            "--results", str(results),
            "--baseline", str(baseline),
            "--state-file", str(tmp_path / "state.json"),
        ],
        environ={},
        stdout=out,
        stderr=err,
    )
    assert code == 0
    record = json.loads(out.getvalue().strip().splitlines()[-1])
    assert record["event"] == "w2_window_evaluated"
    assert "factual-eval-delta" in record["firing"]
    assert record["dry_run"] is True
    assert "worker-heartbeat" in record["no_data"]


def test_main_missing_events_file_reports_no_data_and_exits_zero(tmp_path):
    results, baseline = _eval_files(tmp_path, factual=1.0)
    out = io.StringIO()
    code = w2.main(
        [
            "--once",
            "--dry-run",
            "--events-file", str(tmp_path / "absent.jsonl"),
            "--results", str(results),
            "--baseline", str(baseline),
        ],
        environ={},
        stdout=out,
        stderr=io.StringIO(),
    )
    assert code == 0
    record = json.loads(out.getvalue().strip().splitlines()[-1])
    assert record["events_file_available"] is False
    assert "retrieval-p95" in record["no_data"]


def test_main_requires_webhook_unless_dry_run(tmp_path):
    results, baseline = _eval_files(tmp_path, factual=1.0)
    err = io.StringIO()
    code = w2.main(
        [
            "--once",
            "--results", str(results),
            "--baseline", str(baseline),
        ],
        environ={},
        stdout=io.StringIO(),
        stderr=err,
    )
    assert code == 2
    assert "configuration_error" in err.getvalue()
