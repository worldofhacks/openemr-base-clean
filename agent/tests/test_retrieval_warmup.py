"""R07 — deployed reranker readiness: startup warms the retrieval models.

A cold container paid the first-use ONNX model load (and, before the image
pre-bake, a multi-hundred-MB download) inside the `active_reranker` soft-probe
budget, so cache-busted `/ready` intermittently reported
`active_reranker: timeout`. These tests pin the startup warmup seam:

- `AgentServices.startup` spawns a daemon warmup thread when `RETRIEVAL_WARMUP`
  is truthy (the deploy image sets it) and the warmup runs the same synthetic,
  non-clinical search the readiness probe runs (`"hypertension"`, k=2).
- Default-off: local/test boots stay model-free (W2-D4 invariant).
- Warmup is best-effort: a model-load failure never raises and never blocks
  boot — the soft readiness probe owns failure reporting.

The Dockerfile pre-bake half of R07 is pinned structurally below; the build
itself is the functional verification for the baked weights.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app.service as service_module


def _bare_services() -> "service_module.AgentServices":
    """Minimal AgentServices for startup() (same shape as the migrations tests)."""

    class Sessions:
        async def ensure_schema(self) -> None:
            return None

    services = object.__new__(service_module.AgentServices)
    services.sessions = Sessions()
    services.settings = type("Settings", (), {"w2_document_runtime_enabled": False})()
    services._retrieval_warmup_thread = None
    return services


@pytest.mark.asyncio
async def test_startup_spawns_daemon_warmup_running_the_probe_query(monkeypatch):
    """RETRIEVAL_WARMUP=1 => startup spawns a daemon thread that runs the exact
    synthetic search `probe_active_reranker` runs, off the boot path."""
    monkeypatch.setenv("RETRIEVAL_WARMUP", "1")
    services = _bare_services()

    calls: list[tuple[str, int]] = []

    class FakeRetriever:
        def search(self, query: str, *, k: int, demographic_strings=()):
            calls.append((query, k))
            return object()

    services.get_evidence_retriever = lambda: FakeRetriever()

    await services.startup()

    thread = services._retrieval_warmup_thread
    assert thread is not None, "startup must spawn the retrieval warmup thread"
    assert thread.daemon is True, "warmup must never block interpreter shutdown"
    thread.join(timeout=5.0)
    assert not thread.is_alive(), "warmup must complete promptly with a fake model"
    assert calls == [("hypertension", 2)], (
        "warmup must run the same synthetic non-clinical search as the "
        "active_reranker readiness probe"
    )


@pytest.mark.asyncio
async def test_startup_skips_warmup_by_default(monkeypatch):
    """No RETRIEVAL_WARMUP => boot stays model-free (tests, local dev, evals)."""
    monkeypatch.delenv("RETRIEVAL_WARMUP", raising=False)
    services = _bare_services()

    touched: list[bool] = []
    services.get_evidence_retriever = lambda: touched.append(True)

    await services.startup()

    assert services._retrieval_warmup_thread is None
    assert touched == [], "default-off warmup must never build the retriever"


@pytest.mark.asyncio
async def test_startup_skips_warmup_on_falsey_value(monkeypatch):
    """An explicit disable ('0') is honored, not just an unset variable."""
    monkeypatch.setenv("RETRIEVAL_WARMUP", "0")
    services = _bare_services()

    touched: list[bool] = []
    services.get_evidence_retriever = lambda: touched.append(True)

    await services.startup()

    assert services._retrieval_warmup_thread is None
    assert touched == []


@pytest.mark.asyncio
async def test_warmup_failure_never_raises_or_blocks_boot(monkeypatch):
    """A model-load failure inside warmup is swallowed (best-effort): the soft
    readiness probe owns reporting; boot must never crash on a warm miss."""
    monkeypatch.setenv("RETRIEVAL_WARMUP", "true")
    services = _bare_services()

    def boom():
        raise RuntimeError("synthetic model load failure")

    services.get_evidence_retriever = boom

    await services.startup()

    thread = services._retrieval_warmup_thread
    assert thread is not None
    thread.join(timeout=5.0)
    assert not thread.is_alive(), "warmup thread must terminate after a failure"


def test_dockerfile_prebakes_pinned_weights_and_enables_warmup():
    """Structural pin for the image half of R07: the Dockerfile bakes the model
    cache from the runtime pins in corpus/retrieval.py (no drift possible) and
    opts the deployed process into the startup warmup."""
    dockerfile = (
        Path(__file__).resolve().parents[1] / "Dockerfile"
    ).read_text(encoding="utf-8")

    assert "ENV FASTEMBED_CACHE_DIR=" in dockerfile, (
        "the image must fix the model cache location the runtime reads"
    )
    assert "snapshot_download" in dockerfile, (
        "the image must download the pinned weights at build time"
    )
    assert "corpus.retrieval" in dockerfile or "corpus import retrieval" in dockerfile, (
        "the bake must import the runtime pins (repo/revision/onnx filenames) "
        "from corpus/retrieval.py instead of duplicating them"
    )
    assert "ENV RETRIEVAL_WARMUP=1" in dockerfile, (
        "the deployed image must opt into the startup retrieval warmup"
    )
