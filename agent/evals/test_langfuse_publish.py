"""D16 / §8: Langfuse publishing must never weaken the offline eval gate.

These tests freeze the publisher as a soft, additive sink: the canonical results export
and process exit code remain wholly determined by the offline cases.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals import runner
from app.llm.provider import Usage
from app.orchestrator.loop import BriefResult
from evals.langfuse_publish import _outcome_metrics, publish_if_configured
from evals.schema import EvalCase, EvalCategory, EvalResult


@dataclass(frozen=True)
class _DatasetItem:
    id: str
    dataset_id: str
    input: dict
    expected_output: dict
    metadata: dict


class _RecordingLangfuse:
    def __init__(self) -> None:
        self.datasets: list[dict] = []
        self.items: list[dict] = []
        self.experiments: list[dict] = []
        self.flushes = 0

    def create_dataset(self, **kwargs):
        self.datasets.append(kwargs)
        return SimpleNamespace(name=kwargs["name"])

    def create_dataset_item(self, **kwargs):
        self.items.append(kwargs)
        return _DatasetItem(
            id=kwargs["id"],
            dataset_id="dataset-1",
            input=kwargs["input"],
            expected_output=kwargs["expected_output"],
            metadata=kwargs["metadata"],
        )

    def run_experiment(self, **kwargs):
        scored = []
        for item in kwargs["data"]:
            output = kwargs["task"](item=item)
            if asyncio.iscoroutine(output):
                output = asyncio.run(output)
            evaluations = []
            for evaluator in kwargs["evaluators"]:
                values = evaluator(
                    input=item.input,
                    output=output,
                    expected_output=item.expected_output,
                    metadata=item.metadata,
                )
                values = values if isinstance(values, list) else [values]
                evaluations.extend(
                    value if isinstance(value, dict) else vars(value) for value in values
                )
            scored.append({"item_id": item.id, "output": output, "evaluations": evaluations})
        self.experiments.append({**kwargs, "scored": scored})
        return SimpleNamespace(dataset_run_id="run-1", item_results=scored)

    def flush(self) -> None:
        self.flushes += 1


def _case(run=lambda: True, check=bool) -> EvalCase:
    return EvalCase(
        id="stable-case",
        category=EvalCategory.REGRESSION,
        guards="D16 / §8",
        description="a stable synthetic eval",
        expected="the case passes",
        run=run,
        check=check,
    )


def _result(*, passed: bool = True) -> EvalResult:
    return EvalResult(
        case_id="stable-case",
        category="regression",
        guards="D16 / §8",
        passed=passed,
        detail="the case passes" if passed else "expected: the case passes",
    )


def _configured_env() -> dict[str, str]:
    return {
        "LANGFUSE_PUBLIC_KEY": "pk-test",
        "LANGFUSE_SECRET_KEY": "sk-test",
        "LANGFUSE_HOST": "https://langfuse.example.test",
        "GITHUB_SHA": "abc123",
    }


def test_no_keys_keeps_runner_exit_and_export_shape_without_sdk_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = _result()
    exported: list[dict] = []
    client_calls = 0

    async def fake_run_all() -> list[EvalResult]:
        return [result]

    def forbidden_client_factory(**_kwargs):
        nonlocal client_calls
        client_calls += 1
        raise AssertionError("Langfuse SDK must not be constructed without all three keys")

    def fake_export(summary: dict, path: str | Path = "evals/results.json") -> Path:
        exported.append(summary)
        return tmp_path / "results.json"

    for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(runner, "run_all", fake_run_all)
    monkeypatch.setattr(runner, "export", fake_export)
    monkeypatch.setattr(
        "evals.langfuse_publish._default_client_factory", forbidden_client_factory
    )

    assert runner.main() == 0
    assert client_calls == 0
    assert exported == [{
        "total": 1,
        "passed": 1,
        "failed": 0,
        "by_category": {"regression": {"passed": 1, "failed": 0}},
        "cases": [{
            "case_id": "stable-case",
            "category": "regression",
            "guards": "D16 / §8",
            "passed": True,
            "detail": "the case passes",
        }],
    }]


def test_configured_publish_upserts_stable_items_and_scores_one_trace_per_case() -> None:
    first = _RecordingLangfuse()
    second = _RecordingLangfuse()
    runs = 0

    def run_brief() -> BriefResult:
        nonlocal runs
        runs += 1
        return BriefResult(
            text="verified synthetic answer",
            source="llm",
            degraded=False,
            usage=Usage(),
            iterations=1,
            verdicts=["pass", "flagged", "blocked"],
        )

    case = _case(run_brief, lambda outcome: outcome.source == "llm")

    assert publish_if_configured(
        [case], [_result(passed=False)], env=_configured_env(), client_factory=lambda **_: first
    ) is True
    assert publish_if_configured(
        [case], [_result(passed=False)], env=_configured_env(), client_factory=lambda **_: second
    ) is True

    assert runs == 2  # the experiment task reruns the case; it does not replay EvalResult
    assert len(first.datasets) == len(first.items) == len(first.experiments) == 1
    assert first.items[0]["id"] == second.items[0]["id"]
    assert first.items[0]["input"]["case_id"] == "stable-case"
    assert first.items[0]["expected_output"]["passed"] is True
    assert first.experiments[0]["run_name"] == "eval-gate-abc123"
    assert len(first.experiments[0]["data"]) == 1
    assert first.experiments[0]["scored"] == [{
        "item_id": first.items[0]["id"],
        "output": {
            "case_id": "stable-case",
            "category": "regression",
            "guards": "D16 / §8",
            "passed": True,
            "detail": "the case passes",
            "claims_submitted": 3,
            "claims_verified": 2,
            "claims_dropped": 1,
            "verification_drop_rate": 1 / 3,
            "source": "llm",
            "degraded": False,
        },
        "evaluations": [
            {"name": "claims_submitted", "value": 3, "comment": None,
             "metadata": None, "data_type": None, "config_id": None},
            {"name": "claims_verified", "value": 2, "comment": None,
             "metadata": None, "data_type": None, "config_id": None},
            {"name": "claims_dropped", "value": 1, "comment": None,
             "metadata": None, "data_type": None, "config_id": None},
            {"name": "verification_drop_rate", "value": 1 / 3, "comment": None,
             "metadata": None, "data_type": None, "config_id": None},
            {"name": "source", "value": "llm", "comment": None,
             "metadata": None, "data_type": "CATEGORICAL", "config_id": None},
            {"name": "degraded", "value": False, "comment": None,
             "metadata": None, "data_type": "BOOLEAN", "config_id": None},
            {"name": "offline_gate_passed", "value": True, "comment": "the case passes",
             "metadata": None, "data_type": "BOOLEAN", "config_id": None},
        ],
    }]
    assert first.flushes == 1


def test_non_brief_outcome_uses_explicit_not_applicable_scores() -> None:
    assert _outcome_metrics(True) == {
        "claims_submitted": 0,
        "claims_verified": 0,
        "claims_dropped": 0,
        "verification_drop_rate": 0.0,
        "source": "not_applicable",
        "degraded": False,
    }


@pytest.mark.parametrize(("passed", "expected_exit"), [(True, 0), (False, 1)])
def test_langfuse_outage_never_changes_offline_gate_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    passed: bool,
    expected_exit: int,
) -> None:
    result = _result(passed=passed)

    async def fake_run_all() -> list[EvalResult]:
        return [result]

    def failed_publish(*_args, **_kwargs):
        raise RuntimeError("Langfuse unavailable")

    monkeypatch.setattr(runner, "run_all", fake_run_all)
    monkeypatch.setattr(runner, "export", lambda _summary: tmp_path / "results.json")
    monkeypatch.setattr(runner, "publish_if_configured", failed_publish)

    assert runner.main() == expected_exit
