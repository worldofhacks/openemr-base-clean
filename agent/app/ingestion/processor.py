"""Claimed/leased document processor and injectable worker CLI (§3/§5)."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
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
        if claimed.attempt_count >= self._max_attempts:
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
    processor: DocumentProcessor, *, once: bool = False, poll_seconds: float = 1.0
) -> None:
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    while True:
        processed = await processor.process_once()
        if once:
            return
        if processed is None:
            await asyncio.sleep(poll_seconds)


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
    args = parser.parse_args(argv)

    async def start() -> None:
        processor = await _load_factory(args.factory)
        await run_worker(processor, once=args.once, poll_seconds=args.poll_seconds)

    asyncio.run(start())
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by deployed entry point
    raise SystemExit(main())
