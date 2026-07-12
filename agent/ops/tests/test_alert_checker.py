from __future__ import annotations

import base64
import io
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from ops import alert_checker as ac


def _env(**overrides: str) -> dict[str, str]:
    values = {
        "LANGFUSE_BASE_URL": "https://hipaa.cloud.langfuse.com",
        "LANGFUSE_PUBLIC_KEY": "pk-test",
        "LANGFUSE_SECRET_KEY": "sk-test",
        "COPILOT_ALERT_DRY_RUN": "true",
    }
    values.update(overrides)
    return values


def _observation(
    *,
    observation_id: str,
    trace_id: str,
    start: str,
    end: str,
    name: str,
    parent_id: str | None,
    level: str = "DEFAULT",
    metadata: dict | None = None,
    tags: list[str] | None = None,
    trace_name: str = "previsit-brief",
) -> dict:
    return {
        "id": observation_id,
        "traceId": trace_id,
        "startTime": start,
        "endTime": end,
        "parentObservationId": parent_id,
        "type": "SPAN",
        "name": name,
        "level": level,
        "metadata": metadata or {},
        "tags": tags or [],
        "traceName": trace_name,
    }


def _breaching_observations() -> list[dict]:
    return [
        _observation(
            observation_id="root-1",
            trace_id="trace-1",
            start="2026-07-12T12:00:00Z",
            end="2026-07-12T12:00:10Z",
            name="request",
            parent_id=None,
            tags=["fallback:none"],
        ),
        _observation(
            observation_id="tool-1",
            trace_id="trace-1",
            start="2026-07-12T12:00:01Z",
            end="2026-07-12T12:00:02Z",
            name="tool.get_conditions",
            parent_id="root-1",
            metadata={"status": "ok"},
            tags=["fallback:none"],
        ),
        _observation(
            observation_id="root-2",
            trace_id="trace-2",
            start="2026-07-12T12:00:00Z",
            end="2026-07-12T12:00:20Z",
            name="request",
            parent_id=None,
            level="ERROR",
            metadata={"status": "error"},
            tags=["fallback:transient", "request:error"],
        ),
        _observation(
            observation_id="tool-2",
            trace_id="trace-2",
            start="2026-07-12T12:00:01Z",
            end="2026-07-12T12:00:03Z",
            name="tool.get_recent_labs",
            parent_id="root-2",
            level="ERROR",
            metadata={"status": "error"},
            tags=["fallback:transient"],
        ),
        _observation(
            observation_id="ignored",
            trace_id="other-trace",
            start="2026-07-12T12:00:00Z",
            end="2026-07-12T12:01:00Z",
            name="request",
            parent_id=None,
            level="ERROR",
            trace_name="unrelated-app",
        ),
    ]


class FakeTransport:
    def __init__(self, pages: list[dict] | None = None, error: Exception | None = None):
        self.pages = list(pages or [])
        self.error = error
        self.get_calls: list[tuple[str, dict[str, str], float]] = []
        self.post_calls: list[tuple[str, dict, float]] = []

    def get_json(self, url: str, *, headers: dict[str, str], timeout: float) -> dict:
        self.get_calls.append((url, headers, timeout))
        if self.error is not None:
            raise self.error
        return self.pages.pop(0)

    def post_json(self, url: str, payload: dict, *, timeout: float) -> None:
        self.post_calls.append((url, payload, timeout))


class FakeClient:
    def __init__(self, observations: list[dict]):
        self.observations = observations
        self.windows: list[tuple[datetime, datetime]] = []

    def fetch_observations(self, start: datetime, end: datetime) -> list[dict]:
        self.windows.append((start, end))
        return list(self.observations)


class RecordingNotifier:
    def __init__(self):
        self.calls: list[tuple[list[ac.Alert], ac.MetricsSnapshot]] = []

    def notify(self, alerts: list[ac.Alert], snapshot: ac.MetricsSnapshot) -> None:
        self.calls.append((list(alerts), snapshot))


def test_config_requires_credentials_and_a_delivery_mode() -> None:
    with pytest.raises(ac.ConfigError, match="LANGFUSE_SECRET_KEY"):
        ac.Config.from_environ(
            {
                "LANGFUSE_BASE_URL": "https://hipaa.cloud.langfuse.com",
                "LANGFUSE_PUBLIC_KEY": "pk-test",
                "COPILOT_ALERT_DRY_RUN": "true",
            }
        )

    with pytest.raises(ac.ConfigError, match="COPILOT_ALERT_WEBHOOK_URL"):
        ac.Config.from_environ(
            {
                "LANGFUSE_BASE_URL": "https://hipaa.cloud.langfuse.com",
                "LANGFUSE_PUBLIC_KEY": "pk-test",
                "LANGFUSE_SECRET_KEY": "sk-test",
            }
        )


