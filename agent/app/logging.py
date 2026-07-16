"""Structured JSON logging (ARCHITECTURE.md §7, §3.1).

Logs are one JSON object per line, each carrying the active correlation id. The
message field is a short event name — variable context goes in structured fields,
never interpolated into the message (PSR-3-style discipline; keeps PHI out of the
free-text message). Observability is wired here from the first boot, not
retrofitted (§7).
"""

from __future__ import annotations

import json
import logging
import sys
from typing import TextIO

_CONFIGURED = False
_SUPPRESSED_THIRD_PARTY_PREFIXES = (
    "anthropic",
    "asyncpg",
    "httpcore",
    "httpx",
    "urllib3",
    "uvicorn.access",
)


class _DropUnsafeThirdPartyLogs(logging.Filter):
    """The application emits its own content-free dependency events."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith(_SUPPRESSED_THIRD_PARTY_PREFIXES)


class JsonFormatter(logging.Formatter):
    """Render each record as a single-line JSON object with the correlation id."""

    # Standard LogRecord attributes we do not want duplicated into the JSON.
    _RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        # Import here to avoid a circular import at module load.
        from app.middleware.correlation import correlation_id_var

        payload: dict[str, object] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id_var.get(),
        }
        # Merge any explicit structured context passed via `extra=`.
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_") and key not in payload:
                payload[key] = value
        if record.exc_info:
            # Exception text and tracebacks can contain URLs, queries, or provider
            # payloads. Preserve only the exception class for operational grouping.
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, default=str)


def configure_logging(stream: TextIO | None = None, level: int = logging.INFO) -> None:
    """Install the JSON handler on the root logger (idempotent per stream)."""
    global _CONFIGURED
    root = logging.getLogger()
    root.setLevel(level)
    # Replace existing handlers so tests can redirect the stream deterministically.
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(_DropUnsafeThirdPartyLogs())
    root.addHandler(handler)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name)
