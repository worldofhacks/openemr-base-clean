"""Deployment contract for the dedicated W2 document worker (W2-D1/D9; §3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_worker_uses_real_factory_config_and_graceful_stop(monkeypatch) -> None:
    import app.ingestion.worker as worker_module

    processor = object()
    stop = worker_module.asyncio.Event()
    calls: list[tuple[object, dict[str, object]]] = []

    async def build():
        return processor

    async def run(built, **kwargs) -> None:
        calls.append((built, kwargs))

    settings = type(
        "Settings",
        (),
        {
            "document_worker_poll_seconds": 2.5,
            "document_worker_lease_seconds": 30,
        },
    )()
    monkeypatch.setattr(worker_module, "build_document_processor", build)
    monkeypatch.setattr(worker_module, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_module, "run_worker", run)

    await worker_module.serve(stop_event=stop)

    assert calls == [
        (
            processor,
            {
                "poll_seconds": 2.5,
                "heartbeat_seconds": 10.0,
                "stop_event": stop,
            },
        )
    ]


@pytest.mark.parametrize(
    ("lease_seconds", "expected"),
    [(60, 10.0), (30, 10.0), (3, 1.0), (1, 1 / 3)],
)
def test_worker_heartbeat_interval_remains_inside_lease(
    lease_seconds: int, expected: float
) -> None:
    from app.ingestion.worker import heartbeat_interval

    assert heartbeat_interval(lease_seconds) == pytest.approx(expected)


def test_worker_service_definition_has_exact_non_http_start_command() -> None:
    agent_root = Path(__file__).resolve().parents[1]
    definition = json.loads((agent_root / "railway.worker.json").read_text())

    assert definition["build"] == {
        "builder": "DOCKERFILE",
        "dockerfilePath": "Dockerfile",
    }
    assert definition["deploy"]["startCommand"] == "python -m app.ingestion.worker"
    assert definition["deploy"]["restartPolicyType"] == "ON_FAILURE"
    assert definition["deploy"]["numReplicas"] == 1
    assert "healthcheckPath" not in definition["deploy"]


def test_web_service_uses_hard_dependency_readiness_for_rotation() -> None:
    agent_root = Path(__file__).resolve().parents[1]
    definition = json.loads((agent_root / "railway.json").read_text())

    assert definition["deploy"]["healthcheckPath"] == "/ready"
    assert definition["deploy"]["healthcheckTimeout"] == 60


def test_procfile_keeps_web_enqueue_only_and_worker_separate() -> None:
    agent_root = Path(__file__).resolve().parents[1]
    processes = dict(
        line.split(": ", maxsplit=1)
        for line in (agent_root / "Procfile").read_text().splitlines()
        if line.strip()
    )

    assert processes == {
        "web": (
            "sh -c 'uvicorn app.main:app --host 0.0.0.0 "
            "--port ${PORT:-8000} --no-access-log'"
        ),
        "worker": "python -m app.ingestion.worker",
    }
    assert "ingestion.worker" not in processes["web"]
    assert "--no-access-log" in processes["web"]