def test_config_parses_thresholds_and_rejects_remote_plaintext() -> None:
    config = ac.Config.from_environ(
        _env(
            COPILOT_ALERT_P95_LATENCY_MS="12000",
            COPILOT_ALERT_REQUEST_ERROR_RATE="0.07",
            COPILOT_ALERT_TOOL_FAILURE_RATE="0.15",
            COPILOT_ALERT_LLM_FALLBACK_RATE="0.20",
            COPILOT_ALERT_WINDOW_MINUTES="20",
            COPILOT_ALERT_SETTLE_DELAY_SECONDS="300",
        )
    )

    assert config.thresholds == ac.Thresholds(12000.0, 0.07, 0.15, 0.20)
    assert config.window_minutes == 20
    assert config.settle_delay_seconds == 300
    assert config.tool_prefix == "fhir.,tool."
    assert config.parallel_tool_prefix == "fhir."

    with pytest.raises(ac.ConfigError, match="HTTPS"):
        ac.Config.from_environ(_env(LANGFUSE_BASE_URL="http://langfuse.example"))


def test_langfuse_v2_query_uses_basic_auth_bounded_fields_and_cursor_pagination() -> (
    None
):
    config = ac.Config.from_environ(_env(COPILOT_ALERT_PAGE_LIMIT="2"))
    transport = FakeTransport(
        pages=[
            {"data": [{"id": "one"}], "meta": {"cursor": "next-page"}},
            {"data": [{"id": "two"}], "meta": {"cursor": None}},
        ]
    )
    client = ac.LangfuseClient(config, transport=transport)
    start = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 12, 12, 15, tzinfo=timezone.utc)

    assert client.fetch_observations(start, end) == [{"id": "one"}, {"id": "two"}]
    assert len(transport.get_calls) == 2

    first_url, first_headers, _timeout = transport.get_calls[0]
    first = urlsplit(first_url)
    query = parse_qs(first.query)
    assert first.path == "/api/public/v2/observations"
    assert query["fields"] == ["core,basic,metadata,trace_context"]
    assert query["limit"] == ["2"]
    assert query["fromStartTime"] == ["2026-07-12T12:00:00Z"]
    assert query["toStartTime"] == ["2026-07-12T12:15:00Z"]
    assert "cursor" not in query
    assert "sk-test" not in first_url
    assert (
        base64.b64decode(first_headers["Authorization"].removeprefix("Basic ")).decode()
        == "pk-test:sk-test"
    )

    second_query = parse_qs(urlsplit(transport.get_calls[1][0]).query)
    assert second_query["cursor"] == ["next-page"]


def test_aggregation_and_threshold_evaluation_cover_all_four_required_alerts() -> None:
    start = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 12, 12, 15, tzinfo=timezone.utc)
    snapshot = ac.aggregate_observations(
        _breaching_observations(),
        trace_name="previsit-brief",
        tool_prefix="tool.",
        window_start=start,
        window_end=end,
    )

    assert snapshot.request_count == 2
    assert snapshot.completed_request_count == 2
    assert snapshot.p95_latency_ms == 20000.0
    assert snapshot.request_error_count == 1
    assert snapshot.request_error_rate == 0.5
    assert snapshot.tool_call_count == 2
    assert snapshot.tool_failure_count == 1
    assert snapshot.tool_failure_rate == 0.5
    assert snapshot.llm_fallback_count == 1
    assert snapshot.llm_fallback_rate == 0.5

    alerts = ac.evaluate(snapshot, ac.Thresholds(15000.0, 0.05, 0.10, 0.10))
    assert [alert.name for alert in alerts] == [
        "p95_latency",
        "request_error_rate",
        "tool_failure_rate",
        "llm_fallback_rate",
    ]


def test_recorded_step_latency_precedes_export_span_wall_time() -> None:
    observations = [
        _observation(
            observation_id="root",
            trace_id="trace-1",
            start="2026-07-12T12:00:00Z",
            end="2026-07-12T12:00:00.010Z",
            name="request",
            parent_id=None,
        ),
        _observation(
            observation_id="llm-step",
            trace_id="trace-1",
            start="2026-07-12T12:00:00.001Z",
            end="2026-07-12T12:00:00.002Z",
            name="llm.complete",
            parent_id="root",
            metadata={"latency_ms": 20_000},
        ),
    ]
    snapshot = ac.aggregate_observations(
        observations,
        trace_name="previsit-brief",
        tool_prefix="tool.",
        window_start=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 7, 12, 12, 15, tzinfo=timezone.utc),
    )

    # The current sink exports spans after the request and records real step duration in
    # metadata; the 10 ms export span must not hide the 20 s request step.
    assert snapshot.p95_latency_ms == 20_000.0


