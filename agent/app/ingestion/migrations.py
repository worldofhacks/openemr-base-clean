"""Idempotent document-runtime schema bootstrap (W2-D10; §3)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

_MIGRATIONS = ("003_document_jobs.sql", "004_extraction_refs.sql")


async def apply_document_migrations(
    connect: Callable[[], Awaitable[object]],
) -> None:
    """Apply the two append-only W2 schemas in dependency order.

    Both SQL files use ``CREATE ... IF NOT EXISTS`` and own their transactions, so this
    is safe across concurrent web/worker startup. Diagnostics contain no row values.
    """

    root = Path(__file__).resolve().parents[2] / "migrations"
    connection = await connect()
    try:
        for filename in _MIGRATIONS:
            sql = (root / filename).read_text(encoding="utf-8")
            await connection.execute(sql)  # type: ignore[attr-defined]
    finally:
        close = getattr(connection, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result
