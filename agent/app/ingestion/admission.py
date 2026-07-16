"""Bounded, content-free admission for patient-pinned document submissions.

The durable document repository remains the exactly-once authority.  This meter is a
process-local overload guard that runs before the upload coordinator can construct a
remote writer.  Meter keys are keyed hashes, so neither opaque session handles nor
clinician subjects are retained in process diagnostics by this component.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import math
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import AsyncIterator, Callable


Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class UploadAdmissionLimits:
    """Conservative serving defaults; tests may inject smaller closed limits."""

    session_daily_count: int = 20
    session_daily_bytes: int = 100 * 1024 * 1024
    clinician_daily_count: int = 100
    clinician_daily_bytes: int = 500 * 1024 * 1024
    per_session_concurrent: int = 1
    global_concurrent: int = 4
    global_outstanding_jobs: int = 100
    max_daily_meter_keys: int = 20_000

    def __post_init__(self) -> None:
        if any(
            value <= 0
            for value in (
                self.session_daily_count,
                self.session_daily_bytes,
                self.clinician_daily_count,
                self.clinician_daily_bytes,
                self.per_session_concurrent,
                self.global_concurrent,
                self.global_outstanding_jobs,
                self.max_daily_meter_keys,
            )
        ):
            raise ValueError("document admission limits must be positive")
        if self.per_session_concurrent > self.global_concurrent:
            raise ValueError("per-session concurrency cannot exceed global concurrency")


class UploadQuotaExceeded(RuntimeError):
    """A caller-scoped daily or concurrent bound was reached."""

    def __init__(self, *, retry_after_seconds: int) -> None:
        super().__init__("document upload quota exceeded")
        self.retry_after_seconds = max(1, retry_after_seconds)


class UploadCapacityExceeded(RuntimeError):
    """A process-wide or durable-workload capacity bound was reached."""


@dataclass
class _Usage:
    count: int = 0
    byte_count: int = 0


class UploadAdmissionLease:
    """One admitted request; quota can be refunded for a deduplication race."""

    def __init__(
        self,
        controller: UploadAdmissionController,
        *,
        day: object,
        session_key: bytes,
        clinician_key: bytes,
        byte_count: int,
        charged: bool,
    ) -> None:
        self._controller = controller
        self._day = day
        self._session_key = session_key
        self._clinician_key = clinician_key
        self._byte_count = byte_count
        self._charged = charged

    async def refund_quota(self) -> None:
        """Idempotently undo a charge when permanent dedup wins a race."""

        if not self._charged:
            return
        await self._controller._refund(  # noqa: SLF001 - lease is controller-owned
            day=self._day,
            session_key=self._session_key,
            clinician_key=self._clinician_key,
            byte_count=self._byte_count,
        )
        self._charged = False


class UploadAdmissionController:
    """Per-process quotas plus a repository-backed global workload ceiling."""

    def __init__(
        self,
        *,
        limits: UploadAdmissionLimits | None = None,
        now: Clock | None = None,
        hash_key: bytes | None = None,
    ) -> None:
        self._limits = limits or UploadAdmissionLimits()
        self._now = now or _utcnow
        self._hash_key = hash_key or secrets.token_bytes(32)
        if len(self._hash_key) < 16:
            raise ValueError("document admission hash key is too short")
        self._lock = asyncio.Lock()
        initial = self._aware_now()
        self._day = initial.date()
        self._session_usage: dict[bytes, _Usage] = {}
        self._clinician_usage: dict[bytes, _Usage] = {}
        self._active_by_session: dict[bytes, int] = {}
        self._global_active = 0
        self._new_inflight = 0

    @asynccontextmanager
    async def admit(
        self,
        *,
        session_id: str,
        clinician_sub: str,
        byte_count: int,
        duplicate: bool,
        outstanding_jobs: int,
    ) -> AsyncIterator[UploadAdmissionLease]:
        """Reserve caller/global capacity before entering any remote-write path."""

        if not session_id or not clinician_sub:
            raise ValueError("document admission principals must not be empty")
        if byte_count < 0 or outstanding_jobs < 0:
            raise ValueError("document admission counts must not be negative")

        session_key = self._digest(b"session", session_id)
        clinician_key = self._digest(b"clinician", clinician_sub)
        charged = not duplicate
        async with self._lock:
            now = self._roll_day()
            retry_after = self._retry_after(now)
            active_for_session = self._active_by_session.get(session_key, 0)
            if active_for_session >= self._limits.per_session_concurrent:
                raise UploadQuotaExceeded(retry_after_seconds=1)
            if self._global_active >= self._limits.global_concurrent:
                raise UploadCapacityExceeded("document upload capacity unavailable")
            if charged:
                if (
                    outstanding_jobs + self._new_inflight
                    >= self._limits.global_outstanding_jobs
                ):
                    raise UploadCapacityExceeded("document upload capacity unavailable")
                session_usage = self._session_usage.get(session_key, _Usage())
                clinician_usage = self._clinician_usage.get(clinician_key, _Usage())
                if (
                    session_usage.count + 1 > self._limits.session_daily_count
                    or session_usage.byte_count + byte_count
                    > self._limits.session_daily_bytes
                    or clinician_usage.count + 1
                    > self._limits.clinician_daily_count
                    or clinician_usage.byte_count + byte_count
                    > self._limits.clinician_daily_bytes
                ):
                    raise UploadQuotaExceeded(retry_after_seconds=retry_after)
                new_keys = int(session_key not in self._session_usage) + int(
                    clinician_key not in self._clinician_usage
                )
                if (
                    len(self._session_usage)
                    + len(self._clinician_usage)
                    + new_keys
                    > self._limits.max_daily_meter_keys
                ):
                    raise UploadCapacityExceeded("document upload capacity unavailable")
                self._charge(
                    session_key=session_key,
                    clinician_key=clinician_key,
                    byte_count=byte_count,
                )
                self._new_inflight += 1
            self._active_by_session[session_key] = active_for_session + 1
            self._global_active += 1
            lease = UploadAdmissionLease(
                self,
                day=self._day,
                session_key=session_key,
                clinician_key=clinician_key,
                byte_count=byte_count,
                charged=charged,
            )

        try:
            yield lease
        finally:
            async with self._lock:
                active = self._active_by_session.get(session_key, 0)
                if active <= 1:
                    self._active_by_session.pop(session_key, None)
                else:
                    self._active_by_session[session_key] = active - 1
                self._global_active = max(0, self._global_active - 1)
                if charged:
                    self._new_inflight = max(0, self._new_inflight - 1)

    def _aware_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            raise ValueError("document admission clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    def _roll_day(self) -> datetime:
        now = self._aware_now()
        if now.date() != self._day:
            self._day = now.date()
            self._session_usage.clear()
            self._clinician_usage.clear()
        return now

    @staticmethod
    def _retry_after(now: datetime) -> int:
        tomorrow = datetime.combine(
            now.date() + timedelta(days=1), time.min, tzinfo=timezone.utc
        )
        return max(1, math.ceil((tomorrow - now).total_seconds()))

    def _digest(self, domain: bytes, value: str) -> bytes:
        return hmac.new(
            self._hash_key,
            domain + b"\x00" + value.encode("utf-8"),
            hashlib.sha256,
        ).digest()

    def _charge(
        self, *, session_key: bytes, clinician_key: bytes, byte_count: int
    ) -> None:
        session_usage = self._session_usage.setdefault(session_key, _Usage())
        session_usage.count += 1
        session_usage.byte_count += byte_count
        clinician_usage = self._clinician_usage.setdefault(clinician_key, _Usage())
        clinician_usage.count += 1
        clinician_usage.byte_count += byte_count

    async def _refund(
        self,
        *,
        day: object,
        session_key: bytes,
        clinician_key: bytes,
        byte_count: int,
    ) -> None:
        async with self._lock:
            if day != self._day:
                return
            self._refund_usage(self._session_usage, session_key, byte_count)
            self._refund_usage(self._clinician_usage, clinician_key, byte_count)

    @staticmethod
    def _refund_usage(
        usage_by_key: dict[bytes, _Usage], key: bytes, byte_count: int
    ) -> None:
        usage = usage_by_key.get(key)
        if usage is None:
            return
        usage.count = max(0, usage.count - 1)
        usage.byte_count = max(0, usage.byte_count - byte_count)
        if usage.count == 0 and usage.byte_count == 0:
            usage_by_key.pop(key, None)
