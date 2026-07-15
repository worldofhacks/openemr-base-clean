"""Per-turn opaque reference registry for canonical worker handoffs.

The registry is discarded with the graph turn. Persistent OpenEMR extraction refs
remain external authority; this registry only makes the audited WorkerInput and
WorkerOutput envelopes trace-resolvable without moving raw clinical values across
the supervisor boundary.

Traceability: W2-D2; W2_ARCHITECTURE.md §2/§3.
"""

from __future__ import annotations

import re
from typing import Protocol


_KIND = re.compile(r"^[a-z][a-z0-9-]{0,47}$")


class RefResolver(Protocol):
    def put(self, value: object, *, kind: str) -> str: ...

    def resolve(self, ref: str) -> object: ...


class PersistentRefResolver(Protocol):
    """Read-only persistent authority; a miss is represented by ``None``."""

    def resolve(self, ref: str) -> object | None: ...


class TurnRefRegistry:
    """Small deterministic ref map scoped to one correlation id and graph turn."""

    def __init__(self, correlation_id: str):
        if not correlation_id:
            raise ValueError("correlation_id is required")
        self._correlation_id = correlation_id
        self._values: dict[str, object] = {}
        self._counter = 0

    def put(self, value: object, *, kind: str) -> str:
        if _KIND.fullmatch(kind) is None:
            raise ValueError("ref kind must be a fixed non-PHI label")
        self._counter += 1
        ref = f"trace:{self._correlation_id}/ref/{self._counter}/{kind}"
        self._values[ref] = value
        return ref

    def resolve(self, ref: str) -> object:
        try:
            return self._values[ref]
        except KeyError as exc:
            raise KeyError("unresolvable graph reference") from exc


class CompositeRefResolver:
    """Keep graph writes turn-local while resolving durable extraction refs.

    The turn registry remains the only mutable graph authority. Persistent stores are
    warmed before the turn and are consulted read-only on a turn miss, so clinical
    artifacts never need to cross a refs-only worker handoff (W2-D2/D3; §2/§3).
    """

    def __init__(
        self,
        turn: TurnRefRegistry,
        *persistent: PersistentRefResolver,
    ) -> None:
        self._turn = turn
        self._persistent = persistent

    def put(self, value: object, *, kind: str) -> str:
        return self._turn.put(value, kind=kind)

    def resolve(self, ref: str) -> object:
        try:
            return self._turn.resolve(ref)
        except KeyError:
            pass
        for resolver in self._persistent:
            value = resolver.resolve(ref)
            if value is not None:
                return value
        raise KeyError("unresolvable graph reference")
