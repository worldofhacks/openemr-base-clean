#!/usr/bin/env python3
"""Poll Langfuse Cloud and deliver aggregate Clinical Co-Pilot alerts.

The checker reads Langfuse's current Observations API v2, groups observations by
``traceId``, evaluates the four §7 alert signals, and sends only aggregate values
to a configured webhook. It deliberately has no dependency on the serving app or
on the Langfuse SDK, so an SDK upgrade cannot disable the independent alert path.

No trace ids, prompts, patient data, credentials, API response bodies, or webhook
URLs are logged or sent in the notification payload.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping, Protocol, Sequence, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


_OBSERVATION_FIELDS = "core,basic,metadata,trace_context"
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_ERROR_STATES = frozenset({"error", "failed", "failure"})
_INACTIVE_FALLBACKS = frozenset({"", "none", "null"})


class ConfigError(ValueError):
    """The checker configuration is absent or unsafe."""


class QueryError(RuntimeError):
    """Langfuse data could not be retrieved or did not match the API contract."""


class NotificationError(RuntimeError):
    """A firing alert could not be delivered."""


class JsonTransport(Protocol):
    """Small HTTP seam used by the production urllib adapter and isolated tests."""

    def get_json(
        self, url: str, *, headers: dict[str, str], timeout: float
    ) -> dict: ...

    def post_json(self, url: str, payload: dict, *, timeout: float) -> None: ...


class ObservationClient(Protocol):
    def fetch_observations(self, start: datetime, end: datetime) -> list[dict]: ...


class Notifier(Protocol):
    def notify(self, alerts: list["Alert"], snapshot: "MetricsSnapshot") -> None: ...


@dataclass(frozen=True)
class Thresholds:
    p95_latency_ms: float
    request_error_rate: float
    tool_failure_rate: float
    llm_fallback_rate: float


@dataclass(frozen=True)
class Config:
    langfuse_base_url: str
    langfuse_public_key: str = field(repr=False)
    langfuse_secret_key: str = field(repr=False)
    webhook_url: str | None = field(repr=False)
    dry_run: bool
    thresholds: Thresholds
    interval_seconds: float
    window_minutes: int
    settle_delay_seconds: int
    http_timeout_seconds: float
    page_limit: int
    max_pages: int
    trace_name: str
    tool_prefix: str
    parallel_tool_prefix: str

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> "Config":
        env = os.environ if environ is None else environ
        base_url = _required_any(env, "LANGFUSE_BASE_URL", "LANGFUSE_HOST")
        public_key = _required(env, "LANGFUSE_PUBLIC_KEY")
        secret_key = _required(env, "LANGFUSE_SECRET_KEY")
        dry_run = _parse_bool(
            env.get("COPILOT_ALERT_DRY_RUN", "false"), "COPILOT_ALERT_DRY_RUN"
        )
        webhook_url = _optional(env, "COPILOT_ALERT_WEBHOOK_URL")
        if not dry_run and webhook_url is None:
            raise ConfigError(
                "COPILOT_ALERT_WEBHOOK_URL is required unless COPILOT_ALERT_DRY_RUN=true"
            )

        base_url = _validate_http_url(base_url, "LANGFUSE_BASE_URL", allow_path=False)
        if webhook_url is not None:
            webhook_url = _validate_http_url(
                webhook_url, "COPILOT_ALERT_WEBHOOK_URL", allow_path=True
            )

        thresholds = Thresholds(
            p95_latency_ms=_parse_float(
                env,
                "COPILOT_ALERT_P95_LATENCY_MS",
                15_000.0,
                minimum=0.0,
                exclusive=True,
            ),
            request_error_rate=_parse_rate(
                env, "COPILOT_ALERT_REQUEST_ERROR_RATE", 0.05
            ),
            tool_failure_rate=_parse_rate(env, "COPILOT_ALERT_TOOL_FAILURE_RATE", 0.10),
            llm_fallback_rate=_parse_rate(env, "COPILOT_ALERT_LLM_FALLBACK_RATE", 0.10),
        )
        page_limit = _parse_int(env, "COPILOT_ALERT_PAGE_LIMIT", 1_000, minimum=1)
        if page_limit > 1_000:
            raise ConfigError("COPILOT_ALERT_PAGE_LIMIT must be at most 1000")

        return cls(
            langfuse_base_url=base_url,
            langfuse_public_key=public_key,
            langfuse_secret_key=secret_key,
            webhook_url=webhook_url,
            dry_run=dry_run,
            thresholds=thresholds,
            interval_seconds=_parse_float(
                env, "COPILOT_ALERT_INTERVAL_SECONDS", 60.0, minimum=0.0, exclusive=True
            ),
            window_minutes=_parse_int(
                env, "COPILOT_ALERT_WINDOW_MINUTES", 15, minimum=1
            ),
            settle_delay_seconds=_parse_int(
                env, "COPILOT_ALERT_SETTLE_DELAY_SECONDS", 600, minimum=0
            ),
            http_timeout_seconds=_parse_float(
                env,
                "COPILOT_ALERT_HTTP_TIMEOUT_SECONDS",
                10.0,
                minimum=0.0,
                exclusive=True,
            ),
            page_limit=page_limit,
            max_pages=_parse_int(env, "COPILOT_ALERT_MAX_PAGES", 20, minimum=1),
            trace_name=_nonempty(
                env.get("COPILOT_ALERT_TRACE_NAME", "previsit-brief"),
                "COPILOT_ALERT_TRACE_NAME",
            ),
            tool_prefix=_prefix_csv(
                env.get("COPILOT_ALERT_TOOL_PREFIXES")
                or env.get("COPILOT_ALERT_TOOL_PREFIX")
                or "fhir.,tool.",
                "COPILOT_ALERT_TOOL_PREFIXES",
            ),
            parallel_tool_prefix=_nonempty(
                env.get("COPILOT_ALERT_PARALLEL_TOOL_PREFIX", "fhir."),
                "COPILOT_ALERT_PARALLEL_TOOL_PREFIX",
            ),
        )


@dataclass(frozen=True)
class MetricsSnapshot:
    window_start: datetime
    window_end: datetime
    request_count: int
    completed_request_count: int
    p95_latency_ms: float | None
    request_error_count: int
    request_error_rate: float
    tool_call_count: int
    tool_failure_count: int
    tool_failure_rate: float
    llm_fallback_count: int
    llm_fallback_rate: float


@dataclass(frozen=True)
class Alert:
    name: str
    value: float
    threshold: float
    unit: str


@dataclass(frozen=True)
class CycleResult:
    snapshot: MetricsSnapshot
    alerts: list[Alert]
    new_alerts: list[Alert]
    resolved_alerts: list[str]


@dataclass
class _TraceAggregate:
    root_start: datetime | None = None
    root_end: datetime | None = None
    explicit_request_latency_ms: float | None = None
    recorded_step_latency_ms: float = 0.0
    parallel_step_latency_ms: float = 0.0
    has_recorded_step_latency: bool = False
    request_error: bool = False
    fallback: bool = False


class UrllibJsonTransport:
    """JSON-over-HTTP transport that never includes response bodies in errors."""

    def get_json(self, url: str, *, headers: dict[str, str], timeout: float) -> dict:
        request = Request(url, headers=headers, method="GET")
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is validated config
                body = response.read()
        except HTTPError as exc:
            raise QueryError(
                f"Langfuse observations query failed (HTTP {exc.code})"
            ) from None
        except (URLError, TimeoutError, OSError):
            raise QueryError(
                "Langfuse observations query failed (network error)"
            ) from None
        return _decode_json_object(
            body, QueryError, "Langfuse observations response was not valid JSON"
        )

    def post_json(self, url: str, payload: dict, *, timeout: float) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is validated config
                response.read()
        except HTTPError as exc:
            raise NotificationError(
                f"alert webhook rejected delivery (HTTP {exc.code})"
            ) from None
        except (URLError, TimeoutError, OSError):
            raise NotificationError(
                "alert webhook delivery failed (network error)"
            ) from None


class LangfuseClient:
    """Read Observations API v2 with bounded windows and cursor pagination."""

    def __init__(self, config: Config, *, transport: JsonTransport | None = None):
        self._config = config
        self._transport = transport or UrllibJsonTransport()
        credential = (
            f"{config.langfuse_public_key}:{config.langfuse_secret_key}".encode("utf-8")
        )
        self._headers = {
            "Accept": "application/json",
            "Authorization": "Basic " + base64.b64encode(credential).decode("ascii"),
            "User-Agent": "clinical-copilot-alert-checker/1",
        }
        self._endpoint = (
            config.langfuse_base_url.rstrip("/") + "/api/public/v2/observations"
        )

    def fetch_observations(self, start: datetime, end: datetime) -> list[dict]:
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise QueryError("invalid observation query window")

        observations: list[dict] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _page in range(self._config.max_pages):
            params = {
                "fields": _OBSERVATION_FIELDS,
                "fromStartTime": _format_timestamp(start),
                "toStartTime": _format_timestamp(end),
                "limit": str(self._config.page_limit),
            }
            if cursor is not None:
                params["cursor"] = cursor
            url = self._endpoint + "?" + urlencode(params)
            try:
                payload = self._transport.get_json(
                    url,
                    headers=dict(self._headers),
                    timeout=self._config.http_timeout_seconds,
                )
            except QueryError:
                raise
            except Exception:
                raise QueryError("Langfuse observations query failed") from None

            data, cursor = _parse_observations_page(payload)
            observations.extend(data)
            if cursor is None:
                return observations
            if cursor in seen_cursors:
                raise QueryError("Langfuse observations pagination repeated a cursor")
            seen_cursors.add(cursor)

        raise QueryError(
            "Langfuse observations query exceeded the configured page bound"
        )


class WebhookNotifier:
    """Send a Slack-compatible aggregate JSON payload to a configured webhook."""

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float,
        transport: JsonTransport | None = None,
    ):
        self._url = url
        self._timeout_seconds = timeout_seconds
        self._transport = transport or UrllibJsonTransport()

    def notify(self, alerts: list[Alert], snapshot: MetricsSnapshot) -> None:
        payload = _webhook_payload(alerts, snapshot)
        try:
            self._transport.post_json(self._url, payload, timeout=self._timeout_seconds)
        except NotificationError:
            raise
        except Exception:
            raise NotificationError("alert webhook delivery failed") from None


class DryRunNotifier:
    """Explicit no-delivery mode for configuration checks and local demos."""

    def notify(self, alerts: list[Alert], snapshot: MetricsSnapshot) -> None:
        return None


class AlertChecker:
    """Evaluate one sliding window and suppress duplicate notifications while firing."""

    def __init__(
        self, config: Config, *, client: ObservationClient, notifier: Notifier
    ):
        self._config = config
        self._client = client
        self._notifier = notifier
        self._active_alerts: set[str] = set()

    def run_once(self, now: datetime | None = None) -> CycleResult:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise QueryError("checker clock must be timezone-aware")
        window_end = current.astimezone(timezone.utc) - timedelta(
            seconds=self._config.settle_delay_seconds
        )
        window_start = window_end - timedelta(minutes=self._config.window_minutes)
        observations = self._client.fetch_observations(window_start, window_end)
        snapshot = aggregate_observations(
            observations,
            trace_name=self._config.trace_name,
            tool_prefix=self._config.tool_prefix,
            parallel_tool_prefix=self._config.parallel_tool_prefix,
            window_start=window_start,
            window_end=window_end,
        )
        alerts = evaluate(snapshot, self._config.thresholds)
        current_names = {alert.name for alert in alerts}
        new_alerts = [
            alert for alert in alerts if alert.name not in self._active_alerts
        ]
        resolved = sorted(self._active_alerts - current_names)
        if new_alerts:
            self._notifier.notify(new_alerts, snapshot)
        self._active_alerts = current_names
        return CycleResult(snapshot, alerts, new_alerts, resolved)


def aggregate_observations(
    observations: Sequence[dict],
    *,
    trace_name: str,
    tool_prefix: str,
    parallel_tool_prefix: str = "fhir.",
    window_start: datetime,
    window_end: datetime,
) -> MetricsSnapshot:
    """Build aggregates without returning or emitting source identifiers/payloads."""

    traces: dict[str, _TraceAggregate] = {}
    seen_observations: set[str] = set()
    tool_call_count = 0
    tool_failure_count = 0
    tool_prefixes = _split_prefixes(tool_prefix)

    for observation in observations:
        if not isinstance(observation, dict):
            raise QueryError("Langfuse observation row was not an object")
        if observation.get("traceName") != trace_name:
            continue
        observation_id = observation.get("id")
        trace_id = observation.get("traceId")
        if not isinstance(observation_id, str) or not observation_id:
            raise QueryError("Langfuse observation row omitted id")
        if observation_id in seen_observations:
            continue
        seen_observations.add(observation_id)
        if not isinstance(trace_id, str) or not trace_id:
            raise QueryError("Langfuse observation row omitted traceId")

        trace = traces.setdefault(trace_id, _TraceAggregate())
        tags = _string_set(observation.get("tags"))
        metadata = observation.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        trace.fallback = trace.fallback or _has_fallback(tags, metadata)
        trace.request_error = trace.request_error or "request:error" in tags

        name = observation.get("name")
        is_tool = isinstance(name, str) and name.startswith(tool_prefixes)
        failed = _is_error_observation(observation, metadata)
        if is_tool:
            tool_call_count += 1
            tool_failure_count += int(failed)

        parent_id = observation.get("parentObservationId")
        if parent_id in (None, ""):
            trace.request_error = trace.request_error or failed
            explicit_latency = _nonnegative_number(metadata.get("request_latency_ms"))
            if explicit_latency is not None:
                trace.explicit_request_latency_ms = explicit_latency
            start = _parse_timestamp(observation.get("startTime"), "startTime")
            end = _parse_optional_timestamp(observation.get("endTime"), "endTime")
            if trace.root_start is None or start < trace.root_start:
                trace.root_start = start
            if end is not None and (trace.root_end is None or end > trace.root_end):
                trace.root_end = end
        else:
            step_latency = _nonnegative_number(metadata.get("latency_ms"))
            if step_latency is not None:
                if isinstance(name, str) and name.startswith(parallel_tool_prefix):
                    trace.parallel_step_latency_ms = max(
                        trace.parallel_step_latency_ms, step_latency
                    )
                else:
                    trace.recorded_step_latency_ms += step_latency
                trace.has_recorded_step_latency = True

    latencies_ms = sorted(
        latency
        for trace in traces.values()
        if (latency := _trace_latency_ms(trace)) is not None
    )
    request_count = len(traces)
    request_error_count = sum(trace.request_error for trace in traces.values())
    fallback_count = sum(trace.fallback for trace in traces.values())
    return MetricsSnapshot(
        window_start=window_start,
        window_end=window_end,
        request_count=request_count,
        completed_request_count=len(latencies_ms),
        p95_latency_ms=_percentile_nearest_rank(latencies_ms, 0.95),
        request_error_count=request_error_count,
        request_error_rate=_rate(request_error_count, request_count),
        tool_call_count=tool_call_count,
        tool_failure_count=tool_failure_count,
        tool_failure_rate=_rate(tool_failure_count, tool_call_count),
        llm_fallback_count=fallback_count,
        llm_fallback_rate=_rate(fallback_count, request_count),
    )


def evaluate(snapshot: MetricsSnapshot, thresholds: Thresholds) -> list[Alert]:
    """Return firing alerts in stable runbook order; thresholds are strict ``>``."""

    alerts: list[Alert] = []
    if (
        snapshot.p95_latency_ms is not None
        and snapshot.p95_latency_ms > thresholds.p95_latency_ms
    ):
        alerts.append(
            Alert(
                "p95_latency", snapshot.p95_latency_ms, thresholds.p95_latency_ms, "ms"
            )
        )
    _append_rate_alert(
        alerts,
        "request_error_rate",
        snapshot.request_error_rate,
        thresholds.request_error_rate,
        snapshot.request_count,
    )
    _append_rate_alert(
        alerts,
        "tool_failure_rate",
        snapshot.tool_failure_rate,
        thresholds.tool_failure_rate,
        snapshot.tool_call_count,
    )
    _append_rate_alert(
        alerts,
        "llm_fallback_rate",
        snapshot.llm_fallback_rate,
        thresholds.llm_fallback_rate,
        snapshot.request_count,
    )
    return alerts


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    transport: JsonTransport | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    parser = argparse.ArgumentParser(
        description="Clinical Co-Pilot Langfuse alert checker"
    )
    parser.add_argument(
        "--once", action="store_true", help="evaluate one window and exit"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        config = Config.from_environ(environ)
    except ConfigError as exc:
        _write_log(err, "configuration_error", message=str(exc))
        return 2

    http = transport or UrllibJsonTransport()
    client = LangfuseClient(config, transport=http)
    notifier: Notifier
    if config.dry_run:
        notifier = DryRunNotifier()
    else:
        assert config.webhook_url is not None  # guaranteed by Config
        notifier = WebhookNotifier(
            config.webhook_url,
            timeout_seconds=config.http_timeout_seconds,
            transport=http,
        )
    checker = AlertChecker(config, client=client, notifier=notifier)

    while True:
        try:
            result = checker.run_once()
        except QueryError:
            _write_log(err, "query_error", message="Langfuse observations query failed")
            return 3
        except NotificationError:
            _write_log(err, "notification_error", message="alert delivery failed")
            return 4
        except Exception:
            _write_log(err, "checker_error", message="unexpected checker failure")
            return 5

        _write_cycle_log(out, result, dry_run=config.dry_run)
        if args.once:
            return 0
        sleeper(config.interval_seconds)


def _parse_observations_page(payload: dict) -> tuple[list[dict], str | None]:
    if not isinstance(payload, dict):
        raise QueryError("Langfuse observations response was not an object")
    data = payload.get("data")
    meta = payload.get("meta")
    if not isinstance(data, list) or not isinstance(meta, dict):
        raise QueryError("Langfuse observations response omitted data/meta")
    if any(not isinstance(row, dict) for row in data):
        raise QueryError("Langfuse observations response contained a non-object row")
    cursor = meta.get("cursor")
    if cursor in (None, ""):
        return data, None
    if not isinstance(cursor, str):
        raise QueryError("Langfuse observations cursor was not a string")
    return data, cursor


def _is_error_observation(observation: dict, metadata: dict) -> bool:
    level = str(observation.get("level") or "").strip().lower()
    status = str(metadata.get("status") or "").strip().lower()
    if level == "error" or status in _ERROR_STATES:
        return True
    if metadata.get("error") is True or metadata.get("is_error") is True:
        return True
    for key in ("http_status", "status_code"):
        value = metadata.get(key)
        if isinstance(value, int) and value >= 500:
            return True
    return False


def _trace_latency_ms(trace: _TraceAggregate) -> float | None:
    if trace.explicit_request_latency_ms is not None:
        return trace.explicit_request_latency_ms
    if trace.has_recorded_step_latency:
        return trace.recorded_step_latency_ms + trace.parallel_step_latency_ms
    if (
        trace.root_start is not None
        and trace.root_end is not None
        and trace.root_end >= trace.root_start
    ):
        return (trace.root_end - trace.root_start).total_seconds() * 1_000
    return None


def _nonnegative_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _has_fallback(tags: set[str], metadata: dict) -> bool:
    for tag in tags:
        if tag.startswith("fallback:"):
            return tag.partition(":")[2].strip().lower() not in _INACTIVE_FALLBACKS
    kind = str(metadata.get("fallback_kind") or "").strip().lower()
    return kind not in _INACTIVE_FALLBACKS


def _append_rate_alert(
    alerts: list[Alert], name: str, value: float, threshold: float, denominator: int
) -> None:
    if denominator > 0 and value > threshold:
        alerts.append(Alert(name, value, threshold, "ratio"))


def _webhook_payload(alerts: list[Alert], snapshot: MetricsSnapshot) -> dict:
    alert_rows = [
        {
            "name": alert.name,
            "value": round(alert.value, 6),
            "threshold": round(alert.threshold, 6),
            "unit": alert.unit,
        }
        for alert in alerts
    ]
    summary = "; ".join(
        f"{alert.name}={alert.value:.4g}{alert.unit}>{alert.threshold:.4g}{alert.unit}"
        for alert in alerts
    )
    return {
        "text": f"Clinical Co-Pilot alert: {summary}",
        "source": "clinical-copilot-alert-checker",
        "window": {
            "from": _format_timestamp(snapshot.window_start),
            "to": _format_timestamp(snapshot.window_end),
        },
        "metrics": {
            "requests": snapshot.request_count,
            "completed_requests": snapshot.completed_request_count,
            "p95_latency_ms": snapshot.p95_latency_ms,
            "request_errors": snapshot.request_error_count,
            "request_error_rate": snapshot.request_error_rate,
            "tool_calls": snapshot.tool_call_count,
            "tool_failures": snapshot.tool_failure_count,
            "tool_failure_rate": snapshot.tool_failure_rate,
            "llm_fallbacks": snapshot.llm_fallback_count,
            "llm_fallback_rate": snapshot.llm_fallback_rate,
        },
        "alerts": alert_rows,
    }


def _write_cycle_log(stream: TextIO, result: CycleResult, *, dry_run: bool) -> None:
    snapshot = result.snapshot
    _write_log(
        stream,
        "window_evaluated",
        requests=snapshot.request_count,
        completed_requests=snapshot.completed_request_count,
        p95_latency_ms=snapshot.p95_latency_ms,
        request_error_rate=snapshot.request_error_rate,
        tool_failure_rate=snapshot.tool_failure_rate,
        llm_fallback_rate=snapshot.llm_fallback_rate,
        firing=[alert.name for alert in result.alerts],
        notified=[alert.name for alert in result.new_alerts],
        resolved=result.resolved_alerts,
        dry_run=dry_run,
    )


def _write_log(stream: TextIO, event: str, **fields: object) -> None:
    record = {"event": event, **fields}
    stream.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
    stream.flush()


def _decode_json_object(
    body: bytes, error_type: type[RuntimeError], message: str
) -> dict:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise error_type(message) from None
    if not isinstance(value, dict):
        raise error_type(message)
    return value


def _parse_timestamp(value: object, field_name: str) -> datetime:
    parsed = _parse_optional_timestamp(value, field_name)
    if parsed is None:
        raise QueryError(f"Langfuse observation omitted {field_name}")
    return parsed


def _parse_optional_timestamp(value: object, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise QueryError(f"Langfuse observation {field_name} was not a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise QueryError(f"Langfuse observation {field_name} was invalid") from None
    if parsed.tzinfo is None:
        raise QueryError(f"Langfuse observation {field_name} omitted timezone")
    return parsed.astimezone(timezone.utc)


def _percentile_nearest_rank(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    index = max(0, math.ceil(quantile * len(values)) - 1)
    return values[index]


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _string_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if value is None or not value.strip():
        raise ConfigError(f"{name} is required")
    return value.strip()


def _required_any(env: Mapping[str, str], primary: str, fallback: str) -> str:
    value = env.get(primary) or env.get(fallback)
    if value is None or not value.strip():
        raise ConfigError(f"{primary} (or {fallback}) is required")
    return value.strip()


def _optional(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    return value.strip() if value is not None and value.strip() else None


def _prefix_csv(value: str, name: str) -> str:
    prefixes = tuple(part.strip() for part in value.split(",") if part.strip())
    if not prefixes:
        raise ConfigError(f"{name} must contain at least one prefix")
    return ",".join(prefixes)


def _split_prefixes(value: str) -> tuple[str, ...]:
    prefixes = tuple(part.strip() for part in value.split(",") if part.strip())
    if not prefixes:
        raise QueryError("tool prefix configuration was empty")
    return prefixes


def _nonempty(value: str, name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ConfigError(f"{name} must not be empty")
    return stripped


def _parse_bool(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false")


def _parse_float(
    env: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float,
    exclusive: bool,
) -> float:
    raw = env.get(name)
    try:
        value = default if raw is None else float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be numeric") from None
    if not math.isfinite(value):
        raise ConfigError(f"{name} must be finite")
    if (exclusive and value <= minimum) or (not exclusive and value < minimum):
        comparator = "greater than" if exclusive else "at least"
        raise ConfigError(f"{name} must be {comparator} {minimum}")
    return value


def _parse_rate(env: Mapping[str, str], name: str, default: float) -> float:
    value = _parse_float(env, name, default, minimum=0.0, exclusive=False)
    if value > 1.0:
        raise ConfigError(f"{name} must be between 0 and 1")
    return value


def _parse_int(env: Mapping[str, str], name: str, default: int, *, minimum: int) -> int:
    raw = env.get(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer") from None
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}")
    return value


def _validate_http_url(value: str, name: str, *, allow_path: bool) -> str:
    parsed = urlsplit(value)
    if not parsed.hostname or parsed.scheme not in {"http", "https"}:
        raise ConfigError(f"{name} must be an HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigError(f"{name} must not contain embedded credentials")
    if parsed.query or parsed.fragment:
        raise ConfigError(f"{name} must not contain a query or fragment")
    if not allow_path and parsed.path not in {"", "/"}:
        raise ConfigError(f"{name} must be the Langfuse origin without an API path")
    if parsed.scheme != "https" and parsed.hostname not in _LOCAL_HOSTS:
        raise ConfigError(f"{name} must use HTTPS except for localhost")
    return value.rstrip("/")


if __name__ == "__main__":
    raise SystemExit(main())
