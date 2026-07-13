"""Best-effort Langfuse dataset publishing for the deterministic eval gate (D16 / §8).

The offline runner remains authoritative. Publishing is additive observability only: missing
configuration is a true no-op, stable case ids upsert dataset items, and any SDK/API failure
is contained so it cannot turn a green gate red or a red gate green.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from app.orchestrator.loop import BriefResult
from evals.schema import EvalCase, EvalResult, run_case_with_outcome


_DEFAULT_DATASET_NAME = "clinical-copilot-offline-evals"
_REQUIRED_ENV = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST")


def _default_client_factory(**kwargs: Any):
    """Import the optional sink only after complete configuration is present."""
    from langfuse import Langfuse

    return Langfuse(**kwargs)


def _configured(env: Mapping[str, str]) -> bool:
    return all(str(env.get(key, "")).strip() for key in _REQUIRED_ENV)


def _stable_item_id(dataset_name: str, case_id: str) -> str:
    """Globally stable 128-bit id: repeat runs upsert rather than duplicate a case."""
    seed = f"{dataset_name}\0{case_id}".encode("utf-8")
    return hashlib.sha256(seed).hexdigest()[:32]


def _run_name(env: Mapping[str, str]) -> str:
    override = str(env.get("LANGFUSE_EVAL_RUN_NAME", "")).strip()
    if override:
        return override
    commit = str(env.get("GITHUB_SHA", "")).strip()
    if commit:
        return f"eval-gate-{commit}"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"eval-gate-{timestamp}"


def _outcome_metrics(outcome: Any) -> dict[str, int | float | str | bool]:
    """Derive the Task-2 verifier scores from a freshly executed eval outcome."""
    if not isinstance(outcome, BriefResult):
        return {
            "claims_submitted": 0,
            "claims_verified": 0,
            "claims_dropped": 0,
            "verification_drop_rate": 0.0,
            "source": "not_applicable",
            "degraded": False,
        }

    verdicts = [str(verdict).split(":", 1)[0] for verdict in outcome.verdicts]
    submitted = len(verdicts)
    verified = sum(verdict in {"pass", "flagged"} for verdict in verdicts)
    dropped = submitted - verified
    return {
        "claims_submitted": submitted,
        "claims_verified": verified,
        "claims_dropped": dropped,
        "verification_drop_rate": dropped / submitted if submitted else 0.0,
        "source": "llm" if outcome.source == "llm" else "fallback",
        "degraded": bool(outcome.degraded),
    }


def publish_if_configured(
    cases: Sequence[EvalCase],
    results: Sequence[EvalResult],
    *,
    env: Mapping[str, str] | None = None,
    client_factory: Callable[..., Any] | None = None,
) -> bool:
    """Publish one scored Langfuse experiment trace per offline eval case.

    Returns ``True`` only when the dataset, all item upserts, and the experiment submission
    complete. It intentionally never raises: Langfuse is a §6 soft dependency and the local
    ``EvalResult`` list remains the sole deploy-gate input.
    """
    config = os.environ if env is None else env
    if not _configured(config):
        return False

    factory = _default_client_factory if client_factory is None else client_factory
    client = None
    try:
        client = factory(
            public_key=str(config["LANGFUSE_PUBLIC_KEY"]).strip(),
            secret_key=str(config["LANGFUSE_SECRET_KEY"]).strip(),
            base_url=str(config["LANGFUSE_HOST"]).strip().rstrip("/"),
            environment="eval",
        )
        dataset_name = str(config.get("LANGFUSE_EVAL_DATASET_NAME", "")).strip()
        dataset_name = dataset_name or _DEFAULT_DATASET_NAME
        client.create_dataset(
            name=dataset_name,
            description=(
                "Deterministic synthetic Clinical Co-Pilot deploy-gate cases "
                "(ARCHITECTURE §8, D16)."
            ),
            metadata={"source": "offline-eval-gate", "synthetic_only": True},
        )

        results_by_case = {result.case_id: result for result in results}
        case_by_item_id: dict[str, EvalCase] = {}
        dataset_items: list[Any] = []
        for case in cases:
            result = results_by_case.get(case.id)
            if result is None:
                continue
            item_id = _stable_item_id(dataset_name, case.id)
            item = client.create_dataset_item(
                dataset_name=dataset_name,
                id=item_id,
                input={
                    "case_id": case.id,
                    "category": case.category.value,
                    "guards": case.guards,
                    "description": case.description,
                },
                expected_output={"passed": True, "expected": case.expected},
                metadata={"synthetic_only": True, "schema_version": 1},
            )
            dataset_items.append(item)
            case_by_item_id[item_id] = case

        if not dataset_items:
            client.flush()
            return True

        async def task(*, item: Any, **_kwargs: Any) -> dict[str, Any]:
            item_id = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
            execution = await run_case_with_outcome(case_by_item_id[item_id])
            return {**asdict(execution.result), **_outcome_metrics(execution.outcome)}

        def offline_gate_evaluator(
            *, output: Mapping[str, Any], **_kwargs: Any
        ) -> list[Any]:
            # Langfuse v4's typed Evaluation is required for non-numeric score types.
            from langfuse import Evaluation

            return [
                Evaluation(name="claims_submitted", value=int(output["claims_submitted"])),
                Evaluation(name="claims_verified", value=int(output["claims_verified"])),
                Evaluation(name="claims_dropped", value=int(output["claims_dropped"])),
                Evaluation(
                    name="verification_drop_rate",
                    value=float(output["verification_drop_rate"]),
                ),
                Evaluation(
                    name="source", value=str(output["source"]), data_type="CATEGORICAL"
                ),
                Evaluation(
                    name="degraded", value=bool(output["degraded"]), data_type="BOOLEAN"
                ),
                Evaluation(
                    name="offline_gate_passed",
                    value=bool(output["passed"]),
                    comment=str(output["detail"]),
                    data_type="BOOLEAN",
                ),
            ]

        commit = str(config.get("GITHUB_SHA", "")).strip()
        run_metadata = {"source": "offline-eval-gate", "synthetic_only": "true"}
        if commit:
            run_metadata["commit"] = commit
        client.run_experiment(
            name="Clinical Co-Pilot offline eval gate",
            run_name=_run_name(config),
            description="One traced and scored Langfuse dataset item per deterministic eval case.",
            data=dataset_items,
            task=task,
            evaluators=[offline_gate_evaluator],
            max_concurrency=1,
            metadata=run_metadata,
        )
        client.flush()
        return True
    except Exception:
        if client is not None:
            try:
                client.flush()
            except Exception:
                pass
        return False
