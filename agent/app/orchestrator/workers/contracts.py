"""Frozen supervisor/worker callable seam (W2-D2, architecture §2)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.schemas.workers import WorkerInput, WorkerOutput


WorkerCallable = Callable[[WorkerInput], Awaitable[WorkerOutput]]
