"""Claimed/leased document processor and injectable worker CLI (§3/§5)."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import signal
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from app.ingestion.pipeline import DocumentExtractionPipeline, PipelineFailure
from app.ingestion.repository import DocumentRecord, DocumentRepository
from app.schemas.documents import FailureReason


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DocumentProcessor:
    """Process one durable job at a time without coupling work to the upload request."""

    def __init__(
        self,
        *,
        repository: DocumentRepository,
        pipeline: DocumentExtractionPipeline,
        worker_id: str,
        lease_seconds: int = 60,
        max_attempts: int = 3,
        base_backoff_seconds: int = 5,
        now: Callable[[], datetime] | None = None,
        worker_heartbeat: Callable[[str], object] | None = None,
    ) -> None:
        if lease_seconds <= 0 or max_attempts <= 0 or base_backoff_seconds <= 0:
            raise ValueError("lease, attempt, and backoff bounds must be positive")
        self._repository = repository
        self._pipeline = pipeline
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._base_backoff_seconds = base_backoff_seconds
        self._now = now or _utcnow
        self._worker_heartbeat = worker_heartbeat

    async def record_worker_heartbeat(self) -> None:
        """Publish process liveness independently of any claimed clinical job."""

        if self._worker_heartbeat is None:
            return
        result = self._worker_heartbeat(self._worker_id)
        if inspect.isawaitable(result):
            await result

    async def process_once(self) -> DocumentRecord | None:
        await self._repository.recover_stale()
        claimed = await self._repository.claim_next(
            self._worker_id, lease_seconds=self._lease_seconds
        )
        if claimed is None:
            return None

        async def on_stage(state: str) -> None:
            await self._repository.heartbeat(
                claimed.document_id,
                worker_id=self._worker_id,
                lease_seconds=self._lease_seconds,
            )
            await self._repository.transition_claimed(
                claimed.document_id, worker_id=self._worker_id, state=state
            )

        try:
            result = await self._pipeline.extract_document(
                claimed.document_id,
                patient_ref=f"patient:{claimed.patient_id}",
                correlation_id=claimed.correlation_id,
                on_stage=on_stage,
            )
        except PipelineFailure as exc:
            return await self._handle_failure(claimed, exc.reason)
        except Exception:
            return await self._handle_failure(claimed, FailureReason.WORKER_RESTART)
        return await self._repository.complete_claimed(
            claimed.document_id,
            worker_id=self._worker_id,
            fields_grounded=result.fields_grounded,
            fields_unsupported=result.fields_unsupported,
        )

    async def _handle_failure(
        self, claimed: DocumentRecord, reason: FailureReason
    ) -> DocumentRecord:
        if (
            reason
            in {
                FailureReason.AUTH_EXPIRED,
                FailureReason.PATIENT_MISMATCH,
                FailureReason.ENCOUNTER_MISMATCH,
            }
            or claimed.attempt_count >= self._max_attempts
        ):
            return await self._repository.fail_claimed(
                claimed.document_id, worker_id=self._worker_id, reason=reason
            )
        delay = self._base_backoff_seconds * (2 ** (claimed.attempt_count - 1))
        return await self._repository.reschedule_claimed(
            claimed.document_id,
            worker_id=self._worker_id,
            reason=reason,
            next_retry_at=self._now() + timedelta(seconds=delay),
        )


async def run_worker(
    processor: DocumentProcessor,
    *,
    once: bool = False,
    poll_seconds: float = 1.0,
    heartbeat_seconds: float = 10.0,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run the dedicated claimed-job worker with a graceful stop boundary.

    A stop request prevents the *next* claim but does not cancel an in-flight clinical
    step.  A separate pulse keeps readiness fresh while extraction/VLM work is running.
    """

    if poll_seconds <= 0 or heartbeat_seconds <= 0:
        raise ValueError("poll and heartbeat intervals must be positive")
    stop = stop_event or asyncio.Event()
    pulse_stop = asyncio.Event()
    await processor.record_worker_heartbeat()

    async def pulse() -> None:
        while not pulse_stop.is_set():
            try:
                await asyncio.wait_for(pulse_stop.wait(), timeout=heartbeat_seconds)
            except TimeoutError:
                await processor.record_worker_heartbeat()

    pulse_task = asyncio.create_task(pulse(), name="document-worker-heartbeat")
    try:
        while not stop.is_set():
            processed = await processor.process_once()
            if once:
                return
            if processed is None and not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
                except TimeoutError:
                    pass
    finally:
        pulse_stop.set()
        await pulse_task


async def _load_factory(path: str) -> DocumentProcessor:
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("factory must use module:function syntax")
    factory = getattr(importlib.import_module(module_name), attribute)
    built = factory()
    if inspect.isawaitable(built):
        built = await built
    if not isinstance(built, DocumentProcessor):
        raise TypeError("worker factory must return DocumentProcessor")
    return built


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the B2 document extraction worker"
    )
    parser.add_argument(
        "--factory", required=True, help="module:function wiring factory"
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=10.0)
    args = parser.parse_args(argv)

    async def start() -> None:
        processor = await _load_factory(args.factory)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, stop.set)
            except (NotImplementedError, RuntimeError):
                pass
        await run_worker(
            processor,
            once=args.once,
            poll_seconds=args.poll_seconds,
            heartbeat_seconds=args.heartbeat_seconds,
            stop_event=stop,
        )

    asyncio.run(start())
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by deployed entry point
    raise SystemExit(main())
