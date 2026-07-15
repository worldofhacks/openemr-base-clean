"""Dedicated Railway process for durable document jobs (W2-D1/D9; §3).

The FastAPI process only persists source bytes and enqueues a job.  This module is
the separately deployed claimant: it uses the production composition factory and
the processor's graceful boundary, so SIGTERM stops before the next claim without
cancelling an in-flight clinical operation.
"""

from __future__ import annotations

import asyncio
import signal

from app.config import get_settings
from app.ingestion.processor import run_worker
from app.service import build_document_processor


def heartbeat_interval(lease_seconds: int) -> float:
    """Pulse at least three times per lease, capped to avoid needless DB traffic."""

    if lease_seconds <= 0:
        raise ValueError("worker lease must be positive")
    return min(10.0, float(lease_seconds) / 3.0)


def _install_stop_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
        except (NotImplementedError, RuntimeError):
            # Signal handlers are unavailable on some local platforms. Railway's
            # Linux runtime supports them; the processor still has a safe boundary.
            pass


async def serve(*, stop_event: asyncio.Event | None = None) -> None:
    """Build the real runtime and claim jobs until the process is asked to stop."""

    settings = get_settings()
    processor = await build_document_processor()
    stop = stop_event or asyncio.Event()
    if stop_event is None:
        _install_stop_handlers(stop)
    await run_worker(
        processor,
        poll_seconds=settings.document_worker_poll_seconds,
        heartbeat_seconds=heartbeat_interval(settings.document_worker_lease_seconds),
        stop_event=stop,
    )


def main() -> int:
    asyncio.run(serve())
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the Railway process
    raise SystemExit(main())
