"""Week 2 dashboard and alert definitions remain complete and rubric-exact."""

from __future__ import annotations

import json
from pathlib import Path


OPS = Path(__file__).resolve().parents[1]


def _load(name: str) -> dict:
    return json.loads((OPS / name).read_text(encoding="utf-8"))


def test_dashboard_covers_every_week2_operating_surface():
    dashboard = _load("w2_dashboard.json")
    assert dashboard["data_policy"] == "aggregate_refs_only"
    ids = {panel["id"] for panel in dashboard["panels"]}
    assert ids == {
        "ingestion",
        "grounding",
        "retrieval",
        "worker-routing",
        "queue",
        "write-intents",
        "tokens-cost",
        "evals",
        "readiness",
        "breakers",
    }


def test_alert_rules_lock_exact_thresholds_and_strict_factual_delta():
    rules = {rule["id"]: rule for rule in _load("w2_alerts.json")["rules"]}
    assert rules["extraction-failure-rate"]["threshold"] == 0.2
    assert rules["retrieval-p95"]["threshold"] == 2000
    assert rules["retrieval-p95"]["window_seconds"] == 900
    assert rules["ingestion-p95"]["threshold"] == 30000
    assert rules["queue-oldest"]["threshold"] == 120
    assert rules["worker-heartbeat"]["threshold_expression"].startswith("2 *")
    # The metric is already elapsed-open time; another five-minute `for` would make
    # the alert fire after roughly ten minutes instead of the required five.
    assert rules["breaker-open"]["threshold"] == 300
    assert rules["breaker-open"]["for_seconds"] == 0
    assert rules["deterministic-eval"]["threshold"] == 1.0
    assert rules["factual-eval-threshold"]["threshold"] == 0.9
    assert rules["factual-eval-delta"]["operator"] == ">"
    assert rules["factual-eval-delta"]["threshold"] == 5.0


def test_definitions_never_dimension_by_clinical_or_identity_values():
    combined = json.dumps(
        [_load("w2_dashboard.json"), _load("w2_alerts.json")]
    ).casefold()
    for forbidden in (
        "patient_id",
        "patient_name",
        "document_text",
        "query_text",
        "prompt",
        "transcript",
        "access_token",
    ):
        assert forbidden not in combined