def test_parallel_fhir_spans_use_critical_path_and_count_as_tools() -> None:
    observations = [
        _observation(
            observation_id="root",
            trace_id="trace-1",
            start="2026-07-12T12:00:00Z",
            end="2026-07-12T12:00:00.010Z",
            name="request",
            parent_id=None,
        ),
        _observation(
            observation_id="fhir-conditions",
            trace_id="trace-1",
            start="2026-07-12T12:00:00.001Z",
            end="2026-07-12T12:00:00.002Z",
            name="fhir.get_conditions",
            parent_id="root",
            metadata={"latency_ms": 1_000, "status": "ok"},
        ),
        _observation(
            observation_id="fhir-labs",
            trace_id="trace-1",
            start="2026-07-12T12:00:00.001Z",
            end="2026-07-12T12:00:00.003Z",
            name="fhir.get_recent_labs",
            parent_id="root",
            metadata={"latency_ms": 2_000, "status": "failed"},
        ),
        _observation(
            observation_id="llm-step",
            trace_id="trace-1",
            start="2026-07-12T12:00:00.004Z",
            end="2026-07-12T12:00:00.005Z",
            name="llm.complete",
            parent_id="root",
            metadata={"latency_ms": 3_000},
        ),
    ]

    snapshot = ac.aggregate_observations(
        observations,
        trace_name="previsit-brief",
        tool_prefix="fhir.,tool.",
        parallel_tool_prefix="fhir.",
        window_start=datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 7, 12, 12, 15, tzinfo=timezone.utc),
    )

    assert snapshot.p95_latency_ms == 5_000.0
    assert snapshot.tool_call_count == 2
    assert snapshot.tool_failure_count == 1


def test_checker_notifies_once_per_continuous_breach_and_rearms_after_resolution() -> (
    None
):
    config = ac.Config.from_environ(_env(COPILOT_ALERT_SETTLE_DELAY_SECONDS="0"))
    client = FakeClient(_breaching_observations())
    notifier = RecordingNotifier()
    checker = ac.AlertChecker(config, client=client, notifier=notifier)
    now = datetime(2026, 7, 12, 12, 15, tzinfo=timezone.utc)

    first = checker.run_once(now)
    second = checker.run_once(now)
    assert len(first.new_alerts) == 4
    assert second.new_alerts == []
    assert len(notifier.calls) == 1

    client.observations = []
    checker.run_once(now)
    client.observations = _breaching_observations()
    checker.run_once(now)
    assert len(notifier.calls) == 2


def test_webhook_payload_contains_only_aggregate_sanitized_data() -> None:
    transport = FakeTransport()
    notifier = ac.WebhookNotifier(
        "https://hooks.example/secret-path-token",
        timeout_seconds=4.0,
        transport=transport,
    )
    start = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    end = datetime(2026, 7, 12, 12, 15, tzinfo=timezone.utc)
    snapshot = ac.aggregate_observations(
        _breaching_observations(),
        trace_name="previsit-brief",
        tool_prefix="tool.",
        window_start=start,
        window_end=end,
    )
    alerts = ac.evaluate(snapshot, ac.Thresholds(15000.0, 0.05, 0.10, 0.10))

    notifier.notify(alerts, snapshot)

    assert len(transport.post_calls) == 1
    _url, payload, timeout = transport.post_calls[0]
    serialized = json.dumps(payload)
    assert timeout == 4.0
    assert set(payload) == {"text", "source", "window", "metrics", "alerts"}
    assert "trace-1" not in serialized
    assert "trace-2" not in serialized
    assert "secret-path-token" not in serialized
    assert "pk-test" not in serialized
    assert "sk-test" not in serialized


def test_cli_returns_nonzero_and_never_logs_secret_on_config_or_query_failure() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    missing_secret = _env()
    missing_secret.pop("LANGFUSE_SECRET_KEY")

    assert (
        ac.main(["--once"], environ=missing_secret, stdout=stdout, stderr=stderr) == 2
    )
    assert "pk-test" not in stderr.getvalue()

    stdout = io.StringIO()
    stderr = io.StringIO()
    transport = FakeTransport(
        error=RuntimeError("upstream echoed sk-test and patient-123")
    )
    assert (
        ac.main(
            ["--once"],
            environ=_env(COPILOT_ALERT_SETTLE_DELAY_SECONDS="0"),
            stdout=stdout,
            stderr=stderr,
            transport=transport,
        )
        == 3
    )
    assert "sk-test" not in stderr.getvalue()
    assert "patient-123" not in stderr.getvalue()
